# Quote matcher: structural buckets replace the ratio cut

**2026-09-22.** Closes the `OPEN 2026-09-19` item in `scratch/TODO.md`.

## The bug

`quote_matcher._best_match_with_passage` slid a window over the opinion and
scored the quote (length `w`) against a chunk of length `1.5w`. The extra half
window is there to absorb insertions, but it also sits in the denominator:
`SequenceMatcher.ratio()` is `2M/T`, so the ratio could never exceed
`2w / 2.5w = 0.80`.

Consequences, all of them load-bearing:

* `VERBATIM` (`ratio > 0.85`) was reachable **only** by exact substring match.
  `if best > 0.95: break` was dead code.
* A quote that was verbatim apart from a star-pagination marker scored *below*
  one with a real word changed. `wainwright-16` — verbatim except for a `*534`
  in the middle of the sentence — was recorded `FABRICATED` at 0.46.
* Everything real landed in a narrow `[0.75, 0.85)` smear, and the
  `_CLOSE_FLOOR_MAX_SIM = 0.75` "transcription-noise band" in `_quote_floor`
  was calibrated on that smear. It was calibrated on an artifact.

## Why a corrected ratio is not the fix

Fixing the denominator alone moves the real misquotes up with everything else.
On the corrected scale `withers-04` goes 0.64 → 0.79 and `withers-38` 0.73 →
0.83; both cross the old 0.75 cutoff and stop flooring. And no threshold can
separate the two cases that matter most: *verbatim plus pagination junk* and
*one word changed* both score ~0.96 in a long quote. A `shall` → `may` swap is
a serious misquote that any ratio cut calls verbatim.

## What was built

Locate, then align, then ask *what* differs.

1. **Locate** with the existing oversized-chunk scan — it is good at finding
   the region, which is all it was ever good at. Keep the top 6 candidates.
2. **Align**: for each candidate take the outermost anchor blocks, trim the
   span to them, then extend each end by however much of the quote hangs off,
   so a quote whose first or last words are absent is compared against real
   opinion text rather than against nothing. Snap the edges to whole words and
   whole bracket groups. Recompute the ratio against *that* span — no ceiling.
