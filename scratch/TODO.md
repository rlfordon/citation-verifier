# TODO

> **2026-06-10 — accuracy work status lives in
> [`docs/plans/2026-06-10-prioritized-roadmap.md`](../docs/plans/2026-06-10-prioritized-roadmap.md).**
> Tier 1 false-positive work is **done**: fake corpus 0/19, published reals
> 203/204, fallback reals 32/32, all replayable offline (cassette harness).
> The "Citation mismatch detection (Check Cite)" item below is Tier 1 Step 3,
> still open. New follow-ups that emerged during execution (parallel-citation
> robustness, triaging 18 fallback NOT_FOUNDs, pinning the other state-leak
> reproducers, Charlotin fake-mining, threshold calibration) are in that
> roadmap's "Follow-ups discovered during execution" section.

## OPEN 2026-09-19 — quote matcher fuzzy path is capped at 0.80 (decision needed)

Found while trying to add a verbatim check on agent-written `opinion_block`
(background: `scratch/jev_citation_checking_research.md` §3).

**Fixed (commit `e89783b`):** the opinion text never got smart-quote
straightening (the quote did), so quotes with apostrophes missed the exact path.

**Still open — `quote_matcher._best_match_with_passage`:** the sliding window
compares the quote (length w) against a chunk of length 1.5w, so
`SequenceMatcher.ratio()` tops out at 2w/2.5w = **0.80**. Consequences:
- `VERBATIM` (>0.85) is reachable ONLY by exact substring; `if best > 0.95: break`
  is dead code. A one-character difference in a 70-char quote scores 0.79.
- **False "fabricated":** `wainwright-16` is verbatim except for a star-pagination
  marker (`*534`) mid-sentence; it is recorded FABRICATED at 0.46. A same-length
  window + step-1 refinement scores it 0.97.
- The `[0.75, 0.85)` "transcription-noise band" in `_quote_floor` was calibrated
  on this artifact: the true green that motivated it (`withers-21`, 0.79/0.80)
  scores 0.97/0.98 on a corrected scale.

**Why it is not a drop-in fix:** on a corrected scale, real misquotes move up too
(`withers-04` 0.64 -> 0.76, `withers-38` 0.73 -> 0.83 — both would slip over the
0.75 floor cutoff), and a single meaning-changing word swap in a long quote
("shall" -> "may") scores ~0.96, which the 0.85 cut would call VERBATIM. In the
three frozen corpora the corrected scale separates cleanly (true verbatims
>= 0.88, real alterations <= 0.83), but a ratio threshold alone cannot tell
"verbatim plus pagination junk" from "one word changed".

**Proposed:** bucket on WHAT differs after alignment, not on the ratio: only
junk differs (star pagination, footnote markers, punctuation, hyphenation,
bracketed alterations) -> VERBATIM; any word substituted/added/dropped -> CLOSE;
poor alignment -> FABRICATED. Then drop the noise band (every CLOSE floors) and
re-record the corpora `quote_check` columns + regression baselines. The
`opinion_block` verbatim check gets built on top of this.

## Hand-off — contribute DE-279 to lq-ai (case citation validation)

