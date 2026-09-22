# Stage 2 — grading the claims the gate does not clear (2026-09-22)

Everything Jev has done so far is **stage 1**: an auto-Green gate that clears
supported claims and escalates everything else, never issuing a Yellow or Red.
This is about **stage 2** — the job that starts where stage 1 stops. Among the
escalated claims, is this an *overstatement* (the case is on point, the brief
claimed more than it holds) or is the case *not doing the work at all* (wrong
subject, or the holding runs the other way)?

Stage 2 orders a review queue. It never auto-clears anything, so it carries
none of the gate's safety risk — which is why it can afford question types and
combination rules the gate cannot.

Scripts: `test6_choice_vs_score.py`, `stage2_data.py`, `test7_stage2.py`.
All responses cached in `cache.json`; reruns are free. Total spend: ~$0.20.

## Choice vs score (test 6)

A `score` answer is a `choice` with ordered labels plus an expectation
computed for you:

    {"score": 2.77, "legend": {"0": ..., "3": ...},
     "probabilities": {"0": 0.06, "1": 0.01, "2": 0.03, "3": 0.90}}

The expectation is the only real difference, and for a safety gate it is the
wrong summary: it averages away *where* the mass sits, which is precisely the
partial-vs-full distinction. Two very different answers — "90% sure every part
is there" and "split evenly between most-of-it and all-of-it" — land on nearly
the same number.

`coverage` (choice) and `coverage_level` (score) are near-identical wording and
the cleanest A/B in the bank. On the 97 eligible unhedged search claims:

| signal | type | AUC vs partial | worst not-supported claim | greens above it |
|---|---|---|---|---|
| `coverage` | choice | 0.909 | **0.470** | 46/60 |
| `coverage_level` (score field) | score | 0.864 | **0.920** | 33/60 |
| `coverage_level` (p of top level) | score | 0.899 | 0.790 | 32/60 |

Not a one-off: **all three** score questions in the bank put their worst
negative at >= 0.91 using the `score` field, versus 0.26-0.66 for the
choice/noul champions. Discrimination (AUC) is comparable; **margin is not.**

**Reading `probabilities[top_level]` beats the built-in expectation on every
score question, every metric.** Fixed in `loop_eval.py::_score_top` and
`loop_bank.py::_green_value` — every score question the phrasing loop ever
evaluated was handicapped by the old normalization.

## The two jobs want different questions

The signals that clear greens are close to the *opposite* of the signals that
grade the rest:

| question family | stage 1 (clear greens) | stage 2 (grade the rest) |
|---|---|---|
| "every part as written" (`as_written`, `coverage`, `adds_specifics`, `own_words`) | AUC 0.95, worst negative **0.49** | 0.68 |
| topic and view (`same_issue`, `whose_view`, `states_it`, `support_level`) | AUC 0.89, worst negative **0.96** | **0.83** |
| the two families mixed | 0.92, worst negative 0.71 | 0.79 |

Mixing degrades both. The mechanism is plain: nearly every cited case really
is on the right topic, so topic questions cannot separate good from bad — but
once a claim has already failed, topic is exactly what distinguishes
"overstated" from "wrong case."

**So: two stages with disjoint question sets, not one weighted score.**

## The stage-2 dataset (`stage2_data.py` -> `stage2_dataset.json`)

Labelled by **badge, not colour**. A Yellow can mean "quote is a paraphrase",
which is not a support failure at all; the badge names the *kind* of failure,
so quote and citation-resolution findings can be dropped instead of poisoning
the partial class. 47 claims dropped on that rule.

| badge | class | kind |
|---|---|---|
| Supported | supported | supported |
| Overstated -- case partially supports | partial | overstated |
| Case on unrelated subject | unsupported | wrong_subject |
| Inverts the holding | unsupported | inverted |
| Not supported by cited case | unsupported | not_supported |

Splits are by **brief** — a brief never straddles two splits, and re-runs of
the same brief share a group.