3. **Classify** on a word-level diff of the quote against the aligned span.
   Junk comes off both sides before the diff: star pagination, quote marks and
   punctuation, line-break hyphenation (`non-moving` == `nonmoving`),
   possessive apostrophes (CL's extraction drops some), and case. The
   remaining opcodes decide:

   | opcode | verdict |
   |---|---|
   | insertion at a span edge | licensed — a quotation is an excerpt, and the span boundary is the matcher's choice, not the quoter's |
   | insertion at an ellipsis in the quote | licensed, up to 60 words |
   | insertion at a bracketed alteration in the quote | licensed, **one word only** |
   | insertion of a bare 1–2 digit token | licensed as a footnote marker, **unless the preceding opinion word introduces a quantity** (`to`, `within`, `least`, …) |
   | deletion at a bracketed alteration in the *opinion* | licensed — the reported text carries its own, as Iqbal does quoting Twombly's `"[A] plaintiff's obligation"` |
   | anything else | a real alteration → `CLOSE` |

   No alterations → `VERBATIM`. Alignment below 0.6 → `FABRICATED`: the quote
   is not in the opinion at all. Real fabrications align at ≤ 0.52 and the
   worst genuine alteration at 0.69, so that cut is not tight.

The gap limits matter, and the first pass had them too loose. Review found
that a 4-word bracket let `the defendant [was] liable` grade VERBATIM against
"the defendant **is not** liable" — a misquote wearing a bracket. Every
bracket in the corpora replaces exactly one word, so the limit is 1. The same
review found the footnote rule eating the `10` in "entitled to 10 days
notice"; a bare digit is now a marker only where the word before it does not
introduce a quantity.

`similarity` is now word-content similarity with the licensed material removed,
so `VERBATIM` is exactly `similarity == 1.0`. `QuoteVerification` gained
`alterations: tuple[str, ...]` — each real difference named (`"or -> and"`,
`"dropped: genuine issue of"`) — plus `altered_words: int`, how many words
those differences touch, and `altered_tokens: tuple[str, ...]`, the words
themselves from both sides. All three are persisted per quote in the
`quote_check` column. `similarity` is word-content similarity on the VERBATIM
and CLOSE paths and is capped below 1.0 for CLOSE, since 1.0 means VERBATIM;
on the FABRICATED path there is no trustworthy span to compare words against,
so it is the raw character alignment ratio instead.

## The band is gone; the exemption is structural

`_quote_floor` no longer exempts a *similarity range*. It exempts a **CLOSE
that touches exactly one word** — one word substituted for one word, or one
word added or dropped. The matcher supplies the count as
`QuoteVerification.altered_words`, persisted per quote in `quote_check`.
`FABRICATED`, and any `CLOSE` touching two or more words, floors.

This is the old `[0.75, 0.85)` band restated in terms of what actually
differs. The band was trying to say "this is transcription noise" and was
using a number that the 0.80 ceiling had made meaningless; two of the rows it
protected were quotes that are in fact verbatim, and it had no way to protect
`withers-38` from being lumped in with them.

**One word is where the question becomes unanswerable** — mostly.
`withers-21`'s `"or"` where the opinion says `"and"` and `wainwright-17`'s
inserted `"the"` are the only one-word `CLOSE`s in the 63 quotes, and both are
immaterial.

But not every lone word is a judgment call, and the first pass treated them as
if they were. Review produced a quote dropping `"not"` from "did not have
probable cause" and one turning `30 days` into `10 days`: one word each, and
each reverses or restates the holding. So the exemption never applies when an
altered word is a **negation, a modal, or carries a digit** (`_NEVER_EXEMPT`).
That also settles `shall` → `may`, which this doc previously called
unanswerable — it is not, because a modal is meaning-bearing wherever it
appears. What is left in the exemption is genuinely the noise band:
conjunctions, articles, prepositions.

What the exemption withholds is only the **automatic** Yellow, which is a
deterministic override of the agent's judgment. Everything else still fires:

* the `CLOSE` verdict and the named `alterations` stay on the claim,
* `_quote_alteration_lines` renders `Quote altered: or -> and` as an amber
  chip on the report card,
* `_triage_track_for` still routes any `CLOSE` to the **full** track,
* `tools/poor_mans_check.py` still blocks the claim from clearing.

So a one-word swap that *does* matter still reaches the agent and the reader.
The floor simply stops overriding them on the one case it cannot judge.

Legacy rows written before `altered_words` existed carry no count and floor on
any `CLOSE` — the conservative direction.

## Effect

Across the three frozen corpora and the live brief, 63 (quote, opinion) pairs:

| | before | after |
|---|---|---|
| VERBATIM | 30 | 43 |
| CLOSE | 20 | 9 |
| FABRICATED | 13 | 11 |

`matters/aliaj-rochelle-park` is a live MTD brief that quotes Iqbal and Twombly
heavily and accurately; 9 of its 23 quotes came back `CLOSE`. Six were the
ceiling — star pagination, an opening quote mark, an ellipsis, a bracket in the
reported text. Three are real, and one of those three (`-08`: the brief writes
`Fed R.Civ.P. 8` where the opinion says `Rule 8`) now floors where it did not
before. Net on a real brief: **six false flags removed, one true flag added.**

### Scoring baselines

| | before | after |
|---|---|---|
| withers v1 yellows caught | 14/19 | 14/19 |
| withers v1 greens exact / over-flagged | 9 / 2 | 9 / 2 |
| withers v2 yellows caught | 16/19 | 16/19 |
| withers v2 greens exact / over-flagged | 7 / 4 | 7 / 4 |
| A/B v1 (payne + wainwright) | 23 + 33 = 56/61 | **24 + 33 = 57/61** |
| A/B v2 | 23 + 32 = 55/61 | 23 + 32 = 55/61 |
| v1 lenient-direction errors | payne-03, payne-58 | **payne-03** |

Every baseline holds or improves. The two that move both come from one fix:
the payne and wainwright corpora carried quote columns frozen from their
source briefs, written before `quote_floor` existed, so six `FABRICATED`
quotes had never floored. `tests/build_assessment_corpora.py` now re-runs
`check_quotes` on every corpus, not just withers — that is payne 23 → 24 and
`payne-58` leaving the lenient set. It is independent of the matcher change.

`withers-21` and `wainwright-17` hold their old values under the lone-word
exemption. Flooring them instead — the first thing tried — costs withers
greens 9 → 8 exact, wainwright 33 → 32, and withers v2 greens 7 → 6. That
measurement is why the exemption is stated in words rather than dropped.

Two builder bugs were fixed on the way. `write_cassette` truncated the cassette
file, destroying the `assess-v2` verdicts — live recordings, not reproducible
from anything committed — on any rebuild; it now rewrites only the rows it
owns. And `check_quotes` ran on withers only.

## Per-quote diff

All 63 (quote, opinion) pairs. `**` marks a changed bucket. The floor column is
per *claim*, not per quote, so it repeats across a claim's quotes.

| claim | quote (truncated) | before | after | floor |
|---|---|---|---|---|
| withers/withers-04 | the excusable neglect standard is a strict one | CLOSE 0.64 | CLOSE 0.81 | Yellow |
| withers/withers-04 | inadvertence, ignorance of the rules, or mistakes  | FABRICATED 0.45 | FABRICATED 0.49 | Yellow |
| withers/withers-09 | judicial admissions | CLOSE 0.64 | CLOSE 0.64 | Yellow |
| withers/withers-10 | Admissions obtained under Rule 36, including those | FABRICATED 0.41 | FABRICATED 0.47 | Yellow |
| withers/withers-13 | not mere evidence; they are conclusive judicial ad | FABRICATED 0.47 | FABRICATED 0.47 | Yellow |
| withers/withers-14 | not mere evidence; they are conclusive judicial ad | FABRICATED 0.51 | FABRICATED 0.49 | Yellow |
| withers/withers-21 | mere delay does not alone constitute prejudice, | CLOSE 0.8 | **VERBATIM 1.0** | - |
| withers/withers-21 | in the loss of evidence, increased difficulties in | CLOSE 0.79 | CLOSE 0.98 | - |
| withers/withers-21 | defense sufficient to support a finding on the mer | VERBATIM 1.0 | VERBATIM 1.0 | - |
| withers/withers-30 | good cause | VERBATIM 1.0 | VERBATIM 1.0 | - |
| withers/withers-37 | It would be inconsistent with the purpose of the f | FABRICATED 0.4 | FABRICATED 0.4 | Yellow |
| withers/withers-38 | A genuine issue of material fact exists when the e | CLOSE 0.73 | CLOSE 0.85 | Yellow |
| withers/withers-45 | The statute of limitations for a breach of contrac | FABRICATED 0.53 | **CLOSE 0.69** | Yellow |
| payne/payne-12 | A charge on justification is proper where the defe | FABRICATED 0.41 | FABRICATED 0.42 | - -> Yellow |
| payne/payne-14 | [a]lthough the defendant requested a charge on cit | FABRICATED 0.38 | FABRICATED 0.41 | - -> Yellow |
| payne/payne-15 | [a] citizen’s arrest defense is not required when  | FABRICATED 0.48 | FABRICATED 0.48 | - -> Yellow |
| payne/payne-32 | hindsight has no place in an assessment of the per | VERBATIM 1.0 | VERBATIM 1.0 | - |
| payne/payne-33 | The fact that defendant and his present counsel di | VERBATIM 1.0 | VERBATIM 1.0 | - |
| payne/payne-34 | [t]rial counsel’s decisions relating to strategy a | VERBATIM 1.0 | VERBATIM 1.0 | - |
| payne/payne-38 | in his presence | VERBATIM 1.0 | VERBATIM 1.0 | - |
| payne/payne-38 | within his immediate knowledge | VERBATIM 1.0 | VERBATIM 1.0 | - |
| payne/payne-58 | but there must be a legitimate basis for the defen | FABRICATED 0.48 | FABRICATED 0.47 | - -> Yellow |
| payne/payne-59 | but there must be a legitimate basis for the defen | FABRICATED 0.48 | FABRICATED 0.48 | - -> Yellow |
| payne/payne-69 | thirteenth juror | VERBATIM 1.0 | VERBATIM 1.0 | - |
| payne/payne-73 | The grant or denial of a motion for new trial is a | VERBATIM 1.0 | VERBATIM 1.0 | - |
| payne/payne-77 | for the jury to determine the credibility of the w | VERBATIM 1.0 | VERBATIM 1.0 | - |
| payne/payne-79 | [t]he testimony of a single witness is generally s | FABRICATED 0.44 | FABRICATED 0.46 | - -> Yellow |
| wainwright/wainwright-02 | has the burden to show that he was harmed by that  | VERBATIM 1.0 | VERBATIM 1.0 | - |
| wainwright/wainwright-10 | there was a breakdown in the adversarial process | VERBATIM 1.0 | VERBATIM 1.0 | - |
| wainwright/wainwright-10 | counsel entirely fails to subject the prosecution' | CLOSE 0.8 | **VERBATIM 1.0** | - |
| wainwright/wainwright-12 | attorney's failure [to] be complete | CLOSE 0.8 | **VERBATIM 1.0** | - |
| wainwright/wainwright-14 | does not meet this stringent standard | VERBATIM 1.0 | VERBATIM 1.0 | - |
| wainwright/wainwright-16 | If an appellant fails to meet his or her burden of | FABRICATED 0.46 | **VERBATIM 1.0** | - |
| wainwright/wainwright-17 | upon a party's request, the trial court is require | CLOSE 0.8 | CLOSE 0.99 | - |
| wainwright/wainwright-21 | To authorize a requested jury instruction, there n | VERBATIM 1.0 | VERBATIM 1.0 | - |
| wainwright/wainwright-23 | A defendant is not entitled to a jury instruction  | VERBATIM 1.0 | VERBATIM 1.0 | - |
| wainwright/wainwright-25 | Indeed, it would turn the law on its head to allow | VERBATIM 1.0 | VERBATIM 1.0 | - |
| wainwright/wainwright-26 | may authorize the jury to find the defendant guilt | CLOSE 0.8 | **VERBATIM 1.0** | - |
| wainwright/wainwright-27 | Reluctance, or fighting to repel an unprovoked att | CLOSE 0.8 | **VERBATIM 1.0** | - |
| wainwright/wainwright-32 | counsel was subsequently disbarred does not itself | CLOSE 0.78 | **VERBATIM 1.0** | - |
| aliaj/aliaj-rochelle-park-01 | To survive a motion to dismiss, a complaint must c | CLOSE 0.79 | **VERBATIM 1.0** | - |
| aliaj/aliaj-rochelle-park-02 | state a claim to relief that is plausible on its f | VERBATIM 1.0 | VERBATIM 1.0 | - |
| aliaj/aliaj-rochelle-park-03 | A claim has facial plausibility when the plaintiff | VERBATIM 1.0 | VERBATIM 1.0 | - |
| aliaj/aliaj-rochelle-park-04 | more than a sheer possibility that a defendant has | VERBATIM 1.0 | VERBATIM 1.0 | - |
| aliaj/aliaj-rochelle-park-05 | a plaintiff's obligation to provide the grounds of | CLOSE 0.8 | **VERBATIM 1.0** | - |
| aliaj/aliaj-rochelle-park-06 | in the light most favorable to the plaintiff | VERBATIM 1.0 | VERBATIM 1.0 | - |
| aliaj/aliaj-rochelle-park-07 | However, a court need not credit either "bald asse | VERBATIM 1.0 | VERBATIM 1.0 | - |
| aliaj/aliaj-rochelle-park-08 | the factual detail in a complaint is so undevelope | CLOSE 0.77 | CLOSE 0.96 | - -> Yellow |
| aliaj/aliaj-rochelle-park-09 | Fed.R.Civ.P. 8(a)(2) requires a 'showing' rather t | CLOSE 0.71 | CLOSE 0.9 | Yellow |
| aliaj/aliaj-rochelle-park-10 | does not unlock the doors of discovery for a plain | CLOSE 0.8 | **VERBATIM 1.0** | - |
| aliaj/aliaj-rochelle-park-11 | A claim has facial plausibility when the plaintiff | VERBATIM 1.0 | VERBATIM 1.0 | - |
| aliaj/aliaj-rochelle-park-12 | raise a right to relief above the speculative leve | VERBATIM 1.0 | VERBATIM 1.0 | - |
| aliaj/aliaj-rochelle-park-13 | merely consistent with | VERBATIM 1.0 | VERBATIM 1.0 | - |
| aliaj/aliaj-rochelle-park-13 | stops short of the line between possibility and pl | CLOSE 0.8 | **VERBATIM 1.0** | - |
| aliaj/aliaj-rochelle-park-14 | well-pleaded facts do not permit the court to infe | CLOSE 0.69 | CLOSE 0.9 | Yellow |
| aliaj/aliaj-rochelle-park-15 | context-specific task that requires the reviewing  | VERBATIM 1.0 | VERBATIM 1.0 | - |
| aliaj/aliaj-rochelle-park-16 | are not bound to accept as true a legal conclusion | VERBATIM 1.0 | VERBATIM 1.0 | - |
| aliaj/aliaj-rochelle-park-19 | identify the exact contours of the underlying righ | VERBATIM 1.0 | VERBATIM 1.0 | - |
| aliaj/aliaj-rochelle-park-19 | determine whether the plaintiff has alleged a depr | CLOSE 0.8 | **VERBATIM 1.0** | - |
| aliaj/aliaj-rochelle-park-23 | objective reasonableness | VERBATIM 1.0 | VERBATIM 1.0 | - |
| aliaj/aliaj-rochelle-park-23 | subjective good faith | VERBATIM 1.0 | VERBATIM 1.0 | - |
| aliaj/aliaj-rochelle-park-33 | supervisory authority over all law enforcement act | VERBATIM 1.0 | VERBATIM 1.0 | - |
| aliaj/aliaj-rochelle-park-33 | county and municipal...police officers | CLOSE 0.8 | **VERBATIM 1.0** | - |

The nine surviving `CLOSE`s, what the matcher says differs, and how many
words that touches (`≤1` does not floor):

| claim | words | alteration(s) |
|---|---|---|
| withers-04 | 4 | `is a strict one -> set forth` |
| withers-09 | 2 | `judicial -> withdrawals of` |
| withers-21 | **1** | `or -> and` (exempt: conjunction) |
| withers-38 | 5 | `dropped: genuine issue of`; `exists when -> is genuine that is if` |
| withers-45 | 6 | `statute -> case`; `dropped: limitations for`; `claim begins to run -> the cause of action accrues` |
| wainwright-17 | **1** | `added: the` (exempt: article) |
| aliaj-08 | 4 | `fed r civ p -> rule` |
| aliaj-09 | 4 | `fed r civ p -> twombly rule` |
| aliaj-14 | 6 | `should be dismissed for failing to -> has alleged but it has not` |

`aliaj-08` and `aliaj-09` are the same substitution twice: the brief writes
`Fed R.Civ.P. 8` where Phillips says `Rule 8`. That is a citation-form
substitution inside a quotation, not a misrepresentation, and it floors. A
citation-alias junk rule would clear it, but aliasing reporter and rule names
is a real piece of work and nothing in the corpora needs it yet — noted, not
built.

## Downstream

* `tools/poor_mans_check.py` treated `CLOSE` as a blocker rather than a
  finding, explicitly because of this bug. A `CLOSE` touching more than one
  word is now a finding and the note names the altered words; a lone word
  stays a blocker, matching `_quote_floor`. Re-run live on Aliaj: REVIEW 21 →
  11, LOOKS OK 12 → 19, PROBLEM 1 → 4. Six claims stopped being blocked by a
  quote that was in fact verbatim; three real alterations became findings.
  (Jev's own scores drift by a mean 0.014 between runs, max 0.14 observed, so
  two unrelated rows moved as well — this checker is not bit-reproducible.)
* `proposition_pipeline._quote_alteration_lines` renders the alterations as
  amber flag chips on the report card, alongside the crosscheck flags.

## Known costs

`_locate` is the expensive part: one `SequenceMatcher` pass per window, ~7000
of them on a 100k-character opinion, about 4 seconds per quote that does not
exact-match. This is not new — the pre-change matcher was 5.6s on the same
input — and quotes that exact-match short-circuit in under a millisecond. Two
obvious speedups were measured and **both came out slower**: reusing one
matcher via `set_seq2(needle)` (the cost is the match algorithm, not building
the index) and pruning on `real_quick_ratio`/`quick_ratio` (both are
`2·min/(la+lb)`, which for a 1.5w chunk is exactly the 0.80 the real ratio is
capped at here, so the bound never prunes and only adds work). Anchoring with
`str.find` on distinctive slices of the needle would work, but it is a real
behavior change on a freshly recalibrated path, so it is noted, not done.

## Not done

The `opinion_block` verbatim check this work was originally for — confirming
that a passage an agent quotes back into the report is really in the opinion —
is still unbuilt. It now has the primitive it needed: `verify_quote` returns
`VERBATIM` for a span differing only in pagination and punctuation, which is
exactly the question that check asks.
