# Retrospective: rebuilding the quote matcher (2026-09-22)

The technical write-up — what the bug was, what replaced it, and the 63-quote
before/after calibration — is
[`docs/plans/2026-09-22-quote-matcher-structural-buckets.md`](../plans/2026-09-22-quote-matcher-structural-buckets.md).
This is the part that doesn't belong in a design doc: how the session actually
went, and what it says about where the risk in this code lives.

Commits: `111e9ff`, `47a2566`, `7d94026`, `17b6630` (all on `main`).

## The arc

**The spec was right about the hard part and I should say so.** The
`OPEN 2026-09-19` TODO entry already contained the key move — bucket on *what*
differs after alignment rather than on the ratio — and the reasoning for why a
corrected ratio alone is wrong. That was the whole insight. Implementation was
downhill from there.

**Where I departed from it, and where I shouldn't have.** The spec's junk list
was incomplete — it missed ellipsis elisions, brackets appearing in the
*opinion* rather than the quote, and span overhang — and each omission produced
a false CLOSE in the real data. Those were right to fix.

On the floor, the spec said "drop the noise band, every CLOSE floors." I
measured that it cost two rows (`withers-21`, `wainwright-17`), said so, and
shipped the spec's version anyway. That was the wrong call, and one question
from the user surfaced it: the alternative — exempt a CLOSE that touches a
single word — restored both baselines *and* improved A/B from 56 to 57/61. I
had the measurement in hand before shipping and deferred to the spec instead of
to the numbers.

**Then the code review found four ways a real misquote graded clean.** All
reproduced on the first try:

| quote | opinion | graded |
|---|---|---|
| `the defendant [was] liable` | `the defendant **is not** liable` | VERBATIM 1.0 |
| `the officer did have cause` | `did **not** have probable cause` | CLOSE, no floor |
| `entitled to days notice` | `entitled to **10** days notice` | VERBATIM 1.0 |
| `a period of 10 days` | `a period of **30** days` | CLOSE, no floor |

## The lesson worth keeping

**The bucketing was never the risky part. The licensing was.**

Deciding "junk-only means VERBATIM" is a sound rule, and it survived review
untouched. Every hole was in the list of things I had decided count as junk —
a bracket may stand for up to four words, a bare digit is always a footnote
marker, a lone altered word is always noise. Each was a plausible guess,
calibrated against 63 quotes that happened to contain no adversarial case. The
corpora are drawn from real briefs, and real briefs mostly don't hide
negations inside brackets. So the calibration set could not have caught these;
only adversarial construction could, which is exactly what the review did.

Concretely, for next time: **every licensing rule needs a counterexample test,
written by asking "what is the worst thing this rule would let through?"** —
not by checking that the corpus still scores well. The corpus tells you about
honest briefs. The counterexample tells you about the ones this tool exists
for.

The fix that settled it was cheap and I should have reached for it earlier:
a lone altered word is exempt *unless* it is a negation, a modal, or carries a
digit. That also retired a claim I'd made twice in writing — that nothing
deterministic could separate `"or"->"and"` from `shall->may`. False. A modal is
meaning-bearing wherever it sits; it just never gets the exemption.

## Process notes

* **I left a stale flag up.** I listed "the assess prompt can't see which words
  changed" as an open gap, then closed it from a different direction an hour
  later via the review fixes and didn't retract the note — which read as
  pending work and cost the user a round of confusion about whether the
  cassettes needed re-recording. They didn't. Nothing in this session touched a
  prompt.
* **`tools/poor_mans_check.py` is not bit-reproducible.** Re-running it on
  Aliaj moved two rows that had nothing to do with the code change: Jev's own
  scores drift by a mean of 0.014 between runs, max 0.14 observed. Worth
  knowing before reading any future before/after of that checker as a diff.
* **Two builder bugs surfaced by accident, both real.** `write_cassette`
  truncated the cassette file and actually destroyed the `assess-v2`
  recordings on my first rebuild (restored from git); and `check_quotes` only
  ever ran on the withers corpus, so payne and wainwright carried quote columns
  frozen before `quote_floor` existed — six FABRICATED quotes that had never
  floored. Fixing the second is the entire A/B gain to 57/61; it has nothing to
  do with the matcher.

## Follow-ups filed

* `scratch/TODO.md` — citation-form aliases inside a quotation (`Rule 8` vs
  `Fed R.Civ.P. 8`); `_locate` at ~4s per non-exact quote, with the two
  optimizations that measured *slower* recorded so nobody retries them.
* `scratch/ROADMAP.md` — key the cassettes on a hash of the rendered prompt
  rather than a hand-typed version label, and make the baselines directional.
  Today a one-line prompt improvement costs ~$47 and a full re-adjudication of
  every baseline, which is why small prompt improvements don't get made.

## Still true after all this

The `opinion_block` verbatim check that started this whole thread — confirming
that a passage an agent quotes back into the report is really in the opinion —
is still unbuilt. It now has the primitive it needed.
