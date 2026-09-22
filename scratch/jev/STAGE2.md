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

Scripts: `test6_choice_vs_score.py`, `stage2_data.py`, `test7_stage2.py`,
`test8_checker.py`. All responses cached in `cache.json`; reruns are free.
Total spend: ~$0.25.

## Glossary — every term this document coins

| term | plain meaning |
|---|---|
| **stage 1** / "the gate" | Jev clearing obviously-supported claims so no LLM call is needed. Its dangerous error is waving a bad citation through. |
| **stage 2** | Judging the claims stage 1 did *not* clear: overstatement, or a case that does not do the work at all. |
| **tuning briefs** | The briefs used to pick questions and thresholds. Looking at these repeatedly is expected and fine. |
| **held-out briefs** | Briefs kept completely out of every choice, used once at the end to check whether a result survives on material nothing was tuned against. Once read, they are spent. |
| **detailed labels** | Ground truth taken from `badge_label`, which names the *kind* of failure ("Case on unrelated subject"), so quote problems can be excluded. |
| **coarse labels** | Ground truth taken from the old Green/Yellow/Red colour, which lumps several kinds of failure together. Noisier. |
| **supported / partial / unsupported** | The three ground-truth classes. "Partial" = the case is on point but the brief claimed more than it holds. |
| **AUC** | Pick one good claim and one bad claim at random; how often does the signal rank them correctly. 0.5 = coin flip, 1.0 = perfect. |
| **worst negative** | The highest score reached by any claim that should NOT be cleared. A threshold has to sit above it, so the lower it is, the more room there is. |
| **margin / headroom** | The gap between that worst negative and the threshold. Without it, a threshold that worked on old briefs fails on the next one. |
| **bad clear** | A bad citation cleared as good. |
| **false accusation** | A good citation reported as bad. |
| **noul / choice / score** | Jev's three question types: a single probability, a set of named options, and an ordered scale. |

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

**332 claims across 13 briefs.** Two independent axes, deliberately kept apart:

- label detail — **badge** (`badge_label` names the *kind* of failure, so
  quote and citation-resolution findings are dropped instead of poisoning the
  partial class) or **colour** (legacy `/verify-brief` runs that only recorded
  Green/Yellow/Red — coarser, older prompt, a secondary check).
- which half of the data — **search** (briefs some Jev tuning has touched) or **held-out**
  (briefs no Jev tuning has ever touched).

| badge | class | kind |
|---|---|---|
| Supported | supported | supported |
| Overstated -- case partially supports | partial | overstated |
| Case on unrelated subject | unsupported | wrong_subject |
| Inverts the holding | unsupported | inverted |
| Not supported by cited case | unsupported | not_supported |

| source | split | claims | supported | partial | unsupported | briefs |
|---|---|---|---|---|---|---|
| detailed | tuning | 138 | 79 | 35 | 24 | payne, kettering, sonnet-q3, ohio-mailbox, extrinsic, withers |
| detailed | held out | 71 | 51 | 7 | 13 | maxwell, ohio-pc, lawd207038, protege, makewhole |
| coarse | tuning | 40 | 24 | 7 | 9 | payne, kettering |
| coarse | held out | **83** | 17 | **37** | **29** | fletcher, fivehouse, valve |

Two fixes went into getting here, both of which changed the numbers:

- **Truncated `opinion_file` paths.** The oldest workdirs stored a
  repo-relative path that the CSV writer cut off mid-name
  (`briefs/Valve v Rothschild/opinions/moore-v-ashland`), so every row looked
  textless and three whole briefs had been silently excluded.
  `resolve_opinion()` falls back to a unique stem-prefix match. Recovered
  Valve (42 rows, negative-rich), kettering-v-collier and fivehouse.
- **Cross-workdir duplicates.** Deduplication was per *workdir*, but the same
  brief appears in several (`matters/payne` and `briefs/payne-proposed` are one
  brief twice). Deduplicating per *group* removed **114 double-counted
  claims** — the earlier 308-claim count was inflated.

A brief never straddles a split: re-runs and companion filings share a `group`.