| split | briefs | claims | supported | partial | unsupported |
|---|---|---|---|---|---|
| `search` | payne, kettering-mtd, sonnet-q3, ohio-mailbox, extrinsic, withers | 196 | 117 | 46 | 33 |
| `lock2` | maxwell, ohio-pc, lawd207038, protege, makewhole | 71 | 51 | 7 | 13 |
| `colour` | fletcher, fivehouse | 41 | 6 | 23 | 12 |

`lock2` briefs have never been touched by any Jev tuning. `colour` is a
**secondary** check only: legacy `/verify-brief` runs with no badge, labelled
from the colour with all quote-problem claims dropped — coarser, and from an
older assessment prompt.

Long opinions in the newer briefs exceed Jev's ~32K-token input cap, so
`long_excerpt()` locates in batches that fit and runs a second locator pass
over the pooled candidates. It collapses to the original single-pass request
(byte-identical, cache still hits) whenever the opinion fits.

## Results (test 7)

Task B = partial vs unsupported, among non-supported claims only.

| signal | origin | B search | B lock2 | B colour | lock2 90% CI |
|---|---|---|---|---|---|
| `whose_view` | carried | 0.925 | **0.879** | 0.815 | [0.73, 1.00] |
| `support_level` | carried | 0.857 | 0.874 | 0.795 | [0.71, 1.00] |
| `direction` | new | 0.892 | 0.874 | 0.764 | [0.71, 1.00] |
| `states_it` | carried | 0.905 | 0.868 | 0.788 | [0.71, 0.98] |
| `same_issue` | carried | 0.893 | 0.835 | 0.768 | [0.67, 0.97] |
| `gap` | new | 0.820 | 0.813 | 0.726 | [0.64, 0.95] |
| `topic` | new | 0.852 | 0.791 | 0.774 | [0.61, 0.93] |
| `reach` | new | 0.798 | 0.747 | 0.743 | [0.55, 0.92] |

**1. Stage 2 works, and better than the first pass suggested.** `whose_view`
reaches 0.879 on briefs no tuning has seen. The earlier read was 0.847 on a
noisier label set; dropping quote-driven failures from the partial class is
most of the difference.

**2. Purpose-built questions did not beat carried ones.** `gap`, `topic` and
`reach` were written for this taxonomy and all lost to `whose_view` and
`support_level`, which were written for the gate. Only `direction` was
competitive. This matches LOOP.md's finding that single-question wording gains
mostly do not transfer.

**3. "Wrong subject" vs "overstated" is the sharpest cut available** — 0.966
search / 0.886 lock2 on `same_issue`. That is the distinction with the clearest
operational meaning: a wrong-subject citation is a different conversation with
the author than an overstated one.

**4. Combining still does not beat the best single signal.** Third time:
`whose_view` alone 0.879 vs carried-4 mean 0.868 vs all-8 mean 0.846. The
signals are ~0.92 correlated within family — they are the same model answering
near-paraphrases about the same text. Weights need far more data than we have;
test6 showed a fitted 30-signal model tying a 3-signal unweighted mean.

**5. A score question is fine here.** `support_level` is the #2 stage-2 signal
despite being the worst kind of gate signal. The averaging that destroys safety
margin is harmless when nothing auto-clears and you only want an ordering.

## Caveats

- `lock2` task B rests on **7 partial vs 13 unsupported**. The 90% interval on
  0.879 is [0.73, 1.00]. Treat the ranking as a hypothesis, not a measurement.
- The `colour` split is systematically lower on every signal, which is what
  coarser labels should look like — but it could also mean the effect is
  partly an artefact of how badges are assigned.
- Labels are Opus verdicts, not human judgements, except in the frozen corpora.
- `payne` appears three times (corpus, `matters/payne`, `briefs/payne-proposed`);
  near-duplicate propositions are dropped and all three share one group.

## Next

1. **More held-out `partial` claims** is the binding constraint, exactly as it
   was for the gate. Every `/proposition-verifier` run with `JEV_SHADOW=1`
   grows the pool; stage 2 needs the badge written too.
2. Log the stage-2 questions in `jev_shadow.py` alongside the gate rubric
   (bump `RUBRIC_VERSION`), so the pool grows for both jobs at once.
3. Only then consider a `JevExecutor` that orders the review queue. Nothing
   here should touch claims.csv or the report yet.
