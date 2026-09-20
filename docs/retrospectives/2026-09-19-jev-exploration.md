# Retrospective: exploring TypeSafe's Jev model for citation checking

**Date:** 2026-09-19 (one session). **Total Jev spend:** about $0.35.
**Where things live:** ideas and their status in `scratch/jev/IDEAS.md`; next
step in `docs/plans/2026-09-19-jev-locator-assess-test.md`.

## What we set out to do

Rebecca got access to Jev and asked what people had done with it on citation
checking, then whether a rubric or "logic router" built on it could save money,
since it is so cheap.

## What we did, in order

1. **Research pass** (three Sonnet agents). Jev is not a coding agent: it is a
   classifier from TypeSafe AI (out of stealth 2026-09-15) that takes text plus
   typed questions and returns probabilities. jevcode.ai is a community mirror,
   not the vendor. Public citation-checking work is thin: TypeSafe's cookbook
   (quote match, then one support question, 0.8 confidence gate), Isaac Flath's
   post, one working tool for academic papers (Paper Trellis), and one
   speculative legal essay (Seth Chandler). Nobody had built a legal one. Both
   published recipes skip the existence check our CourtListener step does.
   -> `scratch/jev_citation_checking_research.md` (`e1a558f`)
2. **Eight ideas, ranked.** -> same file, §5; now tracked in `scratch/jev/IDEAS.md`.
3. **Detour: "fix the gap first."** Paper Trellis's rule ("code proves every
   quote exists") exposed that our report's green opinion box is never checked
   against the opinion. Measuring recorded quotes to design that check exposed
   two bugs in the core quote matcher:
   - the opinion text never got smart-quote straightening — **fixed** (`e89783b`;
     42 of 106 recorded quote segments matched exactly before, 78 after);
   - the fuzzy path cannot score above 0.80, so a verbatim quote with a
     page-number marker in it is recorded as FABRICATED, and the documented
     "0.75–0.85 noise band" was calibrated on that artifact — **not fixed**, needs
     a decision. -> `scratch/TODO.md` top item (`2f0d095`)
   We parked it and went back to Jev.
4. **Three small tests on the frozen corpora** (`1fe7144`):
   rubric (7 questions, 0.19 s and $0.00025 per claim); auto-Green gate (strict
   threshold clean, loose threshold looked fine in-sample); passage locator
   (right passage in the top 5 for 50 of 52 claims; 27% of the opinion's size).
   A held-out run on `matters/payne` showed the loose threshold waving through
   7 "partial" claims. -> `scratch/jev/RESULTS.md`
5. **Four unseen briefs** (after Rebecca confirmed they are public filings):
   strict gate cleared 22 of 97 claims, 0 bad (`bdc971f`). They became the lockbox.
6. **Phrasing loop** (one Sonnet agent surveyed optimization-loop practice first).
   Built a question bank plus a free combiner search with a pessimistic
   acceptance rule. Three rounds, 23 new probes. Diagnosis: every blocking claim
   was a compound proposition with one unsupported clause. Winner: "Is the
   proposition, exactly as written, fully supported?" Tuning data 38 -> 49
   cleared; lockbox 22 -> 30 cleared, 0 bad, but the nearest bad claim scored
   0.250 against a 0.255 threshold. -> `scratch/jev/LOOP.md` (`1f9d8ba`, `2e1f7a0`)
7. **Shadow logging shipped** (`aaa7db7`): `src/citation_verifier/jev_shadow.py`,
   the `jev-shadow` verb, opt-in inside `full` via `JEV_SHADOW=1`, 19 tests,
   `tools/jev_shadow_report.py`, backfilled on six prior runs (202 claims).
   Writes one file per run and changes nothing else.
8. **Explainer page** for peers: `scratch/jev/jev-question-loops.html` (`450af9f`).
9. **Handoff plan** for the next test (`82e4201`).

## What we learned

- **Cost stops mattering; accuracy is the whole question.** A 30-claim brief
  costs about a penny and 11 seconds in Jev, against $0.36–0.49 and 26–41 s per
  claim for the recorded Opus runs.
- **Jev is reliable at the extremes and unreliable in the middle.** On held-out
  data every claim scoring 0.95+ was supported and none scoring under 0.50 was.
  The middle is where "partial" (the brief overstates the case) lives.
- **The deterministic quote check and Jev cover for each other.** The claims that
  fooled Jev worst all had quote problems, so eligibility removed them first.
- **Located passages beat the whole opinion** as Jev's input, matching TypeSafe's
  documented "context rot".
- **Wording matters, in a specific direction:** ask whether every part *as
  written* is supported, not whether the opinion "supports" it.
- **In-sample numbers lied twice.** A hand-picked 0.50 threshold and then a
  loop-tuned 0.255 threshold both looked clean on their own data and were unsafe
  or razor-thin on unseen briefs. Most single-question gains did not transfer
  (best probe: 0.939 on tuning data, 0.825 on unseen briefs).
- **What the rewording actually bought is margin.** At thresholds with real
  headroom both wordings clear about the same number of claims.
- **The binding constraint is labelled not-supported claims from new briefs**,
  not wording. Zero bad clears out of 21 still allows a true rate near 14%.

## Decisions made

- Jev only clears Greens or escalates; the LLM never sees Jev's answers.
- The gate stays in shadow until about 100 fresh eligible not-supported claims
  show zero bad clears at 0.50 (rule in `scratch/jev/IDEAS.md`).
- `JEV_SHADOW=1` was added to this machine's `.env`. It is per-machine and
  gitignored: **add it on the other computers too**, and set it to 0 for any
  non-public document.
- Next experiment: the locator feeding the LLM assessment, in a fresh session.

## Open items

- **Quote-matcher ceiling bug** — decision needed: diff-based redesign
  (recommended) vs retuning two thresholds. Blocks the `opinion_block` proof
  check and ideas D and E; makes gate eligibility stricter than it should be.
- `typesafe-sdk` is an optional extra (`pip install -e ".[jev]"`): install it on
  the other machines before expecting shadow logs there.
- The lockbox is spent. Any further rubric tuning needs briefs nobody has seen.
- TypeSafe's terms reportedly allow disclosure of undefined "objectionable
  content" (Chandler). Not verified by us. Read them before sending anything
  that is not already public.

## Process notes

- Parallel research agents worked well; their fetch tool summarizes pages, so
  exact limits and SDK calls were re-checked against raw docs
  (`docs.typesafe.ai/<path>.md`) before writing code.
- One agent's completion notice arrived before its report did; waiting for the
  report rather than guessing was the right call.
- Caching every Jev response to `scratch/jev/cache.json` made every rerun free
  and let the results sync across machines.
- The pessimistic acceptance rule and the untouched lockbox did their job: they
  are the reason the write-up says "real but thin" instead of "38 -> 49".
