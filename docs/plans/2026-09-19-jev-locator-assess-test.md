# Test plan: Jev passage locator feeding the LLM assessment

**Status:** not started. Written 2026-09-19 as a handoff for a fresh session.
**Decision already made by Rebecca:** run this test. It spends real Opus money
(estimate ~$5 on the direct-API path) and adds a prompt version — both approved.

## Read first (10 minutes)

- `scratch/jev/RESULTS.md` — what Jev costs, locator hit rates, gate results.
- `scratch/jev/LOOP.md` — phrasing loop, and the lesson that tuning on ~100
  claims overfits; held-out numbers are the only ones to trust.
- `src/citation_verifier/jev_shadow.py` — production copy of the locator
  (`split_passages`, `_locator_questions`, `_excerpt`). Reuse it; do not fork it.
- `CLAUDE.md` rows for `proposition_pipeline.py`, `executor.py`, `scoring.py`,
  `tools/ab_test_runner.py`, and `tests/test_assessment_regression.py`.

**Jev in one paragraph:** TypeSafe AI's `jev-1.13.0` is a classifier, not a text
generator. You send a `state` plus typed questions and get probabilities back in
~0.2 s for ~$0.0003. The locator tags an opinion's passages `P000…`, asks one
`Choice` over the IDs ("which passage best supports or addresses this
statement") plus one `Noul` ("does any passage state it"), and we keep the top 5
passages plus one neighbour each side. `TYPESAFE_API_KEY` is in `.env`;
`pip install -e ".[jev]"` is already done in this venv.

## The question

If Opus reads **only the located passages** instead of the whole opinion, does
it reach the same verdicts — and how much cheaper and faster is it?

What is already known: the locator puts the passage Opus itself quoted in its
top 5 for 50 of 52 claims (96%); top-5-plus-neighbours is 27% of the opinion's
size. What is NOT known: whether verdicts survive. The specific risk is
findings that depend on a topic being **absent** from the whole opinion ("Case
on unrelated subject", "unverifiable"): excerpts always contain *something*, and
a missed passage (4%) would read as "not supported".

## Design

1. **New prompt version `assess-v3`** — copy `src/citation_verifier/prompts/assess_v2.md`
   to `assess_v3.md`; never edit v2 (it is byte-pinned to recorded cassettes).
   Change only the framing: the agent is given *excerpts* of the opinion, the
   passages most relevant to each claim, with `[...]` marking omitted text; it
   must not infer anything from what is omitted; if the excerpts do not address
   a claim it answers `unverifiable` (NOT `unsupported`) — that is the signal to
   fall back to the full opinion. Keep every criterion, badge label, and output
   field identical to v2 so `scoring.derive_color` and the report work unchanged.
2. **Excerpt files.** Keep v2's per-opinion job packing (`run_assess`,
   `proposition_pipeline.py:1104`; `render_assess_v2_prompt` at `:845`). For each
   opinion, locate passages for every claim citing it and write the **union**
   (document order, `[...]` between gaps) to
   `<workdir>/jobs/excerpts/<opinion-stem>.txt`; the job's `files=[…]` points
   there instead of at the opinion. Log per claim which passage IDs were chosen
   and the `exists` value, so misses can be diagnosed.
3. **Run it live** on copies of the three frozen corpora via
   `tools/ab_test_runner.py` — add a config `opus-v3-api`
   (`executor: api`, `prompt_version: assess-v3`, `model: claude-opus-4-8`),
   modelled on the existing `opus-v2-api`. Verdicts land as a new cassette; do
   not overwrite the v1/v2 cassettes.
4. **Do not tune on the result.** One run, fixed design. If the prompt needs a
   second attempt, that is `assess-v4` and a new run, and say so in the write-up.

## What to measure

- **Agreement with the recorded v2 verdicts, claim by claim** — a 4x4 matrix of
  `support` (v2 full opinion vs v3 excerpts). This is the main result. List every
  disagreement with both `finding_analysis` texts and the locator's `exists`
  value and chosen passages.
- **Baselines** (`tests/test_assessment_regression.py`): v2 scores Withers
  16/19 yellows, 3/3 reds, 4 green over-flags; A/B (payne + wainwright) 55/61,
  lenient set {payne-03}. v3 must not get more lenient: **any new lenient error
  is the headline**, more important than the averages.
- **Where disagreements fall vs `exists`.** Hypothesis from held-out data: low
  `exists` predicts not-supported (on `matters/payne`, 0 of 21 claims scoring
  under 0.5 on the gate were supported). If the v3 misses cluster at low
  `exists`, the fallback rule is cheap: send the full opinion when `exists` is
  low or when v3 answers `unverifiable`/`unsupported`.
- **Cost and time:** `cost_usd` and `elapsed_s` per verdict from the API
  executor. For the v2 side, prefer an **offline estimate** from input-token
  counts over a second paid run (there is no API-path v2 cassette; check whether
  `opus-v2-api` was ever run before paying for it).
- **Net saving under each fallback rule:** none / fall back on `unverifiable` /
  fall back on `unverifiable`+`unsupported` / fall back when `exists` < 0.5.

## Deliverables

- `src/citation_verifier/prompts/assess_v3.md`, the excerpt-writing code, tests.
- The new cassette(s) under `tests/data/assessment_corpora/*/jobs/` (or a
  sibling path if mixing prompt versions in one file is awkward — check how
  `RecordedExecutor` keys on `prompt_version` first).
- `scratch/jev/LOCATOR_ASSESS.md` — results, the disagreement list, and a
  recommendation: adopt / adopt with fallback rule X / drop.
- Commit and push everything, including working data (repo convention).

## Out of scope

- Turning the auto-Green gate on (stays in shadow; trigger is in `scratch/jev/LOOP.md`).
- The quote-matcher ceiling bug (`scratch/TODO.md`, top item) — separate
  decision, still open. Note only that it makes gate eligibility stricter than
  it should be.
- Re-tuning the locator or the rubric wording. The lockbox is spent; further
  tuning needs fresh briefs.

## House rules that bit us today

- Windows: `venv/Scripts/python.exe`; no `head`/`tail`/`grep` in Git Bash; set
  `PYTHONIOENCODING=utf-8` when printing opinion text.
- `load_dotenv()` with no path asserts inside a heredoc — pass the path, or
  import `citation_verifier.client` (loads `.env` on import).
- Expand jargon for Rebecca on first use; no superpowers brainstorming ceremony —
  state assumptions and work.
