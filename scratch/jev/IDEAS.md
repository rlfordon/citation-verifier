# Jev ideas — living backlog

Last updated 2026-09-19. **This file is the current status of every idea.** The
original reasoning for ideas A–H is in `../jev_citation_checking_research.md` §5
(its ranking table is a snapshot from before any experiments). Session record:
`docs/retrospectives/2026-09-19-jev-exploration.md`.

Jev = TypeSafe AI's `jev-1.13.0`: a fast, cheap classifier (typed questions in,
probabilities out; ~0.2 s and ~$0.0003 per request). It never writes prose, so
an LLM still authors every finding that appears in a report.

Standing rules for anything built on it:
- Jev only clears Greens or escalates. It never issues a Yellow or Red.
- The LLM never sees Jev's answers (the Haiku prescreen failed by biasing Opus).
- Counting, dates, numbers, and pincites stay in code.
- Bump `RUBRIC_VERSION` whenever questions, state layout, or excerpting change.
- Sends text to a third party: `JEV_SHADOW=0` for non-public documents.

## Status at a glance

| # | Idea | Status | Next action |
|---|---|---|---|
| A | Decomposed support rubric | **Done** — measured, then reworded by the phrasing loop | none |
| B | Auto-Green gate | **Measured; running in shadow** | turn on when the trigger below is met |
| C | Passage locator | **Measured** (96% top-5). Feeding it to the LLM assessment was **designed, costed, and parked without a paid run** -- best case saves 36% (~$0.86 a brief), ~$0.40 after any fallback | none -- see `LOCATOR_ASSESS.md` for the reopen conditions |
| D | Quote-alteration adjudicator | Not started — **blocked** on the quote-matcher fix | decide the matcher fix (`../TODO.md`, top item) |
| E | Second reader on Opus's proven passage | Not started — **blocked** on the `opinion_block` proof check, which needs the matcher fix | same |
| F | Wrong-document / topic tripwire | Not built; partly answered by data (see I) | fold into I |
| G | Extraction checker | Not started | needs labelled extraction data first |
| H | Poor-man's citator | Not started | park; new feature with its own labelling problem |
| I | Low-score triage | **New** — evidence in hand (more on 2026-09-19: see below), not built | cheap follow-on to B |
| J | Full-opinion fallback rule | **Parked with C** — costed offline: every rule gives back most of the saving, two go negative | none |
| K | Templated Green cards | **New** — needed before B can switch on | design with B |
| L | Per-clause decomposition in code | **New** — not tried | candidate for the next rubric version |
| M | Self-consistency across state framings | **New** — found by the loop, in use in `gate_v1` | watch in shadow data |
| N | Structured criteria (`what` / `not_for`) | **New** — one probe tried, held up on unseen briefs | candidate for the next rubric version |
| O | Pack all of an opinion's claims into one request | **New** — not done (cost already negligible) | only if latency matters |
| P | Shadow pool as the evaluation set | **Built** — logging on every run | re-run the report as runs accumulate |
| Q | Jev as a regression judge for prompt versions | **New** — not tried | consider after C |
| R | Bulk screening (whole dockets, the 525-citation set) | **New** — not tried | needs B trusted first |

## B. Auto-Green gate — what is known, and the switch-on rule

A claim is *eligible* only with a clean `VERIFIED`, no quote problem, no
crosscheck flag. Gate score `gate_v1` = mean of "Is `proposition`, exactly as
written, fully supported by `opinion`?" asked under two state layouts.

- Original wording at 0.90: 36 claims cleared across five prior runs, 0 bad.
  Every bad clear a looser threshold ever made was a "partial" claim.
- Reworded (`gate_v1`): pooled over six runs (132 eligible, 59 not supported),
  0.50 clears 38 with 0 bad; the worst not-supported claim scores 0.295. The
  original wording makes 13 bad clears at 0.50. The rewording bought **margin**,
  not much extra coverage.
- None of that data is fresh — it was all tuning data or the one-shot lockbox.
- Expected saving once on: ~15–25% of LLM assessment work on a weak brief, up to
  ~45% on a clean one.

**Switch-on trigger:** `tools/jev_shadow_report.py` shows about 100 gate-eligible
not-supported claims from runs dated after 2026-09-19, with zero bad clears at a
0.50 threshold (bounds the true bad-clear rate near 3%). Until then it costs
about a penny per brief to keep logging. Needs K first.

## I. Low-score triage (new)