Long opinions in the newer briefs exceed Jev's ~32K-token input cap, so
`long_excerpt()` locates in batches that fit and runs a second locator pass
over the pooled candidates. It collapses to the original single-pass request
(byte-identical, cache still hits) whenever the opinion fits.

## Results (test 7)

Task B = partial vs unsupported, among non-supported claims only.

**Measure it within a brief.** The review queue is ordered inside one brief, so
that is the operationally relevant number — and pooling across briefs reads
*lower than every brief in the pool* (0.844 pooled vs 0.860 within, and single
briefs ranging 0.76–0.96). Different briefs sit on different score scales;
pooling mixes them. That is an artefact, not a finding.

| signal | origin | pooled | within-brief, all | within, badge | within, colour |
|---|---|---|---|---|---|
| `whose_view` | carried | 0.844 | **0.860** | 0.969 | 0.795 |
| `states_it` | carried | 0.821 | 0.843 | 0.933 | 0.794 |
| `direction` | new | 0.829 | 0.841 | 0.935 | 0.786 |
| `support_level` | carried | 0.804 | 0.812 | 0.910 | 0.767 |
| `same_issue` | carried | 0.822 | 0.808 | 0.901 | 0.733 |
| `topic` | new | 0.796 | 0.787 | 0.874 | 0.728 |
| `gap` | new | 0.740 | 0.757 | 0.872 | 0.698 |
| `reach` | new | 0.734 | 0.738 | 0.830 | 0.686 |

**1. Stage 2 works: ~0.86 within-brief on 13 briefs.** The badge/colour gap
(0.969 vs 0.795) is large and holds for **all eight signals**, which points at
label quality rather than task difficulty — badge labels name the failure kind,
colours are a coarse proxy. The truth is likely between: the badge figure is
flattered because the badge was assigned by an LLM reading the same opinion,
and the colour figure is depressed by label noise.

An earlier, smaller cut of this put `whose_view` at 0.879 on 7-vs-13 held-out
claims with a 90% interval of [0.73, 1.00]. The bigger sample lands inside that
interval and pins it down; treat the earlier number as superseded.

**2. Purpose-built questions did not beat carried ones.** `gap`, `topic` and
`reach` were written for this taxonomy and all lost to `whose_view` and
`states_it`, which were written for the gate. Only `direction` was competitive.
This matches LOOP.md's finding that single-question wording gains mostly do not
transfer.

**3. "Wrong subject" vs "overstated" is the sharpest cut available** — 0.986
search / 0.886 held out on `same_issue`. That is the distinction with the
clearest operational meaning: a wrong-subject citation is a different
conversation with the author than an overstated one.

**4. Combining still does not beat the best single signal by much.** Fourth
time: on held-out briefs with coarse labels the carried-4 mean (0.777) edges `whose_view` alone
(0.759), but that is inside the noise, and on held-out briefs with detailed labels the single signal
wins (0.879 vs 0.868). The signals are ~0.92 correlated within family — the
same model answering near-paraphrases about the same text.

**5. A score question is fine here.** `support_level` is a top-4 stage-2 signal
despite being the worst kind of gate signal. The averaging that destroys safety
margin is harmless when nothing auto-clears and you only want an ordering.

## Test 8 — the two-sided checker

Stage 2 on its own is weak product: escalated claims go to the LLM anyway, and
the LLM already produces the badge. The version that pays is **two-sided** —
clear the obvious greens, flag the obvious reds, send only the middle to the
LLM. Both sides ride in **one Jev request** (`bank/checker_v0.json`), because
extra questions add no latency and almost no cost:

- clear side: `as_written` (the phrasing loop's champion question)
- flag side: `min(whose_view, same_issue)`

The two errors are not symmetric and are never traded against each other: a
**bad clear** waves a bad citation through; a **false accusation** calls a good
citation bad. The second is the one that destroys trust in a standalone tool.

One fix from the one-sided run: `whose_view` now counts `other_judge` as
acceptable. Citing a dissent *as* a dissent is legitimate, and penalising it
produced the only false accusation in the earlier pass (an ohio-pc claim that
said "Pfeifer J. **dissent** in Gill..." in the proposition itself). Whether a
brief *hides* that it is quoting a dissent is a separate, deterministic check.

**Protocol:** both thresholds chosen on the tuning briefs by a rule declared in the
script, with explicit margins (LOOP.md: a threshold flush against the worst
training example does not survive a new brief), then frozen and applied to
the held-out briefs unchanged.

    clear if as_written > 0.580                  (worst search negative 0.480 + 0.10)
    flag  if min(whose_view, same_issue) < 0.130 (lowest search supported 0.180 - 0.05)

| cell | claims | CLEAR | bad clears | FLAG | false accusations | to LLM |
|---|---|---|---|---|---|---|
| search (fitted here) | 178 | 48 (27%) | **0** | 18 (10%) | **0** | 63% |
| **held-out (never seen)** | 154 | 36 (23%) | **0** | 19 (12%) | **0** | 64% |
| — badge-labelled | 71 | 25 (35%) | 0 | 4 (6%) | 0 | 59% |
| — colour-labelled | 83 | 11 (13%) | 0 | 15 (18%) | 0 | 69% |
| all 13 briefs | 332 | 84 (25%) | **0** | 37 (11%) | **0** | 64% |

The two buckets never overlap (0 claims trip both thresholds), so the rule is
well-formed rather than accidentally consistent.

Read it two ways:

- **As a triage in front of the LLM:** answers ~36% of claims itself, so the
  expensive pass runs on ~64%. At ~0.5 s and ~$0.0005 per claim, Jev's own cost
  is a rounding error against an Opus assess call.
- **As a standalone fast checker:** on a 30-claim brief, roughly 7 claims come
  back green, 4 come back flagged, and 19 say "can't tell — needs review," in
  about 15 seconds for about 2 cents.

**Flag recall is 36–43%.** It catches four in ten bad citations, not ten. A
standalone tool must say so: silence is "not checked", never "clean".

## Caveats

- **Zero errors, but out of small numbers.** 0 bad clears in 36 held-out cleared
  claims is consistent with a true rate up to ~8% (rule of three); 0 false
  accusations in 19 flagged, up to ~16%. Pooled over all 13 briefs: ~4% and ~8%.
  These are encouraging, not certified.
- The dataset **excludes fabricated cases and fabricated quotes** — the
  CourtListener lookup and the quote matcher catch those upstream for free. So
  test 8 measures the hard residual class, and the full stack catches more.
- `held-out briefs with detailed labels` task B still rests on **7 partial vs 13 unsupported**. The big
  held-out cell (`held-out briefs with coarse labels`, 37 vs 29) has the coarser labels. There is no
  cell that is both large and cleanly labelled — that is the gap to close.
- Labels are Opus verdicts, not human judgements, except in the frozen corpora,
  and the badge was assigned by a model that read the same opinion Jev reads.
- `valve` is a brief built largely of fabricated citations, so its negatives
  may be unrepresentatively blatant; it is 42 of the 83 held-out briefs with coarse labels rows.
- The `quote_suspect` flag (legacy free-text that complains only about quote
  wording on a claim the case otherwise supports) is heuristic. Only 3 rows
  trip it; `--strict` excludes them and changes nothing material.

## Next

1. **Cleanly-labelled held-out claims** is the binding constraint, exactly as
   it was for the gate. The repo is now exhausted: every workdir with a label
   and recoverable opinion text is in the dataset. Growing it further means
   *running the pipeline on new briefs* — `briefs/` holds several unprocessed
   source documents (`north atlantic v indiana import/brief.pdf`,
   `gov.uscourts.insd.216295.134.0.pdf`, `appellant-brief.txt`, the make-whole
   .docx) that would yield badge-labelled claims at Opus assess cost.
2. Log the stage-2 questions in `jev_shadow.py` alongside the gate rubric
   (bump `RUBRIC_VERSION`), so the pool grows for both jobs at once.
3. Only then consider a `JevExecutor` that orders the review queue. Nothing
   here should touch claims.csv or the report yet.
