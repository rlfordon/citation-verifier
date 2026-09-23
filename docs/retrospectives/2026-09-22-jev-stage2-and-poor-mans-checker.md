# Jev session 2: grading, the one-dimension result, and a working fast checker

**2026-09-22.** Follow-on to [2026-09-19](2026-09-19-jev-exploration.md).
Technical detail and all numbers: [`scratch/jev/STAGE2.md`](../../scratch/jev/STAGE2.md).
Total Jev spend for the day: well under a dollar.

## Where it started

A question about Jev's question types — `choice` versus `score` — and whether
support could be decomposed into several weighted sub-questions instead of one.
It ended somewhere else: a working command-line checker that ran on a live brief
and found a real citation error in it.

## What we learned, in the order it happened

**1. A `score` answer is a `choice` with an expectation attached, and the
expectation is the wrong summary for a gate.** It averages away *where* the
probability mass sits, which is exactly the partial-versus-full distinction.
Every score question in the bank puts its worst not-supported claim at ≥0.91
using the `score` field, versus 0.26–0.66 for the choice questions —
discrimination is comparable, safety margin is not. Reading
`probabilities[top_level]` instead recovers most of it. Fixed in the loop
harness; every score question the phrasing loop ever evaluated had been
handicapped by the old reading.

**2. Clearing good claims and grading bad ones want opposite questions.** The
"every part as written" family gates well (worst negative 0.49) and grades
badly (0.68). The topic/whose-view family does the reverse (0.96 / 0.83).
Mixing them degrades both. Nearly every cited case really is on the right
topic, so topic questions cannot separate good from bad — but once a claim has
already failed, topic is exactly what separates "overstated" from "wrong case."

**3. Growing the dataset turned up two data bugs, and the headline number went
down.** Three briefs had been silently excluded because the oldest workdirs
wrote a repo-relative `opinion_file` path that the CSV writer truncated
mid-name, so every row looked textless. And deduplication was per workdir when
the same brief appears in several, double-counting 114 claims. After both
fixes: 332 claims across 13 briefs. The grading result, measured properly
*within* a brief rather than pooled across them, is ~0.86 — the earlier 0.879
on 7-vs-13 claims is superseded, though the new figure sits inside its old
interval.

**4. Weighting does not help, and now we know why.** Tested three ways
(log-odds, fitted, continuous): all tie a plain equal-weight count at 0.929.
The four checks are *nested* — two sensitive ones that fire on nearly every bad
claim and half the good ones, two specific ones that fire rarely and almost
never on a good claim. Counting them is already an ordinal ladder, and weights
cannot reorder a ladder.

**5. The one-dimension result.** If the checks are near-duplicates, the fix
would be a genuinely independent signal. Seven were written in three framings —
reading only the proposition, only the opinion, or doing a factual lookup. The
independence was real (correlations 0.03–0.24, against 0.95 among the existing
four) and the signal was nil (AUC 0.41–0.55, chance). Adding any of them made
things worse. A second hypothesis — that they could mark claims where the
support check is untrustworthy — also came back at chance.

> Everything uncorrelated with the support judgement was also uninformative.
> Jev appears to have **one dimension** to offer about a claim, and every
> question is a cleaner or noisier read of it.

That single result explains the whole session's pattern: why weighting never
pays, why combining never beats the best single question, why purpose-built
questions keep losing to reused ones. It is also a stopping rule — the middle
bucket is not a wording problem.

## The checker

Two-sided: clear the obvious, flag the obvious, send the rest onward. Both
sides ride in one Jev request. Thresholds fitted on six briefs, frozen, applied
to five never used for tuning: **nothing cleared wrongly, nothing accused
wrongly**, about a third of claims answered.

Then [`tools/poor_mans_check.py`](../../tools/poor_mans_check.py) — rough, terminal
only. On four briefs, 107 claims: 8 cleared (all good), 35 flagged (all bad),
64 to review. A clean brief drew zero flags; a bad one drew 22.

### Three bugs that only running it could find

Each was invisible to the offline measurement, and each points the same way.

- **It cleared a claim with a fabricated quote** that scored 0.99 on every Jev
  question. Jev was *right* — the proposition genuinely is supported; the brief
  had attached a quote the opinion does not contain. Only the quote matcher
  sees that. My measurement had excluded such claims as "not support failures,"
  so the gap could never appear.
- **Then I over-corrected** and buried a clean brief under 50% false problems by
  treating `CLOSE` quotes and `POSSIBLE_MATCH` citations as findings. The
  pipeline's own quote floor calls CLOSE a noise band, and the matcher has a
  known ceiling bug. Both now block clearing without accusing.
- **It silently dropped claims with no opinion text** — and a citation that
  resolves to the *wrong case* has, by definition, no correct opinion to fetch.
  So the single most valuable finding in a live brief was being thrown away.

**The theme:** three times in one session, the part doing the real work was the
free deterministic layer, not Jev. That should shape how this is described to
users — the fast checker is a stack, and Jev is the smallest piece of it.

## The live run

`matters/aliaj-rochelle-park` — a Rule 12(b)(6) brief from a real D.N.J. case,
never seen by anything here. 34 claims, 24 citations, 17 seconds, ~3 cents, no
LLM assessment. 12 cleared, 21 to review, 1 problem.

The problem was real: the brief cites **"Tice v. Cramer, 133 N.J. 247, 355"**.
133 N.J. 247 is *State v. Reed*; *Tice v. Cramer* is at 133 N.J. **3**47, and
the brief's own pincite of 355 fits the correct range. A transposed digit,
caught for free in seconds.

(Extraction was done by hand — it is the one step that genuinely needs an LLM.)

## Blocker for the next step

9 of 34 quotes in that brief came back `CLOSE`, which is almost certainly the
quote-matcher ceiling bug rather than nine misquotes, and it is what pushed the
review pile to 62%. The fuzzy path compares a length-`w` quote against a `1.5w`
window, so the ratio cannot exceed 0.80. Demonstrated:

| similarity | verdict | quote |
|---|---|---|
| 1.00 | VERBATIM | exact |
| 0.81 | CLOSE | verbatim + a star-pagination marker |
| 0.84 | CLOSE | one character changed |
| 0.82 | CLOSE | one word changed, meaning altered |
| 0.35 | FABRICATED | genuinely different text |

Verbatim-plus-junk, a typo, and a meaning-changing substitution are
indistinguishable. A spec already exists (`scratch/TODO.md`, "quote matcher
fuzzy path is capped at 0.80"), diagnosing the cause and proposing the fix:
classify on **what** differs after alignment rather than on the ratio. It is at
"decision needed," not implementation — and it now blocks the fast checker as
well as the `opinion_block` verbatim check it was originally written for.

## Process note

Mid-session feedback: too many invented names to keep track of —
`search`/`lock2`, "stage 1/stage 2", "task A/task B", "badge vs colour". Fair,
and repeat feedback. The dataset labels were renamed to plain words, every
script's printed output was rewritten to say what it means, and STAGE2.md now
opens with a glossary of every term it coins. The rule going forward: describe
the thing rather than naming it, and if a name must exist in code, make it
literal.

## Next

1. **Fix the quote matcher.** It now blocks two things, and the spec is ready
   for a decision.
2. Decide whether the fast checker earns real plumbing (its own column, a verb,
   a report lane) or stays a command-line tool.
3. Clean labelled held-out claims remain the binding constraint for everything
   measured here. The repo is exhausted; more means running the pipeline on new
   briefs.
