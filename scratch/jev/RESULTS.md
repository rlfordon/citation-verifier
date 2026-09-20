# Jev experiments — results (2026-09-19)

Model `jev-1.13.0`. Data: the three frozen corpora (90 of 95 claims have an
opinion file and a human label: 60 green, 30 yellow/red) plus one held-out prior
run, `matters/payne` (82 claims, 75 assessed by Opus). All responses are cached
in `cache.json`; rerunning any script replays them for free. Designs: `README.md`.

**Total spent on every experiment below: about $0.08.**

## What Jev costs

| step | per claim | per 30-claim brief |
|---|---|---|
| rubric on whole opinion (7 questions) | 0.19 s, $0.00025 | ~6 s, $0.0075 |
| passage locator | 0.18 s, $0.00027 | ~5 s, $0.0081 |
| rubric on located passages | 0.18 s, $0.0001 | ~5 s, $0.0029 |
| **locator + focused rubric** | **~0.37 s, ~$0.0004** | **~11 s, ~1 cent** |

Times are sequential, one request at a time; the rate limit (1,200 requests/min)
would allow running them concurrently. For comparison, the recorded Opus
assess-v2 cassettes (Agent SDK path) cost **$0.36–0.49 and 26–41 s per claim**.

## Test 3 — passage locator: works well

Reference = the passage holding the quote Opus chose for `opinion_block`
(52 claims have one that string-matches).

| | hit rate |
|---|---|
| top 1 | 40/52 (77%) |
| top 3 | 48/52 (92%) |
| top 5 | 50/52 (96%) |
| top 10 | 51/52 (98%) |

Top-5 passages plus one neighbour each side = **27% of the whole opinion's
size**. Far better than TypeSafe's own legal reranking cookbook (18% top-1);
their task was retrieval across cases, ours is finding a passage inside one
known opinion. The locator's "does any passage state this?" answer also
separates the labels on its own: mean 0.77 for green, 0.36 for yellow/red.

## Tests 1 / 1b / 2 — rubric and the auto-Green gate

Gate score = `min(P(relation = supports), states_it, same_issue)`. A claim is
eligible only if `cl_status == VERIFIED`, it has an opinion, its quote check is
`VERBATIM`/`NO_QUOTES`, and (where the column exists) it has no crosscheck flags.

- **Eligibility does real work.** The yellow claims that fooled Jev worst on the
  full pool (scores 0.93–0.98: withers-10, withers-37, payne-58, withers-45) all
  have quote problems, so the deterministic quote check removes them before Jev
  is asked. The two checks are complementary.
- **Located passages separate better than the whole opinion.** Same clearance,
  but the second-highest-scoring yellow drops from 0.74 to 0.47. This matches
  TypeSafe's documented "context rot".
- **Loose thresholds do not survive a held-out test.** In-sample, 0.50 looked
  safe (40/63 eligible cleared, only the disputed withers-32 wrong). On held-out
  `matters/payne`, 0.50 waved through **7 claims Opus reported as
  "partial"/Yellow**. Every one was an overstated holding — the failure mode
  predicted up front. Tuning on 95 claims overfits; trust only the held-out number.
- **The strict threshold held.**

| threshold | frozen corpora (95 claims) | held-out payne (82 claims) |
|---|---|---|
| 0.90 | 28 cleared, 1 bad (withers-32) | 14 cleared, 0 bad |
| **0.95** | **15 cleared (16%), 0 bad** | **14 cleared (17%), 0 bad** |
| 0.97 | 8 cleared, 0 bad | 6 cleared, 0 bad |

withers-32 is a disputed label: ground truth marks it "hedged — this is
debatable", and Opus rates it Green/Supported under both prompt versions.

Held-out payne, eligible claims by gate score vs what Opus reported:

| score | n | supported | partial | unsupported |
|---|---|---|---|---|
| 0.95–1.00 | 14 | 14 | 0 | 0 |
| 0.80–0.90 | 6 | 4 | 2 | 0 |
| 0.50–0.80 | 8 | 3 | 5 | 0 |
| below 0.50 | 21 | 0 | 7 | 14 |

Jev is reliable at both ends — every claim at 0.95+ was supported, no claim
under 0.50 was — and unreliable in the middle, which is where "partial" lives.

Clearance depends heavily on the brief (threshold 0.90, share of all claims):
wainwright (all-green brief) 47%, 13 of 28 Opus jobs skipped outright; payne
corpus 41%, 10 of 25 jobs; withers 3% (most of its claims carry quote problems
and are never eligible). Held-out payne: 17%, 7 of 44 jobs.

## Estimated savings

**Auto-Green gate at 0.95:** skips roughly **15–20% of Opus assess work on a
weak brief, up to ~45% on a clean one.** On the Agent SDK path (~$13 and ~17 min
per 30-claim brief, measured) that is ~$2–6 and ~3–8 minutes per brief. On the
direct-API path (est. $1–2.50 per brief) it is $0.20–1.10. Jev's own cost and
time are negligible either way. Cleared claims need no Opus prose: the Green
card can show the located passage.

**Passage locator feeding Opus (not yet tested end to end):** applies to *every*
claim, not just cleared ones. Opus input would shrink to ~27% of the opinion.
On the direct-API path, where input tokens are most of the bill, that is
potentially a larger saving than the gate. Risk: findings that depend on a topic
being *absent* from the whole opinion ("Case on unrelated subject"). Needs its
own test: re-run assess-v2 on located passages and score against the baselines.

## Caveats

- Small samples. Zero bad clears among 42 eligible yellow/red claims at risk
  (14 frozen + 28 held-out) does not certify a low error rate; it says the
  strict threshold is worth a larger test.
- The held-out reference is Opus's verdict, not a human label.
- `matters/payne` is only partly independent: it is a separate, larger
  extraction (82 claims) of the same brief the 27-claim payne corpus came from,
  so some propositions overlap. A brief Jev has never seen is the real test.
- The rubric wording was written once and not tuned. "Partial" detection is the
  obvious thing to improve (the `overstated` question helped on the held-out set:
  score >= 0.5 AND overstated < 0.4 cleared 10 with 0 bad — but that was read off
  the held-out data, so it needs a fresh check).
- The quote-matcher ceiling bug (`../TODO.md`) makes eligibility stricter than it
  needs to be: real verbatim quotes scored as CLOSE are excluded from the gate.
  Fixing the matcher would raise clearance.
- `typesafe-sdk` is installed in the venv but not added to `pyproject.toml`.

## Suggested next steps

1. Run Test 4 on more prior runs to firm up the 0.95 number (one command per
   workdir, about 3 cents each) — after confirming those briefs are fine to send
   to a third-party API.
2. Test the locator end to end: assess-v2 on located passages vs the recorded
   baselines. This is a new prompt version, so it means a re-record.
3. If both hold: build `JevExecutor` and the gate as a step between `triage`
   and `assess`, off by default.
