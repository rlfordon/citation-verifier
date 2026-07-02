# F2 — hybrid Sonnet/Opus model routing (design)

**Date:** 2026-07-01. **Parent:** `docs/plans/2026-07-01-pipeline-cost-audit.md`
finding **F2** ("wire `triage_track` to model routing — the built-but-dead
lever"). **Depends on:** the validated `MessagesAPIExecutor` (F1,
`docs/plans/2026-07-01-messages-api-executor-plan.md`) and the
`json_repair` parse fix (commit 8f97872).

## Problem

`run_triage` computes `triage_track` (`full` | `fast` | `''`) per claim
and writes it to claims.csv, but **nothing consumes it** — `run_assess`
sends every assessable claim to one model. The audit measured fast-track
at ~50–60% of assessable claims. F2 routes fast-track claims to Sonnet
(≈half the per-token cost) and escalates anything Sonnet doesn't confirm
to Opus, so cost drops without ever risking a wrong "verified".

**Safety basis (measured 2026-07-01, pinned `claude-sonnet-5`,
`sonnet-v1-api` arm):** 0 lenient-direction errors on the 60-case A/B set
— every Sonnet miss was over-flagging (the over-cautious direction).
Over-flagging only costs an extra Opus call under the escalation rule;
it never yields a wrong Green. Fast-track claims (clean-verified, no
quotes, no quote_floor, no crosscheck flags — see `_triage_track_for`)
are the lowest-risk supported population, where that property holds best.

## Enabling facts (verified in code, 2026-07-01)

1. **Mixed-model verdicts already coexist.** `run_apply_assessments`
   processes each verdict independently and records
   `assessed_by = "{model}/{prompt_version}"` per claim
   (`proposition_pipeline.py:1149`). Sonnet + Opus verdicts in one
   `assess_results.jsonl` (keyed by claim_id) need no special handling.
2. **A Sonnet "supported" → Green card renders no Sonnet prose.** The
   Green lane hard-codes the "Supported" badge and shows only
   proposition + flag chips (+ an optional `supporting_language` that v2
   does not populate); no agent blocks render (`:2082`). A Sonnet
   "supported" verdict is therefore safe to keep verbatim; every
   non-Green (finding) card is authored by Opus via escalation.
3. **No new prompt version.** `render_assess_v2_prompt` takes a *group*
   of claims; a group of one is a single-claim v2 job — same criteria as
   Opus, not the packed prompt that broke the earlier `sonnet-v2` arm.
   Verdicts stay homogeneous assess-v2; no cassette re-record.

## The verb

New orchestration function in `proposition_pipeline.py`, sibling to
`run_assess` (which is left unchanged for the single-model path):

```python
run_assess_hybrid(workdir, *, fast_executor, full_executor,
                  prompt_version="assess-v2") -> AssessStats
```

Job-building currently inline in `run_assess` (the v1 single-claim and v2
packed-per-opinion builders) is extracted into a shared module-level
helper so the two verbs cannot drift.

### Two-pass flow

1. **Pass 1 — fast-track via `fast_executor` (Sonnet).** Select claims
   with `triage_track == "fast"`, `_assessable`, and no persisted verdict
   for `prompt_version`. Build **single-claim v2 jobs** (group-of-one).
   Run through `fast_executor`.
2. **Partition the Pass-1 results.**
   - `support == "supported"` → **keep** (persist; becomes a Green card).
   - `support` in `{partial, unsupported, unverifiable}`, **or the job
     failed / produced no verdict / unparseable** → **escalate**.
3. **Pass 2 — full-track + escalated via `full_executor` (Opus).**
   Select `triage_track == "full"` claims (no persisted verdict) **plus**
   the escalated claim_ids. Build the existing **packed-per-opinion v2
   jobs**. Run through `full_executor`. Opus authors every non-Green card.

### Stats & cost accounting

`AssessStats` gains `fast_kept`, `escalated`, and `escalated_cost_usd`.
The escalation rate is the early-warning signal that Sonnet on
single-claim v2 over-flags more than the June data suggested, and the
discarded Sonnet verdicts take their `cost_usd` with them — the
cassette's cost sum would otherwise silently **understate** true spend by
the escalated claims' Sonnet cost (this repo's cassette cost sums get
quoted; keep them honest). All three fields are stamped into `run.json`
alongside the existing assess stats, and the CLI prints them.

### Persistence & resume

Only *final* verdicts are appended to `assess_results.jsonl`:
Sonnet-`supported` and all Opus verdicts. **Non-supported Sonnet verdicts
are held in memory and never persisted**, so an interrupted run re-runs
escalated claims (Sonnet again → Opus) — correct, with a mild re-cost on
resume only. Resume key is unchanged (`claim_id` + `prompt_version`); a
persisted verdict short-circuits both passes for that claim.

