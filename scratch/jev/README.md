# Jev experiments — three small tests

**Start here:** `IDEAS.md` (status of every idea) · `RESULTS.md` (tests 1-4) ·
`LOOP.md` (phrasing loop + shadow logging) · `LOCATOR_ASSESS.md` (test 5: locator
feeding the LLM assessment — costed, not run) · `jev-question-loops.html` (explainer).

Background and the idea list: `../jev_citation_checking_research.md`.
Goal: **try it, measure Jev's cost/time, estimate what it could save** — not a
model bake-off. Data: the three frozen corpora in
`tests/data/assessment_corpora/` (95 claims with human labels: 61 green,
34 yellow/red; all public court documents). Model pinned to `jev-1.13.0`.

Every API response is cached in `cache.json` (keyed by a hash of model + state
+ questions), so reruns are free and reproducible. Delete the file to go live.
Needs `TYPESAFE_API_KEY` in `.env` and `pip install typesafe-sdk`.

## Test 1 — shadow rubric (`test1_rubric.py`)
One request per claim. State = `{proposition, brief_sentence, opinion}` (whole
opinion; all 79 fit the 32K-token cap). Seven atomic questions in one call
(TypeSafe: extra questions add no latency). Nothing is decided here — it just
records every answer plus latency and tokens to `results/rubric_full.csv`.

**Measures:** seconds and dollars per claim and per brief; how well each
question separates human-green from human-not-green.

## Test 2 — auto-Green gate (`test2_gate.py`)
No new API calls — analysis over Test 1's answers. A claim is *eligible* only if
`cl_status == VERIFIED`, it has an opinion file, and its quote check is
`VERBATIM` or `NO_QUOTES` (strict on purpose: the quote matcher has a known
ceiling bug, see `../TODO.md`). Among eligible claims, sweep gate rules and
thresholds and report, for each: **claims cleared** vs **bad clears** (a
human-yellow/red claim waved through — the error that matters).

Honesty check: tuning and scoring on the same 95 claims flatters the gate, so
the script also does leave-one-corpus-out (pick the threshold on two corpora,
apply it to the third).

**Measures:** clearance rate at zero bad clears -> share of Opus jobs skipped
-> dollars and minutes saved, using `elapsed_s`/`cost_usd` from the recorded
cassettes and prior runs' `run.json`.

## Test 3 — passage locator (`test3_locator.py`)
One request per claim, following TypeSafe's "line-by-line search" cookbook:
split the opinion into <= 250 ID-tagged passages, one `Choice` over the IDs
ranks them, one `Noul` asks whether any passage addresses the proposition.

Reference answer (no new labelling needed): the passage(s) where the recorded
Opus `opinion_block` quote actually sits, found by exact string match.

**Measures:** hit@1 / hit@3 / hit@5; how many tokens a top-5-passages prompt
would be vs the whole opinion (the Opus input-token saving); and whether the
`exists` Noul is itself a useful gate signal.

## Test 1b — rubric on focused passages (`test1_rubric.py --focused`)
Re-runs the Test 1 rubric with state = top-5 located passages (plus one
neighbour each side) instead of the whole opinion. TypeSafe documents that
accuracy falls as irrelevant state grows ("context rot"); this checks whether
the locator buys the gate a better clearance rate.

## Design rules carried over from the research
- Jev only ever clears Greens or escalates. It never issues a Yellow/Red.
- Opus never sees Jev's answers (the Haiku prescreen failed by biasing Opus).
- Counting, dates, numbers, pincites stay in code (documented Jev weak spots).
- Questions are literal and atomic; combination logic lives in Python.