On held-out `matters/payne`, **none of the 21 eligible claims scoring under 0.50
on the original gate was supported** (7 partial, 14 unsupported). Jev is reliable
at both ends and unreliable in the middle. A low score could route a claim to the
`full` triage track, order the report's review queue, or serve as F's tripwire
for a wrong opinion file. It saves nothing; it aims effort. Never a verdict.

More evidence, 2026-09-19 (`LOCATOR_ASSESS.md`): on the three frozen claim sets
the locator's `exists` answer ("does any passage state this?") is 0.23 or lower
for **all 13** claims Opus called unsupported. One-sided: 11 of 61 supported
claims also score under 0.5. In-sample data, so a lead, not a result.

**As a second look at Opus's Greens (quick offline check, 2026-09-19, $0).** Of
the 61 claims the frozen assess-v2 run called "supported", 9 carry a human label
that is not green. Six of those already turn Yellow through the quote check, so
the real misses are withers-32, -33, -49. The original gate score (`gate_v0`,
top-5 passages) is 0.90, 0.47, 0.40: "re-read when under 0.5" catches 2 of 3, and
would also re-read 6 of the 52 correctly-Green claims (about 12% more Opus
work). Against the July API run it catches 3 of 4 real misses (adds withers-12 at
0.25). Tiny numbers, in-sample, and 0.47 against a 0.50 line is the same
razor-thin margin that fooled us twice -- a lead only. Testing it needs no new
code path: the shadow log already records the score on every run; what is
missing is a report section listing "Opus said supported, Jev scored low" for a
human to check on fresh briefs. This is a reason to keep Jev that does not
depend on cost -- on the API path a brief costs about $2.38 ($1.19 batched), so
every cost-saving idea here is now worth well under a dollar a brief.

## J. Full-opinion fallback rule (new)

If the LLM reads located passages instead of the whole opinion, findings that
depend on a topic being *absent* are at risk (a missed passage looks like "not
supported"). Candidate rules, to be measured by C's test: fall back to the whole
opinion when the LLM answers `unverifiable`; or also on `unsupported`; or when
the locator's "does any passage state this?" answer is low.

**Parked 2026-09-19 without a paid run** (`LOCATOR_ASSESS.md`). Input is only
about half of an API-path assessment's cost, so excerpts save at most 36%
($0.86 per 30-claim brief). Priced offline: re-read when `exists` < 0.5 costs
10% MORE than today; routing those claims straight to the full opinion saves
19%; re-read on `unverifiable` saves about 17%. The Batches API saves 50% with
no accuracy risk. Reopen for much longer opinions or bulk screening (R).

## K. Templated Green cards (new)

A claim the gate clears gets no LLM prose. The Green card can be built from
data already in hand: `brief_sentence` for the brief box, the top located passage
for the opinion box, a fixed one-line analysis. Must be visibly marked as
machine-cleared in the report and in `assessed_by`. Prerequisite for B.

## L. Per-clause decomposition in code (new)

The loop's diagnosis: every blocking claim was a **compound proposition** where
the case supports the main point but not one clause. TypeSafe's own advice for
counting is "iterate in code, ask one question per item". Split the proposition
into clauses in code and ask one `Noul` per clause; gate on the minimum. The
crude versions tried ("first half" / "last half") were weak (`first_part` scored
0.684) — clause splitting needs to be real, not positional.

## M. Self-consistency across state framings (new)

The loop's winning combiner averages the *same question* asked with and without
`brief_sentence` in the state. Disagreement between the two framings is itself a
confidence signal. TypeSafe has two cookbooks on this pattern. Caveat found the
same day: one claim scored 0.250 in the loop and 0.295 in production because the
sibling questions in the request differed — answers are not fully independent of
what else is asked.

## N. Structured criteria (new)

TypeSafe documents JSON criteria with `what` / `not_for` / `examples` fields as a
lever for boundary cases. One probe tried (`relation_structured`): 0.905 on the
tuning data and **0.946 on unseen briefs**, one of only three probes whose gain
carried over (with `coverage` and `own_words`).

## Non-Jev findings from this work (tracked in `../TODO.md`)

- **Quote matcher:** the fuzzy path cannot score above 0.80, so a verbatim quote
  with a page-number marker in it is reported as fabricated. Smart-quote half
  fixed (`e89783b`); the ceiling fix needs a decision (diff-based redesign vs
  threshold retune). Blocks D and E, and makes B's eligibility too strict.
- **`opinion_block` is asserted, not proven:** `run_apply_assessments` writes the
  agent's quoted opinion text into the report without checking it appears in the
  opinion. Borrowed from Paper Trellis's "proof, not assertion" rule.