### Legacy / safety defaults

- Empty or missing `triage_track` (legacy workdirs, or triage not run) →
  the claim is treated as **full-track** (Opus). The verb never routes to
  Sonnet without an explicit `fast` track.
- Fast-track claims carry no `quote_floor` by construction, so the
  `run_apply_assessments` floor-enforcement never has to override a
  Sonnet Green.

## Surfaces

- **CLI** (`verify-propositions assess`): new `--route single|hybrid`
  (default `single`). Under `hybrid`, `fast_executor` is pinned to
  `claude-sonnet-5` and `full_executor` to `--model` (default opus), both
  on the `--executor` transport (`api` or `sdk`). `single` behaves exactly
  as today.
- **A/B harness** (`tools/ab_test_runner.py`): a config with
  `"route": "hybrid"` (+ `fast_model`, `full_model`) makes `run_ab_config`
  build two executors and call `run_assess_hybrid`. New config
  `hybrid-v2-api` (`fast_model: claude-sonnet-5`,
  `full_model: claude-opus-4-8`, `executor: api`, `prompt_version:
  assess-v2`). Scoring path unchanged (`score_workdir`, assess-v2).
  **The hybrid branch MUST run `run_triage(wd, prescreen=False)` on the
  run copy before assess** — verified 2026-07-01: all three frozen
  corpora **lack the `triage_track` column**, and `run_ab_config` never
  runs triage, so without this step the legacy-safety rule routes every
  claim to Opus and the arm "passes" while measuring nothing (the two
  safety features interact to defeat the test). The arm prints the
  fast/full mix per corpus and **hard-fails if the fast-track count is 0**
  — a vacuous run must be loud, never a trivial pass.
- **No default flips.** Hybrid is opt-in, like the API transport (F1
  Step 4 unchanged).

## Testing

- **Offline unit tests** (`tests/test_proposition_pipeline.py` or a new
  `tests/test_assess_hybrid.py`), fake/recorded executors, no API:
  - supported Pass-1 verdicts are kept and persisted;
  - non-supported Pass-1 verdicts escalate and are **not** persisted;
  - Pass-1 job failures / unparseable results escalate (not dropped);
  - full-track claims go only to `full_executor`;
  - escalated + full-track claims are packed per-opinion for Opus;
  - legacy empty `triage_track` → all claims routed to `full_executor`;
  - resume skips claims with a persisted verdict;
  - `fast_kept` / `escalated` / `escalated_cost_usd` stats are correct
    (escalated Sonnet cost captured even though the verdict is discarded).
- **Metered validation arm (gate before use):** `hybrid-v2-api` over
  withers + payne + wainwright, scored against a same-day Opus control.
  **Accept only if all hold:** (a) **0 lenient-direction errors on the
  A/B set** (hard fail otherwise); (b) reds **3/3**; (c) A/B **≥ 55/61**;
  (d) withers yellows **≥ 14** (the 2026-07-01 same-day Opus control
  floor). This gate exists because single-claim-v2-on-Sonnet is unmeasured
  (the 2026-07-01 arm was assess-v1). Sonnet over-flagging on single-claim
  v2 shows up only as more escalations (extra cost, acceptable); a lenient
  error is the hard fail. Also record the fast/full mix, the escalation
  rate, and total cost **including `escalated_cost_usd`** vs the Opus-only
  arm, to confirm the savings materialize net of discarded Sonnet calls.

## Files

- `src/citation_verifier/proposition_pipeline.py` — `run_assess_hybrid`,
  shared job-builder helper, partition/escalation logic.
- `src/citation_verifier/__main__.py` — `--route` flag + two-executor
  construction under hybrid.
- `tools/ab_test_runner.py` — hybrid dispatch in `run_ab_config` /
  `make_executor`; `tests/ab_test_configs.json` — `hybrid-v2-api`.
- Tests as above.
- CLAUDE.md (pipeline row) + CHANGELOG (additive, minor).

## Out of scope

- Flipping any CLI default (billing-gated, separate decision).
- Structured outputs for the verdict JSON (roadmap; see TODO).
- Prescreen deletion (F4 — independent cleanup).
- Deleting the triage verb (F2 supersedes the "delete it" alternative by
  giving `triage_track` a consumer).

## Validation outcome (2026-07-02) — gate FAILED, shelved opt-in

Ran the metered `hybrid-v2-api` arm over withers/payne/wainwright (Sonnet
`claude-sonnet-5` fast-track + Opus `claude-opus-4-8` escalation, API
transport). Score rows:
`scratch/ab_runs/ab_hybrid-v2-api_20260702-095708.jsonl`.

**Gate scorecard:**

