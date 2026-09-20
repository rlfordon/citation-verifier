# Locator -> LLM assessment: designed, costed, not run

**Date:** 2026-09-19. **Spend:** $0 (no Opus calls; every Jev answer came from
`cache.json`). **Plan:** `docs/plans/2026-09-19-jev-locator-assess-test.md`.
**Decision (Rebecca, 2026-09-19): do not run it. Parked on cost grounds.**

Jev = TypeSafe's cheap classifier. Its *locator* ranks an opinion's passages for
a claim and also answers "does any passage state this?" (the `exists` value, 0
to 1). The idea was to have Opus read only the located passages ("excerpts")
instead of the whole opinion, under a new prompt version, assess-v3.

## Why it was not run

The test was built right up to the paid step. Pricing it from data already in
hand showed that no result could justify adopting the design, so the run would
not have changed a decision.

Reproduce every number below, offline and free:
`venv/Scripts/python.exe scratch/jev/test5_locator_assess_estimate.py`

### 1. The most it could save is about a third, and that is 86 cents a brief

The like-for-like baseline is the 2026-07-01 run of assess-v2 through the direct
API (the "opus-v2-api" arm) over the same three frozen claim sets: **$7.15 for 90
claims = $0.079 a claim = $2.38 per 30-claim brief.**

Excerpts cut what Opus reads from about 743,000 tokens to about 228,000 (31%).
But input is only about **52%** of that bill; the rest is Opus's thinking and the
findings it writes, which excerpts do not shrink.

### 2. Any fallback rule eats most of it

Findings that rest on something being *absent* from the opinion cannot be made
from excerpts, so those claims have to go back to the full opinion. Estimated
cost of the 90 claims under each rule (an excerpt job is priced as its July cost
minus the input it no longer sends):

| Rule | Cost | Saving | Per 30-claim brief | Opinions read in full |
|---|---|---|---|---|
| Full opinions (today) | $7.15 | -- | -- | 79 of 79 |
| Excerpts, no fallback (unsafe) | $4.57 | 36% | $0.86 | 0 |
| `exists` < 0.5: read excerpts, then re-read in full | $7.85 | **-10%** | -$0.23 | 31 |
| `exists` < 0.5: skip excerpts, go straight to the full opinion | $5.79 | 19% | $0.45 | 31 |
| Re-read when v3 answers "unverifiable" (proxy: claims v2 called unsupported) | $5.90 | 17% | $0.41 | 12 |
| Re-read on anything but "supported" (proxy: v2 not-supported) | $7.33 | -3% | -$0.06 | 28 |

The two "proxy" rows are stand-ins, since v3 was never run: they assume v3 would
punt exactly the claims v2 found unsupported / not supported. Realistic saving:
**40-45 cents per brief**, on a weak brief less.

### 3. One run could not have shown it was safe

Same prompt, same full opinions, same model: the frozen v2 verdicts and the July
API run disagree on **6 of 90 claims, and 4 of those 6 moved in the lenient
direction** (partial -> supported) with nothing changed at all. A single v3 run
would have produced roughly that many "v3 is more lenient" claims from noise
alone. One run can catch a gross failure; it cannot certify the absence of a
small leniency drift, which was the thing that mattered most.

### 4. What adopting it would have cost

Jev in the critical path (today it only logs in shadow); non-public documents
could not use the path at all (Jev is a third-party API); a third prompt version
to maintain; and a new way to be wrong in the lenient direction -- a missed
passage reading as "fine".

### 5. Cheaper levers already in the repo

- **Batches API** (`--executor api --batch`): 50% off, Opus reads exactly what it
  reads today. Beats this idea's best case with no accuracy risk.
- **Auto-Green gate** (idea B, in shadow): removes whole claims, output tokens
  included -- 15-45% of assessment work -- rather than trimming input.

## What we did learn

- **Low `exists` flags unsupported claims.** All 13 claims v2 called
  "unsupported" score 0.23 or lower; 9 of 16 "partial" and 11 of 61 "supported"
  score under 0.5. As a filter it is one-sided: low `exists` does not mean
  unsupported (11 supported claims sit there), but high `exists` has never been
  an unsupported claim in this data. That is evidence for idea I (low-score
  triage), not a verdict. It is in-sample: these corpora were used all day.
- **Input is about half the cost of an API-path assessment.** Anything that only
  trims input tops out near a third. The bigger levers act on whole claims (the
  gate) or the price (Batches).
- **Run-to-run noise on `support` is about 7% of claims**, skewed lenient in this
  one pair of runs. Any future prompt or input comparison needs more than one
  run per arm, or it measures noise.

## When to reopen

If opinions get much longer (input dominates the bill), or for bulk screening
(idea R: whole dockets, the 525-citation set) where a few cents a claim add up.
Then: use more than one run per arm, and measure against the noise floor above.

## What is left in the repo

- `src/citation_verifier/jev_shadow.py`: the locator is now callable on its own
  -- `locate()`, `keep_indices()`, `join_passages()`. Behaviour-preserving
  refactor (same questions, state, excerpting; `RUBRIC_VERSION` unchanged).
- `scratch/jev/locator_assess/`: `jobs.csv` (per-opinion prompt sizes, July
  cost, `exists`, verdicts), the per-claim locator logs (chosen passages and
  `exists` for all 90 claims), and `v2_api_20260701/` -- the July API verdict
  files, copied here because `tests/data/results/` is gitignored.
- **Removed** (commit `07aa15c` has them; one `git revert` away): the assess-v3
  prompt, the excerpt writer `locator_excerpts.py`, the v3 branch in
  `run_assess`, the `opus-v3-api` config, and the 79 excerpt files that had been
  added to the frozen corpora. An unrecorded prompt version and a pipeline branch
  nothing uses would only confuse a later session.

The design, for the record: assess-v3 was assess-v2 with only the framing
changed ("you are reading excerpts; `[...]` marks omitted text; infer nothing
from what is omitted"), plus one rule -- "unsupported" must rest on something
visible in the excerpts, and an absence-based finding answers "unverifiable",
which sends the claim back to the full opinion. One excerpt file per opinion:
the union of each citing claim's top-5 passages plus one neighbour each side,
plus the caption passage so the reader can tell which case it is. Excerpts came
to 23-33% of opinion text.

## Process note

The arithmetic in section 1 needed no code -- two numbers from the July run and
a character count. It should have been done before building the excerpt writer
and the prompt, not after. Price an experiment's best case before building it.
