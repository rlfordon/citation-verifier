# Phrasing loop — design and results (2026-09-19)

Question: how much does the wording of Jev's questions matter for the
auto-Green gate, and can a loop find better wording without fooling itself?

## Design

Borrowed from the literature survey (GEPA's reflective diagnosis + keeping
several survivors; TypeSafe's own `autoresearch_feature_discovery` cookbook)
rather than adopting a framework — DSPy/MIPROv2 want 200+ examples and a
text-generating model; this is ~100 claims and a typed classifier.

- **Two tiers.** Asking Jev new questions is the "expensive" tier (one request
  per claim, ~3 cents and ~20 s per round). Re-fitting the combination rule over
  answers already collected is free. So the loop grows a **question bank**
  (`bank/round_NN.json`) and re-searches the combiner each round.
- **Proposer = Claude, reading failures, not a score.** Each round shows the
  highest-scoring not-supported claims and the nearly-cleared supported ones,
  with the *existing* explanation of each label (human note or Opus finding).
  New probes must state a general principle.
- **Guards.** Polarity (`_green`) is declared in the question file, never fitted.
  Leakage check: no case names, no 7-word overlap with any claim, 900-char cap
  per question. Flat-answer questions are flagged.
- **Objective.** On eligible, unhedged search claims: supported claims scoring
  above *every* not-supported claim plus 0.03 headroom. Candidates are compared
  on the **20th percentile of that count under resampling within each brief**, so
  one lucky negative cannot win. Combiner = greedy forward selection, `min` or
  `mean`, at most 4 signals.
- **Acceptance.** A challenger takes the crown only if its lower bound beats the
  champion's by >= 2 claims AND it adds no cross-brief bad clears (threshold set
  on the other briefs, applied to the held-out one).
- **Split.** Search = Withers, Payne, Wainwright (97 eligible unhedged claims:
  60 supported, 24 partial, 13 unsupported). **Lockbox = four briefs never seen**
  by the loop or the proposer (kettering-mtd, sonnet-q3-protest, ohio-mailbox,
  extrinsic-evidence: 65 eligible claims, 21 not supported, Opus-labelled).
  8 Withers labels the ground truth itself marks "hedged" are excluded.
- **Budget.** 3 proposing rounds, 23 new probes. Total Jev spend ~$0.15.

Run: `loop_bank.py` (all rounds), `--diagnose` (failures), `--lock` (final).

## What the rounds found

Round-0 failures were strikingly uniform: every blocking negative was a
**compound proposition where the opinion supports the main thrust but not one
clause, qualifier, or added detail** ("supports the felony half... says nothing
about misdemeanors"). Jev's original `relation` question answers "supports" on
the main thrust. Round 1 asked about that directly.

| round | champion | cleared (of 60) | lower bound | cross-brief bad | top negative |
|---|---|---|---|---|---|
| 0 | mean(relation, support_level) | 38 | 36 | 1 | 0.888 |
| 1 | mean(coverage, adds_specifics) — **accepted** | 47 | 43 | 1 | 0.550 |
| 2 | (no challenger beat round 1) — rejected | 47 | 43 | 1 | 0.550 |
| 3 | mean(as_written, as_written_lean) — **accepted** | 49 | 45 | 0 | 0.225 |

Single questions, discrimination of supported vs **partial** (search set):
original `relation` 0.874 -> `adds_specifics` ("Does the proposition add a
specific standard, element, category, or example that does not appear in the
opinion?") 0.927. Round 2's probes for contradiction / fact-bound rulings /
narrower rules added nothing. Round 3 re-asked the winners with a leaner state
(no `brief_sentence`); the winning combiner averages the same question asked
under both state framings.

## Lockbox (opened once, thresholds frozen from the search set)

| rubric | threshold | cleared (of 65) | bad | nearest negative |
|---|---|---|---|---|
| baseline `min(relation, states_it, same_issue)` | 0.890 | 22 | 0 | 0.790 |
| round-3 champion | 0.255 | **30** | **0** | **0.250** |

**+36% coverage with no bad clears — but by a hair.** A lockbox negative scored
0.250 against a 0.255 threshold. The lockbox contained a not-supported claim
that outscored every negative in the search set. That is the overfitting risk
showing up exactly where expected, and it means the frozen threshold is too
tight to ship.

### Post-hoc looks (after the lockbox was open — descriptive, not a fair test)

- With the **same 0.10 safety margin** for every rubric: baseline clears 9,
  round-1 champion 25, round-3 champion 28; none has a bad clear. The champion's
  scores are far more spread out, so it tolerates a real margin; the baseline
  does not. This is the most decision-relevant number here and it needs
  confirming on a brief nobody has looked at.
- The round-1 champion had **1 bad clear** at its frozen threshold.
- **Single-question gains mostly did not transfer.** The lockbox briefs were
  easier for the original wording (`relation` vs partial: 0.874 search ->
  0.930 lockbox). The best search-set probe, `adds_specifics_lean`, fell from
  0.939 to 0.825. `coverage` (0.945), `relation_structured` (0.946) and
  `own_words` (0.935) held up and edged the original.

## Reading

1. Wording matters, and the useful direction is specific: ask whether **every
   part as written** is in the opinion, not whether the opinion "supports" it.
2. The gain is real but smaller and noisier than the search set suggested
   (38 -> 49 there; 22 -> 30 on unseen briefs, with a thin margin).
3. 21 negatives cannot certify safety: zero bad clears of 21 is compatible with
   a true rate up to ~14%. The binding constraint is now labelled negatives —
   especially "partial" claims from new briefs — not question wording.
4. The lockbox is spent. Any further tuning needs fresh briefs.

## Suggested next steps

- Adopt `as_written` (both state framings) + `coverage` as the gate rubric, with
  a threshold carrying a real margin (~0.33), and confirm on new briefs.
- Every future `/proposition-verifier` run is free labelled data: log Jev's
  answers in shadow mode alongside Opus's verdicts and re-check the gate as the
  pool grows.

## Shadow logging — built (same day)

`src/citation_verifier/jev_shadow.py` + the `jev-shadow` verb; runs inside `full`
when `JEV_SHADOW=1`. Backfilled on all six prior runs (202 claims, ~11 cents).
Pooled view from `tools/jev_shadow_report.py` — 132 gate-eligible claims, 59 not
supported. None of this is fresh data (two runs were tuning data, four were the
lockbox), so read it as description, not validation.

| gate | threshold | cleared | bad | nearest not-supported claim |
|---|---|---|---|---|
| v1 (as-written) | 0.33 | 48 | 0 | 0.295 |
| v1 (as-written) | 0.50 | 38 | 0 | 0.295 |
| v0 (original) | 0.90 | 35 | 0 | bad clears begin at 0.70 (6) |
| v0 (original) | 0.50 | 77 | 13 | — |

At a threshold with real headroom the two gates clear about the same number of
claims (38 vs 35). What the rewording bought is **margin**: v1's worst
not-supported claim sits at 0.295, far below a 0.50 threshold, where v0 needs
0.90 to stay clean. The same claim scored 0.250 in the loop and 0.295 here:
Jev's answer to a question shifts slightly with the other questions in the same
request, so thresholds need headroom and `RUBRIC_VERSION` must change whenever
the question set does.