| Criterion | Threshold | Result | |
|---|---|---|---|
| withers yellows | >= 14 | 16 | pass |
| reds (withers hallucinations) | 3/3 | 3/3 | pass |
| A/B accuracy (payne+wainwright) | >= 55/61 | 57/61 | pass |
| 0 lenient-direction errors on A/B set | 0 | 1 (payne-58) | **HARD FAIL** |

### Corrected diagnosis (2026-07-02, re-derived from the committed score rows)

The original write-up of this section (see git history of this file)
blamed the shelving on Sonnet fast-track false-negatives. **A claim-by-claim
diff of the hybrid run against the `opus-v2` baseline
(`scratch/ab_runs/ab_opus-v2_20260701-223625.jsonl`) contradicts that.**
Both are 95-row runs over the same corpora; the relevant tallies:

| set | pure-Opus (`opus-v2`) lenient-Green errors | hybrid lenient-Green errors |
|---|---|---|
| **A/B (payne+wainwright) — the gate's scope** | `{payne-58}` | `{payne-58}` |
| all corpora | `{payne-58, withers-12, -32, -33, -44, -49}` (6) | `{payne-58, withers-32, -33, -49}` (4) |

Three load-bearing facts:

1. **The gate-failing error is Opus, not routing.** payne-58 is predicted
   Green *identically by the pure-Opus baseline* — `opus-v2` lenient-on-A/B
   is also exactly `{payne-58}`. **The pure-Opus control fails this exact
   gate on the same run.** A gate the reference implementation cannot pass
   is measuring Opus run-to-run variance, not the change under test.

2. **The gate + run are jointly mis-specified.** The criterion is
   *absolute* ("0 lenient errors") but the design's control step says
   "scored against a same-day Opus control." The 2026-07-02 run **skipped
   the same-day control** and substituted model-attribution — so the one
   comparison that would have exposed the mis-spec (Opus's own nonzero
   lenient A/B rate) was never run. The gate should have been *relative*:
   `hybrid lenient <= same-day Opus-control lenient`.

3. **On the safety axis, hybrid beat pure Opus this run.** 4 lenient-Green
   errors vs 6; it *fixed* two of Opus's lenient calls (withers-12, -44).
   The withers-32/-49 keeps the original note called "fast-track
   false-negatives" are **Opus-inherent** — `opus-v2` greens the same
   claims — so they are not routing regressions.

**The one genuine hybrid-introduced error** is **payne-23 (Red -> Gray)**:
a verification-failure miss (didn't render), *not* a false clearance (not
Green), in the non-lenient direction. Not a safety failure; worth a look.

**The real weakness is cost, as the original note said.** Escalation ran
hot — withers 74% (14/19), payne 50% (6/12), wainwright 9.5% (2/21).
Hybrid ~$8.06/90 claims (incl. $1.53 discarded Sonnet) vs a rough all-Opus
~$9.8 → only **~18% savings** vs the 25–35% target. On high-escalation
corpora Opus does most of the work anyway; only low-escalation wainwright
delivers the promised savings.

### Disposition

Code is sound (fast pipeline fired, escalation invariant held, math
reconciles) and, on this run, **net-safer than the pure-Opus baseline**.
But it **cannot be validated under the current gate** (absolute-zero is
unachievable by Opus itself) and its **cost case is unproven-to-weak**.
`--route hybrid` stays **opt-in and unrecommended**; not shipped as a
default. The branch is kept, not deleted.

### Redesign brief (for whoever picks this up)

This is a validation-methodology + cost-structure redesign, **not a
threshold tune**. To make hybrid shippable:

1. **Make the gate relative.** Replace "0 lenient errors on A/B" with
   "hybrid lenient-Green errors on A/B `<=` same-day Opus-control lenient
   errors." Absolute-zero conflates a routing regression with Opus's own
   variance (this run proved Opus alone trips payne-58).
2. **Always run the same-day Opus control** in the same invocation and
   score the two side by side. Never attribute-by-model as a substitute.
3. **Attack the cost undershoot, which is structural.** Savings ∝ Sonnet
   `fast_kept`; each kept verdict is also an unescalatable false-Green
   surface — so cost and lenient-safety trade off directly. Lowering
   over-escalation (Sonnet's known over-flag mode; here it escalated 74%
   on withers) raises savings but needs a **single-claim-v2 Sonnet eval**
   to bound the lenient risk. That eval does not exist — single-claim v2
   on Sonnet is unmeasured (the June safety basis was assess-v1, packed).
   Build it before trusting a lower escalation rate.
4. **Investigate payne-23 (Red -> Gray)** — the lone hybrid-introduced
   regression.

Rebase the redesign onto the latest `main` (which will carry F3's
assess-v3 schema slim and F6's config/alias cleanup); the redesign rewrites
the A/B validation path anyway, so the rebase is cheap. Bring a fresh
`hybrid-*` A/B config — `main` intentionally does not carry `hybrid-v2-api`.