> **Filed 2026-06-19 as [lq-ai#173](https://github.com/LegalQuants/lq-ai/issues/173).** Awaiting maintainer response on the 3 open questions; the plan below is ready to execute once a shape is greenlit.

Port our name-match verification into [LegalQuants/lq-ai](https://github.com/LegalQuants/lq-ai)
as their unbuilt **DE-279** (catches the name-swap fabrication their thin
`verify_citations` tool misses). Background + ranked PR map:
[`docs/lq-ai-comparison.md`](../docs/lq-ai-comparison.md). Execution-ready plan
(PR-A, 6 TDD tasks, verified against lq-ai main 2026-06-18):
[`docs/plans/2026-06-18-de279-lq-ai-case-resolver-port.md`](../docs/plans/2026-06-18-de279-lq-ai-case-resolver-port.md).

**Run it in a NEW session, rooted in an lq-ai checkout — not this repo.**

Prerequisites (gotchas found 2026-06-18):
- **`C:\Users\Rebecca Fordon\Projects\lq-ai` is our ONLY checkout, and it's STALE** —
  pinned at M2/M3-kickoff (`7b20746`, `EXPECTED_PATHS=73`); it predates the whole
  CourtListener/research subsystem DE-279 builds on. (The `~/Code/lq-ai` in lq-ai's own
  session-handoff docs is the lq-ai *maintainers'* path, NOT ours.) It must be reset to
  current upstream `main` (then `EXPECTED_PATHS` becomes **127** → 128 after the new route).
- **Fork-and-PR only — no push access** (outside contributor; can't self-merge). The
  plan's Step 0 repoints remotes so `origin` = your fork (`rlfordon/lq-ai`, the only push
  target) and `upstream` = `legalquants/lq-ai` (read-only fetch + the PR). Never pushes to lq-ai.
- **Windows box, Unix-oriented project**: venv paths are `.venv\Scripts\...`; set the
  test DB var with `$env:DATABASE_URL='...'`. Surface Windows build friction rather
  than forcing through. Docker is available for the pgvector test DB.
- **Env not set up here**: docker IS available (v29.4.3); create the api venv
  (`cd api && python -m venv .venv && .venv/bin/pip install -e ".[dev]"`) and run a
  throwaway pgvector: `docker run -d --name lq-test-pg -p 15433:5432 -e POSTGRES_USER=lq_ai
  -e POSTGRES_PASSWORD=test -e POSTGRES_DB=lq_ai pgvector/pgvector:pg16`.

Branch `feat/de-279-case-resolver`; api-only (don't touch `gateway/**`) = lightest
review path, but a maintainer merges. PR vs `LegalQuants/lq-ai` from the fork.
**The full copy-paste kickoff prompt now lives in the plan's "Kickoff prompt" section.**

## PDF Download Feature (in progress)

Added checkboxes + "Download PDFs" button to main page (`localhost:8000`). Backend resolves `matched_url` to downloadable PDF, bundles into zip.

### What works
- Frontend: checkboxes, select-all, download button with count, progress status
- RECAP documents: API -> `filepath_local` -> `storage.courtlistener.com` download (works)
- Opinion PDFs: resolves via cluster API -> sub_opinions -> opinion API -> `local_path` (pdf/) or `download_url`
- Skip feedback: UI shows "Downloaded N PDFs (M skipped -- no PDF available)" via X-Downloaded/X-Skipped headers

### Remaining issues
1. ~~**CloudFront WAF blocks server-side opinion PDF downloads.**~~ FIXED. Now resolves through API instead of appending `/pdf/`.
2. ~~**Opinion API resolution needs work.**~~ FIXED. Strategy: `local_path` starting with `pdf/` -> storage URL; else `download_url` (filtering .html/.htm/.xml). Many older opinions (Kitchen v. Herbert, MacPherson, Youngstown) have no PDF available — returns None gracefully.
3. ~~**Rate limiting for PDF resolution.**~~ FIXED. Both phases now use `asyncio.gather` with bounded concurrency: resolution goes through the client's existing rate limiter (0.5s interval + semaphore=5), downloads use a separate semaphore(5) for storage.courtlistener.com.
4. ~~**No user feedback when PDFs are skipped.**~~ FIXED. UI now shows downloaded vs skipped counts.

### Files modified
- `src/citation_verifier/client.py` — `get_pdf_url()`, `_first_recap_doc_url()` on `AsyncCourtListenerClient`
- `web/app.py` — `POST /api/download-pdfs` endpoint, `_sanitize_filename()`
- `web/static/index.html` — checkbox column, select-all, Download PDFs button + JS (now the Debug page at `/debug`)

## ~~Priority 0 — Tech debt~~ DONE

### ~~Deduplicate verify() and verify_async()~~ DONE
Extracted 7 shared helpers (`_process_citation_lookup_hit`, `_check_adjacent_page_cluster`, `_build_search_params`, `_build_fallback_result`, `_docket_date_ranges`, `_extract_docket_entry_docs`, `_has_recap_date_match`). Sync/async methods are now thin I/O wrappers calling shared logic. 1682 -> 1496 lines. Fixed 2 async parity test failures caused by the prior drift.

## Priority 1 — Bugs (wrong results)

### ~~Lever 2 false-negative cost~~ RULED + PARTIALLY FIXED (2026-06-11)
Ruling (Check Cite design §6): **refine via levers (a)+(b), reject (c).**

- **`verified-rule-25d-viken-detection`** — **FIXED.** The fix is *not* lever
  (a1) (cite-corroboration-skips-penalty — Viken's CL record is a caption with
  no matching cite, nothing to corroborate) but **lever (a2)**, a
  placeholder-party waiver: a cited `Doe`/`Roe` defendant carries no identity,
  so it's subtracted from the party-overlap check and the distinctive named
  plaintiff anchors. Live-confirmed VERIFIED 0.58 → "Viken Detection Corp. v.
  Bradshaw" + `cite_not_on_record`. (a2) narrowed to **defendant-position
  placeholders only** after the Charlotin replay caught `Doe v. Northrop
  Grumman → Barker v. Northrop Grumman`; (a1) narrowed to **cite-only** after
  it caught `Lee v. United States, No. 1:23-cv-84 → MOTE` (docket numbers
  aren't unique; the docket-search path is circular).
- **`verified-sundown-energy-fallback`** — **PARTIAL, still NOT_FOUND.** Lever
  (b) fixed the `_DOCKET_JUNK` parse bug (", No. 3" survives in the name, no
  phantom `docket_number=3`). But two independent residual causes remain (see
  the Priority 2 follow-up below): CL opinion search returns 0 for the full
  punctuated query, and the real cluster (4872528, Tex. 2021) has empty CL
  citations + a 14-party caption. Fixture stays red (expected VERIFIED, the
  honest ideal) per the scope guard.

Both fixtures carry `tier1_ruling` notes in `refactor_corpus.json`. Retro:
`docs/retrospectives/2026-06-11-check-cite-cite-unconfirmed.md`.

### Phase 3 Task 4: narrow VIA_RECAP false-positive on "Motion for ... Opinion" descriptions
Filed by Task 4 code-quality review (2026-05-22). `_recap_doc_is_cited_opinion` removed `"motion for"` from `_PROCEDURAL_KEYWORDS` because it would block legitimate `"OPINION on motion for reconsideration"` (false negative). Reciprocal gap: a PACER doc described as `"MOTION FOR RECONSIDERATION OF OPINION"` now matches the `"opinion"` keyword and the procedural filter doesn't fire — yielding false VIA_RECAP. In practice these descriptions exist but are uncommon, and the date ±14 day gate provides a partial backstop. Hardening option: add `"motion for reconsideration"` as a specific compound to `_PROCEDURAL_KEYWORDS`, or anchor `"motion for"` to start-of-description. Phase 4 or later.

### ~~Citation mismatch detection ("Check Cite" status)~~ DONE (2026-06-11)
Shipped as the new **`CITE_UNCONFIRMED`** status (UI "Check Cite"). A fallback
name-search win whose cited reporter/WL location is **contradicted** by CL's
*same-reporter-family* records (`cite_contradicted`), or backed by no text at all —
a bare RECAP docket (`cite_not_on_record`), demotes from VERIFIED-family. The
same-family witness rule (N.E.2d ≡ N.E.3d; U.S. ≠ S. Ct.) spares real cases CL
indexes under only one parallel reporter (the reporter-gap compensation the user
asked for — those stay VERIFIED + `cite_not_on_record` warning). The Drywall example
(`742 F. Supp. 2d 672` cited; case at `759 F. Supp. 2d 822`) is now Check Cite. The
three UI states landed: **Ready** = VERIFIED-family, **Check Cite** = CITE_UNCONFIRMED,
**Review Name** = VERIFIED_PARTIAL+`name_unverified` / WRONG_CASE. Charlotin FPs
67→33, 34 reclassified, zero new found; 600 offline passed. Design
`docs/plans/2026-06-11-check-cite-design.md`; retro
`docs/retrospectives/2026-06-11-check-cite-cite-unconfirmed.md`.

### INSUFFICIENT_DATA: skip verification entirely
When both court and date are missing, return a new `INSUFFICIENT_DATA` status immediately — no API calls. Court-only-missing is also borderline ("In re Wright" with just a year is too weak). WL citation "years" come from the volume number, not a court parenthetical — weaker evidence.
- Example: In re Marriage of Schultz, 2019 WL 5089859 (2015) — parser dropped court and case number, got year wrong (2015 vs actual). Matched wrong case in wrong court (0.59).

### State court RECAP leak (partially fixed)
`is_federal_court()` now gates RECAP API calls, but leaks remain:
- Oddi-Sampson v. State, 201 N.E.3d 749 (Ind. 2022) — court `ind`, matched Garner v. Biden (0.27), completely unrelated federal docket. `is_federal_court("ind")` returns False, so gate should work — needs investigation.
- Reinlasoder v. City of Billings, 455 P.3d 477 (Mont. 2020) — court `mont`, matched Wilson v. Mr. Bludsworth (0.27) via RECAP. Source: Thornton_v._Flathead_County_USA_23_January_2026.pdf.

### Cross-state opinion match (Graves)
Graves v. State, 773 N.E.2d 157 (Ind. 2002) — matched Graves v. State in a *different state* (0.70). Not a RECAP leak (matched via opinion search), but a cross-state match problem: state mismatch should disqualify results. https://www.courtlistener.com/opinion/1613021/graves-v-state/

### False positives: hallucinations scoring POSSIBLE_MATCH
Confirmed hallucinations that scored above NOT_FOUND threshold:
- Thompson v. Best (0.62) — matched Thompson v. Thompson in wrong court. Common surname inflates score.
- Lopez v. Bank of Am., N.A. (0.65) — docket and date mismatch should push below threshold. https://www.courtlistener.com/docket/5887170/85/lopez-v-bank-of-america-na/
- Benjamin v. Costco (0.53) — matched In re Monogioudis (completely wrong)
- In re Hudson (0.50) — matched In re: Hudson (different case, date off by centuries)
- Pointe Wholesale v. Vilardi (0.47), South Pointe Wholesale v. Vilardi (0.41), Reed v. ZipRecruiter (0.43), Mavy v. Comm'n of Soc. Security (0.42), Mungo v. State (0.51)
- Marion v. Hollis Cobb Assocs. (0.41) — wrong name, wrong date, should be well below threshold. https://www.courtlistener.com/docket/69958782/15/winer-v-mohammad/

Consider penalizing more when the distinctive party (defendant) doesn't match at all.

### Court ID mismatch: dccrimct vs dc
Weatherly v. Second Nw. Coop. Homes Ass'n scored only 53% partly because eyecite returned `dccrimct` but CL has `dc`. Either treat these as equivalent in court map, or fall back to prefix/substring match.
- Francis v. Rehman, 110 A.3d 615 (dccrimct 2015) — NOT_FOUND despite exact reporter match, same root cause. https://www.courtlistener.com/opinion/2782310/michael-francis-and-queue-llc-v-munir-rehman-and-hak-llc/

## Priority 0 — SDK billing transition (metered path BUILT 2026-07-01; validation pending)

The `AgentSDKExecutor` runs headless via the Claude CLI's **subscription
auth** (no per-token bill) — that's how the Step 8 re-record, the kettering
v2 re-run, and the sonnet-v2 A/B all ran for free. **Subscription billing
for the SDK goes away ~2026-06-15 (≈2 days from 2026-06-13, per user).**
After that, the free headless path is gone; runs must go through a metered
transport. Get the efficient metered path in place BEFORE the deadline.

**Efficiency plan (the metered path the design always intended — §5
"MessagesAPIExecutor, build last, optional" — now promoted to urgent):**
1. ✅ **BUILT 2026-07-01** — `MessagesAPIExecutor` in `executor.py`
   (plan: `docs/plans/2026-07-01-messages-api-executor-plan.md`): opinion
   text inlined, PDFs as document blocks, concurrent streaming calls,
   `--executor api` wired in the CLI + A/B harness. Offline-tested
   (tests/test_messages_api_executor.py); **LIVE VALIDATION PASSED
   2026-07-01** — `opus-v2-api` matched the same-day opus-v2 SDK control
   within sampling variance (withers 15/19 vs 14, reds 3/3, A/B 55/61,
   no new lenient regression); $0.079/claim, ~2.5 min for 90 jobs vs the
   SDK's ~$0.42/claim + hours. Full table in the plan's Results section;
   snapshots in `scratch/ab_runs/`. Billing update (user, 2026-07-01):
   SDK subscription coverage was temporarily extended; SDK stays the
   headless default until it starts drawing subscription credits, then
   flip to api (plan Step 4 — not taken yet).
2. ✅ **BUILT 2026-07-01** — `batch=True` mode on MessagesAPIExecutor
   (`--batch` in the CLI): one Message Batch, 50% off, polled. Same
   pending validation as item 1.
3. ~~Prompt caching~~ **DROPPED (cost-audit F7, 2026-07-01):** the
   template is ~1.4K tokens — below Opus 4.8's 4,096-token minimum
   cacheable prefix, so the marker would silently no-op; per-opinion
   packing already captures the shareable-context win.
4. ✅ **Prereq DONE:** `ANTHROPIC_API_KEY` is in `.env` (confirmed
   2026-07-02); the opus-v2-api validation arm ran green (item 1).
Cost context (measured 2026-06-13): one Opus v2 kettering run ≈ $13 via
SDK-notional/API rates, ~$6.50 with Batches; Sonnet several-fold cheaper
(sonnet-v2 A/B running now will give the exact figure + accuracy delta).
This is the call the model-default decision feeds into: pick {model ×
batch × cache} for the cheapest path that holds accuracy.

## Cost-audit follow-ups (F1–F7) — status 2026-07-02

Audit: `docs/plans/2026-07-01-pipeline-cost-audit.md`.

- ✅ **F1** (MessagesAPIExecutor / direct-API transport) — built + live-validated (see Priority-0 above).
- ✅ **F4** (delete measured-harmful Haiku prescreen path) — done, merged PR #25.
- ✅ **F3-offline** (default `brief_block` from `brief_sentence` in `run_apply_assessments`) — done, merged PR #25. No re-record needed (no scored baseline moved).
- ✅ **F6** (prune stale A/B configs, pin model IDs) — done, merged PR #25. Alias removal shipped in **v0.6.0** (PR #26).
- ⛔ **F5** skipped (superseded by F1); **F7** dropped (template below cacheable minimum).

### ⏳ OPEN — F3-metered (assess-v3 prompt bump + re-record)
The token-saving half of F3: a new **assess-v3** prompt that stops the agent
*writing* `brief_block` for `supported` claims (drops it from the schema).
The offline enabling half is already in (`run_apply_assessments` defaults it
from `brief_sentence`), so v3 is a one-prompt change. **Blocked on a metered
re-record** of the 3 corpora cassettes (`tests/build_assessment_corpora.py`)
**+ re-deriving the human-adjudicated regression baselines** in
`test_assessment_regression.py`. Payoff is small (~$0.009/claim, <15% of
output tokens). **Recommendation: do NOT re-record solely for this** (the
audit's own F3 guidance) — fold it into the *next* prompt-version bump so the
re-record cost is shared. `ANTHROPIC_API_KEY` is now in `.env` if run anyway.

### ⏳ PARKED — F2 (hybrid Sonnet/Opus routing)
Shelved after failing its validation gate; **draft PR #24** kept, not merged.
The gate was mis-specified (Opus itself trips it) and the cost case was weak
(~18% vs 25–35% target). Corrected diagnosis + a **redesign brief** are in
`docs/plans/2026-07-01-f2-hybrid-model-routing-design.md` (Validation outcome
section) — hand that to Fable for the redesign (relative gate, mandatory
same-day control, single-claim-v2 Sonnet eval, payne-23). Rebase onto current
`main` (post-0.6.0).

## ~~Priority 1 — A/B harness robustness bug~~ FIXED 2026-07-01 (skip-mode RecordedExecutor scores completed claims + reports the drop; tests/test_ab_runner.py::test_missing_verdicts_score_the_rest_and_report_drop)
Live A/B (`tools/ab_test_runner.py`) crashes when an assess job fails
transiently: the failed claim has no verdict, and `score_workdir`'s default
RecordedExecutor raises `RecordedVerdictMiss` on the first gap → whole run
dies (sonnet-v2 lost payne/wainwright this way). Fix `run_ab_config` live
branch: after `run_assess`, if `stats.pending > 0`, either (a) re-run assess
once to fill transient failures (resume-keyed), or (b) score only claims
that have verdicts and report the dropped count (no silent truncation).
Do NOT let one flaky job crash a multi-corpus run. (Offline fix; no API.)

## Priority 2 — Code-review deferrals (PR #21, logged 2026-06-14)
From the independent review; full disposition in
`docs/retrospectives/2026-06-14-code-review-disposition.md`. (Findings
1/2/3/5 were FIXED; these two deferred.)
- **run_verify partial-write window (review #4, Low).** wave1 writes
  verification_results.csv before wave2; if wave2 raises, the file
  persists wave-1-only and run_verify no-ops on rerun (file exists).
  Narrow (verify_batch swallows errors into VERIFICATION_INCOMPLETE).
  Fix when next touching the live verify path (write the CSV once after
  both waves, or add a completion sentinel). Touches live-API code the
  offline suite can't exercise — don't fix blind.
- ~~**_parse_json_object over-capture (review #6, note).**~~ **FIXED
  2026-07-01 — and the "over-capture" label was wrong.** Investigation
  (systematic-debugging + 3 real claude-sonnet-5 failures captured to
  `scratch/parse_failure_raw.txt`) found the actual failure class is the
  model intermittently emitting *almost*-valid JSON: (1) unescaped inner
  double-quotes in a string value, (2) a missing closing brace. Trailing-
  prose over-capture was already handled. All three pre-repair candidates
  share the same malformed text, so none recovered → claim dropped to
  pending (fails SAFE). Fix: added `json_repair` as the last-resort
  candidate in `_parse_json_object` (strict `json.loads` candidates run
  first, so well-formed output is untouched). Tests:
  `tests/test_parse_json_object.py` (RED-verified against the 3 real
  captures). Dep: `json-repair>=0.30` in pyproject.

### Structured outputs for the verdict JSON — the source fix (roadmap)
The deeper fix for the almost-valid-JSON class above: have the
MessagesAPIExecutor use tool-use / structured outputs so the API
*guarantees* schema-valid JSON, making `_parse_json_object` (and its
json_repair fallback) moot. Deferred because it changes the output
modality → requires re-recording the byte-pinned assess cassettes and a
re-validation run (like the 2026-07-01 F1 arm). The json_repair fallback
holds the line until then. (Design intent per the messages-api plan
decision 5, which chose text-parsing "as a fallback so current tests
hold.")

## Priority 2 — Improvements (better results)

### Report layout v2: filterable, skimmable, multi-view (logged 2026-06-12)
User feedback after seeing the first real v2 report (withers-v2-demo): the
current single-scroll card layout may not be the most useful shape — "what
would be most useful is something that lets people filter by type of error,
and is easily skimmed." Reference design: a scholarly-research citation
verification report (user's Downloads, "Citation Verification Report.htm")
with three view modes (**Summary / Table / Inline** buttons), Export, a
field-by-field `Field | Reference | Source` diff table, per-row
error-category badges (Mismatch, Partial match/omission, Missing element,
Verified...), column-visibility toggles, and go-to-context jump links.
NOTE: the reference report has badges only, NOT filtering — filter-by-
error-type is the user's own wish-list addition on top of that layout
("I just thought that would have been nice"). Not to be copied wholesale (it
diffs reference *metadata* against sources; we assess proposition
*support*), but the skimmability mechanics transfer:
- **Filter by error type** maps directly onto structures we already have:
  lane (Red / Yellow / Check Cite / Gray / Green via `report_lane`),
  `badge_label` taxonomy, crosscheck flag types, quote verdict
  (FABRICATED/CLOSE), `support` axis (v2+).
- **Table view**: one row per claim — page | case | lane chip | badge |
  one-line teaser — sortable/filterable, row expands to the existing card
  (brief block / opinion block / analysis). This is the skim layer the
  current layout lacks once findings exceed ~10.
- **Summary view** ≈ current dashboard; **Inline view** (brief text with
  claims highlighted in place) needs claim locations beyond `page` — note
  for extract-v2 (span offsets) if we want it.
- **Export** ties into the export-option item below (the lane-resolved
  JSON is exactly what an alternate renderer needs).
Implementation note: keep `report_template.py`'s data contract; this is a
template-layer change (plus client-side JS for filter/sort — no server).

### Pincite check: cross-reporter / too-few-marker false positives (found 2026-06-13 kettering shakedown)
`crosscheck`'s best-effort pincite check (`_pincite_flag`, §6.5) compares the
cited pinpoint against the opinion's star-pagination markers, but doesn't
verify the markers belong to the cited reporter. Kettering shakedown:
`Royal Truck & Trailer v. Kraft, 974 F.3d 756, 758-61` flagged pinpoint 758
against star range [3,7] — the opinion had only 3 markers [3,4,7] that are
NOT F.3d pages. Fired at the `>=3 markers` threshold on cross-reporter noise.
Flag-only (no color impact), same family as the footnotemark fix. Options:
(a) require the cited reporter to match the star-pagination reporter (parse
the star-marker context), (b) require markers to bracket a plausible range
near the pinpoint (e.g. |pin - nearest_marker| within the opinion's page
span), (c) raise the marker-count threshold. Low priority. Test fixtures:
`matters/kettering-mtd/` claims kettering-mtd-07/-33.

### Custom report consumers: claims.csv contract doc + export option (logged 2026-06-12)
User intent: people should be able to take the pipeline's outputs and make
their own judgment calls / build their own report (e.g., hand `claims.csv` to
Claude and ask for a custom memo) instead of using the built-in `report` verb.
Already works today — `report` is verb 8, optional; everything upstream is
plain CSV with facts (cl_status, quote_check, quote_floor, crosscheck_flags)
and judgments (assessment, finding_analysis) in separate columns, and
`scoring.report_lane()` / `quote_floor` are the two semantics a custom
renderer must honor (CITE_UNCONFIRMED is "check this cite", never Red; floors
already enforced into `assessment` by apply). Two cheap formalizations when
this becomes a first-class use case:
1. **Consumer-facing doc of the `claims.csv` column contract** — which columns
   are deterministic facts vs. LLM judgments, and the two semantics above.
2. **`export` option** (e.g., `report --format json` or an `export` verb)
   dumping the lane-resolved dict `generate_report` already builds
   (findings/check-cite/verified/unable lists) so any renderer — including a
   Claude prompt — starts from resolved lanes instead of re-deriving them.
Neither blocks the Step 8 acceptance work; log-only for now.

### Multi-party-caption + punctuated-query opinion-search gap (Sundown) (found 2026-06-11)
Surfaced finishing the Lever-2 ruling. `Sundown Energy LP v. HJSA No. 3, L.P.,
622 S.W.3d 884 (Tex. 2021)` stays NOT_FOUND even after lever (b) fixed its parse.
Two independent causes, both in the opinion-search path:
1. **Punctuated full-party query returns nothing.** CL opinion search returns **0**
   results for `q="Sundown Energy LP v. HJSA No. 3, L.P."` but **62** for
   `q="Sundown Energy HJSA"`. The `" v. "` + `"No. 3, L.P."` punctuation defeats CL's
   tokenizer. Candidate fix: a simplified/fielded fallback query (drop `v.`, strip
   trailing party-form suffixes) when the full query returns nothing.
2. **Sprawling caption + empty CL citations.** The real cluster (4872528, Tex. 2021)
   lists 14 parties (`Sundown Energy Lp Smc 2000 Lp, Pgp Holdings 1, LLC … v. Hjsa
   No. 3, Limited Partnership`) and an **empty** `citation` list. Even if returned,
   `name_matcher` similarity is dragged down by the extra parties. Candidate fix:
   lead-party / first-vs-first matching, or a containment boost when the cited lead
   plaintiff+defendant both appear in a longer CL caption.
Ideal outcome is VERIFIED + `cite_not_on_record` (case exists, CL lacks the cite).
Both are name-search/query-construction work, deliberately out of Tier 1 Step 3 scope.

### Plaintiff name truncation (eyecite upstream)
The dominant false negative pattern. eyecite stops too early on multi-word plaintiffs with abbreviations/punctuation:
- "Acceptance Indem. Ins. Co. v. Shepard" -> "Ins. Co. v. Shepard"
- "Auto. Fin. Corp. v. Liu" -> "Fin. Corp. v. Liu"
- "Jones v. Tenn. Dep't of Homeland Sec." -> "In re Tenn. Dep't of Homeland Sec." (plaintiff dropped, "In re" fallback)

Root cause likely in eyecite's `_scan_for_case_boundaries()`.

### Missing abbreviations in name_matcher
`LEGAL_ABBREVIATIONS` doesn't cover all Indigo Book terms. Known gaps:
- `Pro.` -> `Professional` (Smart v. Pro. Grp.)
- `Grp.` -> `Group` (Smart v. Pro. Grp.)
- Smart case: correct match found but scored only POSSIBLE_MATCH (0.60) because of these missing expansions.

### Composite opinion-likelihood + progressive date widening + page count
Full plan at `.claude/plans/woolly-nibbling-axolotl.md`. Three changes:
1. **Composite `_opinion_likelihood(desc, is_free, page_count)`** replaces separate `is_free` and `_doc_type_priority` tiebreakers. Prevents `is_free` on a memo endorsement from beating a real opinion (Cohen fix). Tier: opinion+free=3, opinion=2, order+free=2, order=1, free-only=1, none=0. Page count breaks ties within tier.
2. **Progressive date widening** in `_fetch_docs_for_docket`: exact date → month±1 → full year. Prevents year-range fallback from pulling docs 6 months away when we have month precision (HoosierVac fix).
3. **Page count as final tiebreaker** — `page_count` from docket-entries API. Longer docs more likely to be opinions (13-page opinion > 2-page memo endorsement).

### ~~`is_free_on_pacer` boost — manual testing~~ DONE
Tested via investigate rerun. Dukuray fixed (doc 40, exact date match). Cohen and HoosierVac need composite opinion-likelihood (above).

### Post comment on CL #6963
Draft ready at `scratch/drafts/cl-6963-is-free-on-pacer.md`. Post when ready.

### RECAP document selection (Patterns A, B, D)
**Pattern A** (non-substantive doc filtering): Lacey v. State Farm still chose "Order on Motion for Leave to File Document Under Seal" (POSSIBLE_MATCH 0.79). Fix: use `"leave to file" in desc` (contains) instead of `startswith`.

**Pattern B** (doc type priority — API issue): Mata v. Avianca and Davis v. Marion County still chose wrong docs. Correct docs (Opinion, R&R) likely not in `recap_documents` from search API. Investigate: docket-entries API followup query?

**Pattern D** (wrong doc, imprecise date matching):
- Wadsworth v. Walmart: chose "Memorandum in Opposition" (LIKELY_REAL 0.90)
- Dobson v. U.S. Bank: chose "Judgment" (POSSIBLE_MATCH 0.71)
- Moore v. Md. Hemp Coal.: NOT_FOUND 0.37, correct doc found but name mismatch ("Charm City Hemp v. Moore" vs "Moore v. Md. Hemp Coal.")
- Gauthier v. Goodyear: correct case (LIKELY_REAL 0.90) but wrong docket entry (doc 53 vs correct doc 48). https://www.courtlistener.com/docket/67624744/48/gauthier-v-goodyear-tire-rubber-co/
- HoosierVac: correct case (LIKELY_REAL 0.85) but wrong document. https://www.courtlistener.com/docket/68879596/129/mid-central-operating-engineers-health-and-welfare-fund-v-hoosiervac-llc/

### Semantic search fallback (CourtListener Citegeist)
CL supports `semantic=true` for `type=o`. Could help with abbreviation mismatches, name variations, multi-defendant cases. Best fit: new step between opinion search (Step 2) and RECAP search (Step 3), triggered only on failures. See: https://www.courtlistener.com/help/api/rest/search/#semantic-search

## Priority 3 — Parser edge cases

### Complex case number parsing (Button v. Doherty)
"Button v. Doherty, Case No. 24 Civ. 5026 (JPC) (KHP), 2025 WL 2776069 at *5 n. 7 (S.D.N.Y. Sept. 30, 2025)" — multiple parentheticals in case number may confuse court/year extraction.

### Short cite handling
eyecite may support short cites (e.g., "M.G., 566 P.3d at 146-147"). Would need to resolve back to the full citation earlier in the document.

### Docket number format variants
- Aikens v. Nw. Dodge: docket `03 C 7956` not parsed correctly, should expand to `03-cv-7956`. https://www.courtlistener.com/docket/5359695/108/aikens-v-northwestern-dodge/
- Fibertext Corp.: docket `20-20720-Civ` should parse as `20-cv-20720`. NOT_FOUND (0.32), matched wrong case.

## Investigate — needs manual research

### Johnson v. Dunn (slip opinion format)
Real case at https://www.courtlistener.com/docket/62980057/204/johnson-v-dunn/ but NOT_FOUND. Citation: `Johnson v. Dunn, -- F. Supp. 3d ----, 2025 WL 2086116 (N.D. Ala. July 23, 2025)`. The `-- F. Supp. 3d ----` slip-opinion format likely threw off citation lookup, and RECAP should have caught it.

### ~~Bidirectional abbreviation normalization (Priority 1)~~ PARTIALLY FIXED
Added missing abbreviations to both `name_matcher.py` (LEGAL_ABBREVIATIONS) and `parser.py` (`_normalize_case_name()`). The name_matcher now normalizes both the cited name AND the CL result name through the same expansion pipeline. New abbreviations: `comm'r` → commissioner, `info` → information, `sol`/`sols` → solution/solutions, `fin` → finance, `nw`/`sw` → northwest/southwest, `ass'n` → association, `coop` → cooperative. Also added `&` → `and` conversion in `_normalize()`.

Fixed abbreviation mismatches:
- ~~`Comm'r` vs `Commissioner` (Russomanno)~~ ✓
- ~~`&` vs `and` (King v. Police & Fire)~~ ✓
- ~~`Info. Sols.` vs `Information Solutions` (Dukuray)~~ ✓
- ~~`Fed.` vs `Federal` (King)~~ ✓ (was already handled)
- ~~`Gen. Ins. Co.` vs `General Insurance Company` (Lacey v. State Farm)~~ ✓ (was already handled)
- ~~`Nw.` vs `Northwest` + `Ass'n` vs `Assoc.` (Weatherly — scored 53%)~~ ✓
- ~~`Fin.` vs `Finance` + `Corp.` vs `Corporation` (Auto Fin. Corp. v. Liu — scored 59%)~~ ✓

Remaining (not abbreviation issues — different root causes):
- First names in CL but not in citation (Glass, Todd v. vs Glass v.) — name length mismatch, not abbreviation
- Truncated plaintiff: `Welfare Fund` vs `Mid Central Operating Engineers Health and Welfare Fund` (HoosierVac) — plaintiff truncation issue
- Reporter citation not confirmed when CL simply has no citations on file (Shahid v. Esaam) — CL data gap

### Cohen (common name)
United States v. Cohen, 724 F. Supp. 3d 251 (S.D.N.Y. 2024). Real case but NOT_FOUND. Common defendant name + CL search limitations. Semantic search may help.

### Hayes (common name)
U.S. v. Hayes, 763 F. Supp. 3d 1054 (E.D. Cal. 2025). Confirmed real, NOT_FOUND. Appears twice in seed 3193 batch (from two different PDFs).

### Rocha v. Fiedler, 2025 WL 1219007 (9th Cir. Apr. 28, 2025)
NOT_FOUND despite opinion existing in CL: https://www.courtlistener.com/opinion/10386186/rocha-v-fiedler/. Unclear why search missed it.

### Fibertext fuzzy search (possibly resolved)
"Fibertext" (cited) vs "Fibertex" (actual) — single character difference defeated CL fuzzy search. May have been resolved by CL improvements or our normalization fixes. Needs rerun to confirm. (Docket parsing issue tracked separately in Priority 3.)

## Eyecite Upstream

### PR for fork fixes
Holding until we've used the fork more. Current fixes on rlfordon/eyecite branch `fix-pdf-metadata-parsing`:
1. Apostrophe truncation in `_process_case_name()` -- negative lookbehind fix
2. ParagraphToken boundary in `match_on_tokens()` -- single newline = space
3. Apostrophe in regex character classes (SHORT_CITE_ANTECEDENT_REGEX, etc.)

## QC Review Findings

Items flagged during QC review (uncategorized — sort periodically).

**Dukuray v. Experian Info. Sols., No. 23 Civ. 9043, 2024 WL 3812259 (S.D.N.Y. July 26, 2024)**
Source: melissa_wilcox_v._matthew_a._gingrich.pdf. Verification: POSSIBLE_MATCH (0.5589). Matched: https://www.courtlistener.com/docket/67881565/43/dukuray-v-experian-information-solutions/. Notes: no slightly different date! https://www.courtlistener.com/docket/67881565/40/dukuray-v-experian-information-solutions/. Added automatically from QC review.

## FLP Contributions

See `scratch/flp_contributions.md` for detailed write-ups and drafted comments.

### RECAP-only federal court opinions (contribution #5)
Growing list of confirmed real cases found only in RECAP, not in the opinions DB. Now 16 unique cases (up from 9). CL's `recap_into_opinions` (#3790) converted 317K+ civil cases in Oct 2025, but gaps remain. See `flp_contributions.md` section 5.

## Known CL API Limitations

Tracked in `tests/data/cl_api_issues.json`. All have workarounds implemented.

1. **Search abbreviation matching** (HIGH) - "Cnty." doesn't match "County". Workaround: client-side normalization. FLP aware (#3089, #3367).
2. **Docket parameter unreliable** (HIGH) - RECAP `docket` param ignored. Workaround: use `q` with quoted string.
3. **Case name variations** (MEDIUM) - CL stores different defendant than cited. Workaround: docket search + fuzzy matching.
4. **Missing citations field** (MEDIUM) - Some cases have empty citations. Workaround: fall through to opinion/RECAP search.
5. **State court coverage gaps** (LOW) - Some state courts incomplete. No workaround.

## Testing

### Justia diagnostic script
One-off script to compare NOT_FOUND citations against Justia to distinguish: real hallucinations, CL data gaps, our search bugs. Diagnostic only.

### ~~Expand fake citations corpus~~ DONE (2026-06-10)
Expanded 8 → 19 entries: promoted 11 QC-confirmed hallucinations from `QC_TRIAGE.md` (Lopez, Reed v. ZipRecruiter, In re Tenn. DHS, Johnson v. Mitchell, both Thompson v. Best variants, both Pointe Wholesale variants, Mavy, Marion, In re Hudson). New `tests/test_false_positives.py` runs them live (`pytest tests/test_false_positives.py -m live_api`) asserting no VERIFIED-family status. **Next: run live on a token-equipped machine** — the prior_result fields show which were v0.2 false positives; the live run shows which v0.3 already fixed (Tier 1 Step 1 measurement in `docs/plans/2026-06-10-prioritized-roadmap.md`). Excluded for conflicting labels: Hayes (TODO says real, QC says fake — resolve), Benjamin v. Costco + Mungo 486 Md. (possible CL data gaps), Avery v. Ward + Morgan v. Cmty. (calibration cases, not confirmed fakes).

## verify-brief Skill

### Fabricated quote flag in assessment criteria
Assessment subagent prompts need a separate "FABRICATED QUOTE" check: for any text the brief places in quotation marks and attributes to a case, verify the exact words appear in the opinion. Currently our Green/Yellow/Red conflates substantive accuracy with verbatim accuracy. The Fletcher run had 5 false Greens where the substance was right but the quoted words were AI-generated. See `docs/retrospectives/2026-03-10-verify-brief-fletcher-v-experian.md` §1.

### TOA vs body citation cross-check in Phase 1a
Extract citations from both the Table of Authorities and the brief body. Flag discrepancies in reporter volume, page, or year. The Fletcher brief had "97 F.3d" in the body vs "597 F.3d" in the TOA — wrong case entirely. We used the TOA version and missed it. BriefCatch caught it deterministically.

### Short-form-only citation risk flag
When a case appears only in short form (no full citation anywhere in the brief), flag it in the CSV/report. The reconstructed full citation could be wrong. From Fivehouse retro: reconstructed "Dow AgroSciences, 637 F.3d at 268–69" to the full citation from context — worked, but risky.

### Assessment subagent batching guidance
Max 4-5 opinion files per subagent. The Fletcher "remaining 11 opinions" agent took 149s and 119K tokens. Splitting into 3-4 smaller agents would parallelize better.

### Prohibit external tools in assessment agents
Assessment agent prompts must explicitly say "Do NOT use any external tools like Midpage — only use Read." The Flatley v. Mauro agent in the Haiku prescreen test autonomously called Midpage, introducing an uncontrolled variable. Fixed mid-test but needs to be permanent in the skill.

### Assessment-to-CSV workflow
Currently requires throwaway Python scripts to write subagent JSON results into claims.csv. Options: (a) subagents write JSON sidecar files, single merge step updates CSV; (b) orchestrator uses Edit tool on CSV directly; (c) add `--update-assessments` CLI command to `brief_pipeline.py`. Same issue for report generation — should be `--report` CLI command.

### A/B testing infrastructure (2026-03-22)
Built and tested. Files: `tests/ab_test_runner.py`, `tests/ab_test_cases.json`, `tests/ab_test_configs.json`, `tests/build_review_page.py`. Uses `claude -p` headless mode (no separate API costs). 61 human-reviewed ground truth cases (27 Payne + 34 Wainwright). First results: Sonnet 81% vs Opus 85% on 27-case Payne subset. Next steps:
- [ ] Run full 61-case suite with both Sonnet and Opus baselines
- [ ] Test "with hints" configs (Haiku summary hints to assessment agent)
- [ ] Add Kettering test cases to corpus
- [ ] Investigate whether batch mode (all claims per opinion) changes results vs single-claim mode

### Report improvements
- [ ] Add opinion side panel + search to `report.html` (like the review page)
- [ ] Standardize on Kettering-style report format (quote comparisons, Retrieved column)
- [ ] Consider `build_report.py` as reusable across briefs or add `--report` CLI command

### Pipeline architecture: skill -> Python scripts
`claude -p` headless mode makes it feasible to replace the SKILL.md with a series of Python scripts. LLM-dependent phases (extraction, proposition extraction, summaries, assessment) become `claude -p` calls. Gains: reproducible, testable, resumable, runs unattended, A/B testable per-phase. Design doc needed before implementation.

### Proposition extraction: separate argument from citation scope
Currently, the proposition extraction agent captures the full sentence/argument a case appears in. But briefs often make compound arguments and cite a case for only part of it — scoped by a parenthetical. Example: "the justification charge already covered the legal defense raised; plain error requires a clear legal mistake that affects substantial rights (State v. Kelly, 290 Ga. 29)." Kelly is cited for the plain error standard (which it does address), not for the justification point (which it doesn't). Our current extraction treats the whole sentence as the proposition, causing false Yellow/Red assessments.

Fix options:
- (a) Extract two fields: `full_proposition` (the sentence) and `cited_for` (what the parenthetical or citation signal attributes to this specific case)
- (b) Instruct the assessment agent to scope its evaluation to what the case is actually cited for, considering parentheticals and citation signals (See, e.g., Cf., etc.)
- (c) Both — extract scoped propositions and also give the assessment agent the surrounding context

This is a design question for the pipeline redesign. Option (b) is cheaper (prompt change only). Option (a) is more testable.

### Proposition extraction quality
Payne case 47 (Chambers v. State) — the proposition extraction agent assigned the wrong proposition to this case. The brief cites Chambers for "deadly force is justified if reasonably necessary to prevent death/great bodily injury" but the agent recorded it as "excessive force in self-defense is not justifiable." This affected the assessment. Need to investigate whether this is a one-off or systematic.

### Quote checker limitations (from Wainwright review)
- Star pagination in CourtListener HTML interferes with matching (Lawrence v. State FABRICATED due to `*534` in the middle of the quote)
- Missing articles ("the") cause CLOSE instead of VERBATIM (Hammond v. State)
- Bracket alterations in quoted text (Charleston: `[to]` replacing `must`) not handled

### Baseline test (RED phase)
Run the brief verification task WITHOUT the skill loaded to establish what Claude does naturally. Steps:
1. Rename `~/.claude/skills/verify-brief/SKILL.md` → `SKILL.md.bak`
2. Open fresh Claude Code session in this folder
3. Give it the Kettering brief: "verify the citations in this brief" (same input as first test)
4. Observe: Does it use `citation_verifier`? Read opinions? Assess claims? What structure does it use?
5. Rename skill back when done
6. Compare to skill-guided run — document what the skill improves

### Word doc support
Not tested yet. Options: python-docx dependency, or just have user paste text.

### Second test run
Re-run `/verify-brief` on Kettering brief with iteration 1 fixes. Document results in `briefs/kettering-v-collier/skill-test-2-feedback.md`.

### Valve v. Rothschild run retrospective (2026-03-04)
Full retrospective at `.claude/projects/-Users-fordon-4-Projects-citation-verifier/memory/verify-brief-retrospective.md`. Key takeaways:
- **AskUserQuestion broken** — remove from skill; auto-accept high-confidence, always generate HTML report.
- **Phase 2 needs async batch mode** — run all citation lookups (step 1) concurrently first, then steps 2-3 only for unresolved. Mirrors the web app approach. Could cut Phase 2 time significantly. Requires new `AsyncCitationVerifier` or batch method in the library.
- **Phase 1 CSV writing is slow** — Opus extraction is needed, but CSV writing from structured data could be Haiku.
- **Fortune Dynamic wrong text** — CL opinion page had Arthur v. Torres attached. Add sanity check: compare downloaded case name to expected.
- **Brief had 51% Red citations** — fabricated quotes, cases cited for opposite holdings, inapposite cases. Patterns consistent with AI-generated legal writing.

## Bankruptcy Court — Known Hard Problem

Basic parsing support landed (Bankr. prefix → CL court IDs like `nysb`, `deb`, `txsb`; judge initials stripped; In re case names parsed correctly). But RECAP document selection is fundamentally harder for bankruptcy dockets:

- **Volume**: Dozens of entries per day (Cineworld had 19 entries across Sept 8-9, 2022 alone). Progressive date widening pulls back massive result sets.
- **Document types**: Substantive documents are "Orders Authorizing..." not "Opinions" — the current `_opinion_likelihood` ranker was tuned for regular federal litigation. Orders are medium-tier but in bankruptcy they're the main event.
- **Noise**: Audio files (MP3), courtroom minutes, pro hac vice orders, notices of appearance, transcript requests — document types that barely exist in regular federal dockets.
- **No clear opinion document**: The operative filing is usually just an "Order," indistinguishable from dozens of other procedural orders on the same day.

### Approach: case-type-dependent ranking

The current `_opinion_likelihood` assumes we're looking for something labeled "opinion" — wrong for bankruptcy (and possibly other case types like patent, habeas). Ranking should adapt based on court/case type.

**Bankruptcy substantive document hierarchy** (rough):
1. Findings of Fact / Conclusions of Law — closest to an "opinion"
2. Order Confirming Plan / Order Approving [Settlement/Sale/DIP Financing] — the substantive rulings
3. Memorandum of Decision — rare but exists
4. Generic "Order" — could be anything, need description matching

**Bankruptcy noise floor** (deprioritize heavily):
- Audio files / MP3s
- Courtroom minutes
- Pro hac vice orders
- BNC certificates of mailing/service
- Notices of appearance
- Transcript requests (AO 435)
- Notices (unless "Notice of" + substantive topic)

This case-type-dependent approach could also help other case types: patent (Claim Construction Orders), habeas (R&Rs), etc.

### CL docket entry classifier (active FLP project)

FLP is actively building this: [#6689 — Docket entry classifier](https://github.com/freelawproject/courtlistener/issues/6689) (grandparent issue, open). Goal: label docket entries as `Complaint`, `Motion`, `Order`, `Judgment`, `Claim Construction Order`, etc. — exactly what we need. Would become search facets and filters.

Related issues:
- [#5288 — Identify FLP categories](https://github.com/freelawproject/courtlistener/issues/5288) (open) — Phase 1: high-level classes (complaint, motion, notice, judgment, etc.)
- [#5294 — Modeling baseline](https://github.com/freelawproject/courtlistener/issues/5294) (closed) — baseline ML model established
- [#5123 — Study filing categorizing ontologies](https://github.com/freelawproject/courtlistener/issues/5123) (closed)

If/when this ships, we could consume the classification labels via API to dramatically improve RECAP document selection, especially for bankruptcy. In the meantime, we'd need our own heuristic ranking. Worth monitoring and potentially contributing training data from our bankruptcy verification experience.

### Other open questions
- Do bankruptcy courts label orders as "opinions" less frequently than district courts? If so, the opinion-keyword tier is systematically biased against bankruptcy results.
- Could we match the cited order's subject matter against docket entry descriptions? e.g., if the citation context mentions "settlement with Prime Trust," match against entries containing those terms.

## A/B Test: `/proposition-verifier` vs `/verify-brief` — COMPLETE

Both skills run on the same brief (Brooks v. Lowe's, `briefs-2/gov.uscourts.lawd.207038.49.1.pdf`) on 2026-04-15.

- `/proposition-verifier` run complete — report at `briefs-2/gov.uscourts.lawd.207038.49.1_proposition_report.html`, notes at `briefs-2/gov.uscourts.lawd.207038.49.1_run_notes.md`
- `/verify-brief` run complete — retrospective at `docs/retrospectives/2026-04-15-verify-brief-brooks-v-lowes.md`, report at `briefs/gov.uscourts.lawd.207038.49.1/report.html`
- `/verify-brief` results: 9 Green, 8 Yellow, 3 Red (20 claims, 16 citations, ~9 min active time, ~21 CL API calls + 17 LLM agents)
- `/proposition-verifier` results: 11 Green, 3 Yellow, 3 Red, 2 Gray (19 claims, ~31 min compute, ~17 CL MCP calls)
- **Report format: `/proposition-verifier` report is significantly better.** Collapsible details, paired blockquotes ("What the brief claims" / "What the opinion actually says"), Lora serif headings, methodology disclosure. User strongly prefers this over `/verify-brief`'s flat table format.
- [x] Compare: same issues caught? Different false positives/negatives? Timing/API usage?

### Comparison results

**Proposition-verifier was stricter and more accurate on 5 of 6 divergent calls:**
- Collins: prop-verifier Red (correct — page 784 is expert cross-exam, not settlement exclusion), verify-brief Yellow
- Abel: prop-verifier Red (correct — brief inverts the holding), verify-brief Yellow
- Menges: prop-verifier Gray (correct — CL coverage gap, not fabricated), verify-brief Red
- Michelson: prop-verifier Yellow (correct — pinpoint off), verify-brief Green
- Bankcard/Lasha: prop-verifier Yellow (correct — word swaps in "verbatim" quotes), verify-brief Green

**Verify-brief caught one thing prop-verifier missed:**
- Old Chief p.8: verify-brief Red (correct — Rule 403 language applied to spoliation, which Old Chief never discusses), prop-verifier Green

**Verify-brief pipeline advantages:** 3.4x faster, found Gilliam (NY state) via RECAP that MCP connector missed, resumable CLI phases.

**Decision:** Merge into unified `/verify-brief` — verify-brief pipeline + proposition-verifier report format and assessment calibration. Plan at `docs/plans/2026-04-15-unified-brief-verifier-plan.md`, rationale at `docs/plans/2026-04-15-unified-brief-verifier-rationale.md`.

## Future Ideas

Moved to `scratch/ROADMAP.md` — covers client-side BYOK, WL/Lexis data contributions, semantic search, and more.
