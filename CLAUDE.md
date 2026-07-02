# Citation Verifier - Development Guide

## Project Overview

Legal citation verification tool that checks citations against [CourtListener](https://www.courtlistener.com/)'s API. Designed to catch AI-hallucinated case citations. Python 3.10+, installed as editable package (`pip install -e .`).

Uses a forked [eyecite](https://github.com/freelawproject/eyecite) (rlfordon/eyecite branch `fix-pdf-metadata-parsing`) with PDF parsing improvements: apostrophe preservation in case names, single newline = space (PDF line breaks), consecutive newlines = paragraph break. Installed as editable: `pip install -e /Users/fordon.4/Projects/eyecite`.

## Workflow Preferences

- **Always commit and push all changes** (not just code). Working data in `scratch/`, CSV files, etc. should all be committed and pushed unless gitignored. The user works across multiple computers and uses git to sync everything.
- **Never write important information only to Claude memory.** Memory files are per-machine and per-project-path — they don't sync across computers. Anything that should persist (retrospectives, test feedback, design decisions, session notes) must be written to a file in the repo so it gets committed and pushed. Memory is fine for caching preferences and shortcuts, but not for unique artifacts.
- **"Save this somewhere"** means write it to a file in the repo (e.g., `scratch/`, `briefs/`, `docs/`), never to memory. Ask where if the right location isn't obvious.

## Architecture

Three-step verification pipeline in `src/citation_verifier/verifier.py`:

1. **Citation Lookup API** (`/api/rest/v4/citation-lookup/`) - Resolves reporter citations (e.g. `576 U.S. 644`). If found, verifies case name matches before returning VERIFIED. If the citation exists but belongs to a different case, returns POSSIBLE_MATCH with a name-mismatch diagnostic. If there is no comparable case name on either side (parse produced none, or the CL cluster lacks a caption), returns VERIFIED_PARTIAL with a `name_unverified` warning — never blind VERIFIED@1.0 (Charlotin Bug 1 policy, 2026-06-11).

2. **Opinion Search** (`/api/rest/v4/search/?type=o`) - Fuzzy search by case name, court, and date range (+/- 1 year). Retries without court filter if no results.

3. **RECAP Search** (`/api/rest/v4/search/?type=r`) - Searches PACER docket data. When a docket is found, queries the **docket-entries API** (`/api/rest/v4/docket-entries/`) filtered by the cited year to find the actual opinion/order document. Important: RECAP `dateFiled` is the case filing date, NOT the opinion date -- always use `entry_date_filed` from individual docket entries.

**Check Cite — `CITE_UNCONFIRMED` status (Tier 1 Step 3, 2026-06-11).** A fallback (step 2/3) name-search win is *post-threshold* reclassified by `_classify_cite_unconfirmed` (shared sync/async, in both `_build_fallback_result` variants) when the cited reporter/WL location can't be tied to the matched case. Two sub-cases: (a) the matched record lists a citation in the **same reporter family** as the cited one (N.E.2d ≡ N.E.3d via `_reporter_family`; U.S. ≠ S. Ct.) but not the cited address → `CITE_UNCONFIRMED` + `cite_contradicted`; (b) a **bare RECAP docket** (no document/text) → `CITE_UNCONFIRMED` + `cite_not_on_record`. When there's **no same-family witness** (CL reporter gap, parallel cite, or a VIA_RECAP doc gate-passer) the status is **unchanged** and only a `cite_not_on_record` warning fires — this is deliberate reporter-gap compensation (the case exists; CL just lacks the cite). **No scoring changes** — classification never touches the score, threshold, party penalty, or cap (the Muldrow constraint: reporter mismatches must not move scores). UI label "Check Cite"; design `docs/plans/2026-06-11-check-cite-design.md`.

## Key Design Decisions

- **Multi-factor name matching** (`name_matcher.py`): 4-factor weighted score (sequence similarity 0.25, word overlap 0.30, substring 0.20, key words 0.25). Abbreviated name boost to 0.85 when short name is a subset of long name (skipped for "In re" cases).

- **Two name-matching thresholds**: `_names_match_citation_lookup` is lenient (surname containment — citation already proven to exist). `_names_match` is stricter (used for fuzzy search). Both compare defendants for common-prefix cases (State v., United States v.).

- **Scoring weights**: case name 50%, court 20%, date 20%, docket number 5%, reporter/WL citation 5%. Weights redistribute proportionally when court or year is missing from the citation. RECAP docket-only matches get a 0.6x score discount.

- **Parser** (`parser.py`): eyecite handles standard reporters. Regex fallbacks handle WestLaw (`2018 WL 301424`), California style (`(2022) 76 Cal.App.5th`), reversed parentheticals (`(Feb. 5, 2026 SDNY)`), and complex party names. Docket number junk is stripped from case names. `parsed_citation_from_eyecite()` builds a `ParsedCitation` directly from an eyecite `FullCaseCitation`, avoiding the lossy string round-trip that drops court, month, and day fields.

- **Pre-parsed citation path**: `verify()` accepts an optional `parsed: ParsedCitation` parameter. When provided, the internal `parse_citation()` call is skipped. This lets batch pipelines pass eyecite-extracted metadata (court, month, day) without information loss. All existing callers (CLI, tests) are unaffected.

- **Batch verification** (`verify_batch()`): Async method on `CitationVerifier` that verifies multiple citations efficiently. Sends all citations in a **single** citation-lookup API call, then only falls back to opinion search + RECAP for misses. Much faster than calling `verify()` in a loop (which does 1-3 API calls per citation). Supports `progress_callback(completed, total)`, optional `parsed_citations` list, and `quick_only=True` to skip fallback. Always prefer `verify_batch()` over looping `verify()` when verifying multiple citations. Usage: `results = await verifier.verify_batch(citations)`.

- **Abbreviation normalization**: 47 Indigo Book terms expanded client-side in `parser.py:_normalize_case_name()` to work around CL search not matching abbreviations. Smart apostrophes normalized to straight before matching.

- **Docket number normalization**: Strips division prefix (`2:`), judge suffix (`-JCC`), expands shorthand (`C15` -> `15-cv`), strips leading zeros.

- **RECAP state court skip**: RECAP is federal PACER data only. `is_federal_court()` gates all RECAP API calls.

- **429 retry handling** (`client.py`): Parses `wait_until` ISO-8601 timestamp from CL response body, falls back to Retry-After header, retries up to 3 times.

- **RECAP document ranking** (`_opinion_likelihood`): Composite tiebreaker `(tier, page_count)` for picking the best document from a docket. Tier combines doc-type keywords (opinion/memo/R&R = high, order/ruling = medium) with `is_free_on_pacer` flag. Prevents `is_free` from promoting across doc-type tiers. Replaces the old separate `_doc_type_priority` + `is_free` tiebreakers.

- **Progressive date widening**: When fetching docket entries, uses 3-step fallback: exact date -> month +/- 1 -> full year. Prevents pulling documents months away when we have month precision.

- **Slip opinion placeholder stripping** (`parser.py`): `_SLIP_OPINION_JUNK` regex strips `-- F. Supp. 3d ----` and `--- S.Ct. ---` patterns from case names. eyecite absorbs these into the defendant field, poisoning CL searches (causes Solr 500 errors). Filed as potential eyecite contribution (#9 in `scratch/flp_contributions.md`).

## Files

### Core library (`src/citation_verifier/`)

| File | Purpose |
|------|---------|
| `models.py` | Data structures (enums, dataclasses) |
| `court_map.py` | Court abbreviation -> CL ID mapping (135 federal courts), `is_federal_court()` |
| `state_reporter_map.py` | Regional reporter -> state court mapping |
| `name_matcher.py` | Multi-factor case name similarity (adapted from CaseStrainer) |
| `quote_matcher.py` | Quote-fidelity primitives. Public `verify_quote(quote, opinion_text, *, was_ocrd=False) -> QuoteVerification` (+ `QuoteMatch` enum), exported from the package. Houses `_normalize_quote_text`, `_best_match_with_passage`, `_extract_passage` (re-exported from `proposition_pipeline` for compat) and `_normalize_ocr_confusions` (conservative `rn`->`m` / `O`->`0` / `l`->`1`, applied only when `was_ocrd`). |
| `text_cleaner.py` | Contamination phrase removal (adapted from CaseStrainer) |
| `parser.py` | Citation parsing (eyecite + regex + abbreviation normalization + eyecite factory) |
| `client.py` | CourtListener API wrapper (rate limiting, 15s timeout, 429 retry). Both sync (`CourtListenerClient`) and async (`AsyncCourtListenerClient`) have `get_opinion_text()` and `get_opinion_text_with_metadata()` for fetching full opinion/RECAP text + metadata. Supports `prefer_html=True` for raw HTML and PDF download fallback. **Canonical opinion-text fallback chain (`_extract_opinion_text`):** `plain_text` -> `html_with_citations` -> `html` -> `html_lawbox` -> `html_columbia` -> `html_anon_2020` -> `xml_harvard`. State opinions in CL frequently have empty `plain_text` but populate `html_lawbox`/`html_with_citations`/`xml_harvard` -- never roll your own fetcher that only checks `plain_text`. The 2026-05-06 state-court smoke test found this gap drops state-tier full-text coverage from 86% to 9%. Tests for each fallback field live in `tests/test_client_opinion_text.py::test_falls_back_to_secondary_html_fields`. |
| `verifier.py` | Core 3-step pipeline (shared helpers + thin sync/async wrappers) |
| `executor.py` | LLM executor protocol (pipeline-redesign design §5): `Job`/`Verdict` dataclasses, `LLMExecutor` Protocol, JSONL verdict serde, `RecordedExecutor` replay adapter (raises `RecordedVerdictMiss` on cassette miss; key = `claim_id` + `prompt_version`), `AgentToolExecutor` (jobs mode), and **`AgentSDKExecutor`** — the headless default (§5.1): one `claude-agent-sdk` `query()` per job, `allowed_tools=["Read"]`, strips `ANTHROPIC*`/`CLAUDE*` from `os.environ` around the call (the SDK's `options.env` only merges over inherited env — it cannot remove parent-session leakage), drains the async generator fully (partial consumption segfaults at shutdown on Windows), raises `AgentSDKAuthError` ("run `claude login`") on 401 instead of failing N jobs, records non-auth per-job failures in `.failures` and continues; a result whose JSON carries a `verdicts` array (assess-v2 packed jobs) fans out one per-claim `Verdict` via the shared `_fan_out_verdicts` helper (unknown claim_ids recorded+dropped, skipped claims stay pending). Not usable inside a running event loop (calls `anyio.run` per job) — in-session runs use jobs mode. **`MessagesAPIExecutor`** (cost-audit F1, 2026-07-01): direct Messages API transport — one single-shot completion per job, `job.files` inlined (PDFs as base64 document blocks; the byte-pinned templates are untouched — a transport-level bridge note wraps the verbatim prompt), concurrent streaming calls by default (`max_concurrency=8`) or `batch=True` for the Batches API (50% off, polled); model aliases pinned to explicit IDs (`opus`→`claude-opus-4-8`, `sonnet`→`claude-sonnet-5`, `haiku`→`claude-haiku-4-5`) so `Verdict.model` never records an alias; `cost_usd` computed from usage at published rates; needs `ANTHROPIC_API_KEY` in `.env` and raises `MessagesAPIAuthError` on 401 (the CLI catches the new `ExecutorAuthError` base, which `AgentSDKAuthError` also joins). `RecordedExecutor(missing="skip")` tolerates cassette gaps — records `.misses` and keeps yielding — for scoring live runs with transient job failures; the default stays strict (`"raise"`, the cassette policy) |
| `scoring.py` | Two-axis scoring (design §6.9/§8): `derive_color()` pure color function, `report_lane()` (the report's v1-schema lane adapter — v1 verdicts have NO support axis, so the report can't call derive_color per claim; precedence: WRONG_CASE Red > CITE_UNCONFIRMED CheckCite-never-Red > UNLOCATABLE-no-text Gray > floor-enforced `assessment` column authoritative (empty → Yellow); switches to derive_color when assess-v2 fills `support`), `predict_workdir()`/`score_workdir()` offline corpus scoring (enforces `quote_floor` on replayed verdicts — the agent can lower a color, never below the floor), CLI `python -m citation_verifier.scoring <corpus-dir>` |
| `proposition_pipeline.py` | Proposition verification pipeline (evolved from `brief_pipeline.py`; **`brief_pipeline` is a deprecated `sys.modules` alias** of this module for one minor version — attribute patches against either name hit the same globals). Idempotent verbs (design §3): `run_extract()` (LLM verb 0, optional front end: document → `claims.csv` + `citations_toa.txt` + `citations_body.txt` via `prompts/extract_v1.md`; one job per workdir, resume key = `"extract"` + prompt_version in `jobs/extract_results.jsonl`; jobs mode writes `jobs/extract.json` and pends, rerun to ingest; pipeline assigns `claim_id = <workdir.name>-NN`; no-ops when claims.csv exists so prepared-pairs workdirs never re-extract), `run_verify()` (wave1+wave2+downloads, no-ops if `verification_results.csv` exists, `--force` to redo; `citations_from_workdir` unions the extract citation lists), `run_merge()` (claims↔results join + **slug-token opinion linkage** — Jaccard ≥ 0.25 on cl_url slug / matched name / cited name tokens vs file stems, replacing name-containment), `run_assess()` (LLM verb: resume key = claim_id + prompt_version; default executor is jobs mode — writes `jobs/assess.json`, agents append verdicts to `jobs/assess_results.jsonl`, rerun to ingest; pass `RecordedExecutor` for offline replay. **assess-v1** (default): one job per claim via byte-pinned `prompts/assess_v1.md`. **assess-v2** (`--prompt-version assess-v2`): one **packed job per opinion** (Step 8 decision: per-opinion only, a documented deviation from §6.8's multi-opinion caps) via `prompts/assess_v2.md` + `render_assess_v2_prompt` — claim blocks carry `cited_for` (§6.3, judge-this instruction), quoted strings, matched-passage hints ≥0.65 sim, and `prescreen_hint` (§6.7); the agent returns a per-claim `verdicts` array with `support` + the four report-block fields, never a color), `run_assess_hybrid()` (cost-audit F2: Sonnet single-claim v2 over `triage_track=='fast'` claims → any not `support=='supported'` (or that fails) escalates to Opus with the full-track claims (packed per opinion); only final verdicts persist, discarded Sonnet cost captured in `AssessStats.escalated_cost_usd`; opt-in via `--route hybrid`, requires `--executor api|sdk`; assess-v2 only), `run_apply_assessments()` (verdicts JSONL → claims.csv: schema validation + §6.4 quote-floor enforcement; v1 verdicts write the agent color, **v2 verdicts derive the color via `scoring.derive_color(cl_status, support, quote_check_worst)`** and write `support`/`badge_label`/`brief_block`/`opinion_block`/`finding_analysis`), `run_check_quotes()` (thin wrapper over `check_quotes` + run.json stamp), `run_crosscheck()` (§6.5 deterministic flags → `crosscheck_flags` JSON column: TOA-vs-body variant diff from the extract lists, cited-vs-matched court check via `lookup_court_id` + the vr CSV's `matched_court_id`, best-effort pincite check — star-pagination range + footnote existence grep; **flags only, never colors**; skips silently when inputs are missing — legacy workdirs tolerated), `run_triage()` (§6.7: `triage_track` full|fast|'' deterministic rules — FABRICATED/CLOSE, quote_floor, quoted_text, crosscheck flags, or non-clean-verified status → full; the SKILL's syllabus/lead-authority LLM judgments are NOT replicated; `prescreen=True` runs Haiku summary-hint jobs via `prompts/prescreen_v1.md` for opinions ≥20K chars through the executor protocol into `prescreen_hint` — **default OFF, decided 2026-06-13** by the per-phase A/B re-run (hints gave no net A/B gain and regressed Withers 16→14 yellows in the lenient direction); assess-v1 can't consume hints, assess-v2 can), `run_report()` (§3 row 8: claims.csv → `report.html`; reads `brief_metadata.json` for the header when present; routes every claim through `scoring.report_lane` — the §6.9 lanes under the v1 single-color schema: WRONG_CASE → Red even unassessed, **CITE_UNCONFIRMED → amber "Check Cite" card, never Red** (forced badge; agent blocks still render), all UNLOCATABLE-no-text statuses → Gray "Unable to verify" with status-specific explanations, otherwise the floor-enforced `assessment` column is authoritative; `crosscheck_flags` render as amber **flag chips** on finding AND green verified cards via `_crosscheck_flag_lines` — flags never move lanes; returns `ReportStats(path, findings, check_cite, verified, unable)`), plus `run.json` reproducibility stamps (`_update_run_json`). Prompt templates are versioned files in `src/citation_verifier/prompts/` — **editing one means a new version + re-recording the corpora cassettes** (the fidelity test pins assess-v1 to the recorded prompt byte-for-byte). Legacy functions intact: `wave1_verify_and_download()`, `wave2_fallback_and_download()`, `merge_claims()` (now passes `claim_id`/`cited_for` through), `check_quotes()` (§6.4: derives ≥2-word double-quoted spans from proposition/brief_sentence when `quoted_text` is empty, and writes `quote_floor` — FABRICATED or CLOSE<0.75 → "Yellow"; CLOSE in [0.75, 0.85) is transcription-noise band, no floor — see `_quote_floor`; **OCR gate** sourced per opinion from `opinions/ocr_status.json` (CL `extracted_by_ocr`) and routed through `quote_matcher.verify_quote` — `rn`/`O`/`l` rules off by default when unknown), `metadata_check()`, `generate_report()`, `_find_substantive_sibling()` (short-order sibling-cluster swap; CSV written after downloads so swapped URLs persist). CLI: `python -m citation_verifier verify-propositions <workdir> <extract\|verify\|merge\|check-quotes\|crosscheck\|triage\|assess\|apply-assessments\|report\|full>` (`--document <path>` for extract / the full chain; `--executor jobs\|sdk\|api` + `--model` select the LLM transport (sdk = headless `AgentSDKExecutor`, needs `claude login` credentials; api = `MessagesAPIExecutor`, needs `ANTHROPIC_API_KEY`, `--batch` for the Batches API); `--replay <jsonl>` for offline replay, wins over `--executor`; `--prescreen` enables triage's Haiku hints; `full` chains verify → merge → check-quotes → crosscheck → triage → assess → apply-assessments → report and stops at extract-pending/assess-pending until verdicts complete) and `verify-brief <workdir> [--wave1\|--wave2\|--merge\|--check-quotes\|--metadata-check\|--report\|--full]` (legacy) |
| `report_template.py` | HTML report template (proposition-verifier style). Step 7: "Check cite" dashboard stat + `sev-orange` issue rows for the §6.9 Check Cite lane, `_build_flags` amber flag chips (§6.5 crosscheck flags on finding and verified cards), all-clear banner suppressed when check-cite items exist |
| `__main__.py` | CLI (single-citation verify + `verify-brief` subcommand) |

### Tests and tools (`tests/`)

| File | Purpose |
|------|---------|
| `test_verifier.py` | 101 unit tests (mocked API calls) |
| `test_async_verifier.py` | 29 async parity tests (sync/async behavior equivalence) |
| `test_client_html.py` | Tests for prefer_html and PDF fallback in client |
| `test_brief_pipeline.py` | Legacy pipeline tests (merge, wave1, wave2, full_pipeline) — run against the `brief_pipeline` alias |
| `test_proposition_pipeline.py` | New-in-step-2 behavior: matched_case_name accessor, slug linkage (incl. frozen-Withers reproduction), verbs, alias, CLI |
| `test_false_negatives.py` | Regression tests against real CourtListener API |
| `test_executor.py` | Executor protocol + RecordedExecutor replay tests |
| `test_scoring.py` | derive_color table + workdir prediction/scoring tests |
| `test_assessment_corpora.py` | Structural invariants of the frozen assessment corpora |
| `test_assessment_regression.py` | Offline assessment baselines. v1: Withers 14/19 yellows (post-§6.4 floors), A/B 56/61. **assess-v2** (2026-06-12 re-record): Withers 16/19 yellows / reds 3/3 / greens 4 over-flags (≤2 guardrail miss, flagged), A/B 55/61 (90%), lenient set {payne-03} only |
| `build_assessment_corpora.py` | Builds/refreshes `tests/data/assessment_corpora/` (idempotent) |
| `test_ab_runner.py` | Offline tests for `tools/ab_test_runner.py` (RecordedExecutor seam; payne 23/27, wainwright 33/34) |
| `test_parser_diagnostics.py` | eyecite vs our parser comparison |
| `test_cl_api_issues.py` | Documents and tests CL API limitations |
| `extract_citations_batch.py` | Batch PDF citation extraction (reads from `scratch/hallucination_opinions/`) |
| `extract_hallucination_citations.py` | Hallucination keyword classifier (imported by batch extractor) |
| `verify_from_csv.py` | Iterative CSV-based verification (master workflow — see below) |
| `verify_sample_citations.py` | Sample and verify citations from JSON extraction results (ad-hoc exploration) |
| `data/known_real_citations.json` | 5-case real citation regression corpus |
| `data/known_fake_citations.json` | 8-case confirmed hallucination corpus |
| `data/cl_api_issues.json` | 5 documented CL API issues with workarounds |
| `data/assessment_corpora/` | Frozen assessment workdirs + recorded LLM cassettes (replay harness for the assessment layer — see its README) |
| `data/results/` | Timestamped extraction and verification output (gitignored) |

### Other

| File | Purpose |
|------|---------|
| `scratch/` | Working directory for iterative verification workflow (see `scratch/README.md`) |
| `scratch/citations_for_review.csv` | Master CSV — 525 citations with verification results and QC status |
| `scratch/TODO.md` | Bug/feature tracking with prioritized items |
| `scratch/flp_contributions.md` | Drafted contributions to Free Law Project (with submission checklists) |
| `scratch/flp_findings.csv` | Flagged results from Debug page for CL issue evidence (auto-created) |
| `briefs/` | Working directories for `/verify-brief` skill runs (frozen for old runs). Each brief gets `<name>/claims.csv`, `opinions/`, `report.html`. |
| `matters/` | Working directories for `/proposition-verifier` runs (design §2; created on first use) |
| `tools/ab_test_runner.py` | A/B harness (§9): runs the assess verb over copies of the frozen corpora with a named config (`tests/ab_test_configs.json`), scores via `scoring.score_workdir`; `--replay` scores the cassettes offline; the prompt comes from the byte-pinned template, not a local copy. `tests/ab_test_cases.json` stays the human-review ledger; `include_hints` configs require `prompt_version: assess-v2` (v1 is byte-pinned, no hint) and run a Haiku prescreen phase into the copy's claims before assess — configs `opus-v2` / `opus-v2-hints` are the §6.7 A/B arms (run 2026-06-13: hints hurt → prescreen default OFF); live runs score gap-tolerantly (skip-mode `RecordedExecutor`, dropped claims reported) and configs may set `"executor": "api"` — `opus-v2-api` is the F1 validation arm, `sonnet-v1-api` the pinned-model F2 prerequisite; `hybrid-v2-api` is the F2 hybrid-routing validation arm (`route: hybrid`, `fast_model: claude-sonnet-5` + `full_model: claude-opus-4-8` via `run_assess_hybrid`) — since the frozen corpora predate `triage_track`, `run_ab_config` runs `run_triage` on the copy first so Sonnet has fast-track claims to work, then hard-fails (`SystemExit`) if the mix comes back `fast==0`, so the arm can't pass vacuously with everything routed to Opus |
| `docs/plans/` | Implementation plans and design docs |
| `docs/retrospectives/` | Post-run retrospectives and skill test feedback (date-prefixed, e.g. `2026-03-04-verify-brief-valve-v-rothschild.md`) |
| `.replit` | Replit config (deployment, workflows, `MODE=public`) |
| `replit.nix` | Nix dependencies for Replit (python311Full) |

## Replit Deployment (Public Mode)

Set `MODE=public` to serve only the Retrieve page publicly. The Debug page and QC page are blocked.

- `/` serves `get.html` (Retrieve, nav hidden), `/get` redirects to `/`, `/debug`, `/qc`, `/api/qc/*`, and `/api/flag-for-flp` return 404
- All shared API routes (`/api/verify`, downloads, health) remain available
- One-way git flow: develop locally, push to GitHub, pull on Replit (`git fetch origin && git reset --hard origin/main`)
- Redeploy after code changes: `git fetch origin && git reset --hard origin/main && rm -rf .venv`, then Run/Publish
- CL API key stored in Replit's `.env` (Secrets tab)

## Environment

**Always activate the virtual environment before running any commands.**

```bash
# macOS/Linux
source venv/bin/activate
# Windows (Git Bash)
source venv/Scripts/activate
```

### API Configuration

- API token in `.env` file: `COURTLISTENER_API_TOKEN=...`
- `.env` is in `.gitignore` -- never commit it
- Get token at: https://www.courtlistener.com/ -> Profile -> API Keys
- All API requests have a 15-second timeout and 1-second rate limiting

## Testing

```bash
# Unit tests (mocked, no API calls)
pytest tests/test_verifier.py -v

# False negative regression (hits real API, needs token)
pytest tests/test_false_negatives.py -v

# Parser diagnostics
pytest tests/test_parser_diagnostics.py -v -s

# CL API issue workarounds
pytest tests/test_cl_api_issues.py -v

# Manual single citation
python -m citation_verifier "Obergefell v. Hodges, 576 U.S. 644 (2015)"

# Batch extraction from PDFs
python tests/extract_citations_batch.py

# Sample verification
python tests/verify_sample_citations.py --sample-size 50
```

## Iterative Verification Workflow

The master state file is `scratch/citations_for_review.csv`. Full workflow documentation is in `scratch/README.md`.

### CSV verification columns

| Column | Values |
|--------|--------|
| `v_status` | `VERIFIED`, `LIKELY_REAL`, `POSSIBLE_MATCH`, `NOT_FOUND`, `SKIPPED`, (empty) |
| `v_confidence` | 0.0–1.0 |
| `v_url` | CourtListener match URL |
| `v_matched_name` | CL matched case name |
| `v_git_hash` | Code version |
| `qc_status` | `approved`, `rerun`, `duplicate`, `ignore`, `investigate`, `data`, (empty) |
| `qc_notes` | free text |

### Post-run checklist

After running `verify_from_csv.py`, prompt the user to:
1. Review NOT_FOUND and POSSIBLE_MATCH items from the JSON sidecar
2. Set `qc_status` on reviewed rows in the CSV
3. Update `scratch/TODO.md` with any new `investigate` items
4. Update `scratch/flp_contributions.md` §6 with any new `data` items

### CLI reference

```bash
python tests/verify_from_csv.py [options]
  --csv PATH          (default: scratch/citations_for_review.csv)
  --sample-size N     (default: 50)
  --seed N            (default: random)
  --all               verify all pending, no sampling
  --rerun-only        only rows where qc_status=rerun
  --dry-run           show what would be verified, don't call API
```

## Adding a New Opinion for Verification

To extract citations from a CourtListener opinion and run them through verification:

1. **Fetch the document text** via CL API: `GET /api/rest/v4/recap-documents/{id}/` and read `plain_text`
2. **Extract citations** using eyecite (`AhocorasickTokenizer` on Windows) + manual review of the text to catch citations eyecite misses
3. **Append rows** to `scratch/citations_for_review.csv` with parsed citation fields, leaving `v_*` columns empty and `qc_status` set to `rerun`
4. **Run verification**: `python tests/verify_from_csv.py --rerun-only`
5. **QC review**: Start web app (`python web/app.py`), review at http://localhost:8000/qc

The web app's batch loop is parallelized (asyncio.Queue with MAX_CONCURRENT=5). To start/stop: `python web/app.py` / `taskkill //PID <pid> //F`.

## Claude Code Skills

- **`/proposition-verifier`** — Thin trigger for the proposition pipeline (design §2/§9; supersedes `/verify-brief` for new runs). ~40 lines of pure orchestration: startup checks, workdir under `matters/<name>/` (+ `brief_metadata.json` for the report header), one `verify-propositions <workdir> full [--document <path>]` invocation, jobs-mode Agent-subagent dispatch when the CLI prints PENDING (subagents run the job's `prompt` verbatim and append one envelope line `{claim_id, prompt_version, model, fields}` to `jobs/<phase>_results.jsonl`; max ~5 parallel; never edit claims.csv — apply-assessments owns it), open `report.html` + chat summary. **All assessment criteria live in the versioned templates** in `src/citation_verifier/prompts/` — never add criteria to the SKILL (that's a new prompt version + re-record). Lives at `.claude/skills/proposition-verifier/SKILL.md`.

- **`/verify-brief`** (FROZEN — kept for old `briefs/` runs; use `/proposition-verifier` for new work) — Multi-phase legal brief citation verifier. Uses `brief_pipeline.py` for mechanical work (batch verify, download, merge, quote check, metadata check, report generation). LLM orchestrates extraction (Phase 1a/1c) and assessment (Phase 2 via triage + Opus subagents). Generates proposition-verifier-style interactive HTML report with collapsible findings, paired blockquotes, and methodology disclosure. Output: `claims.csv` + `report.html` in `briefs/<name>/`. CLI: `python -m citation_verifier verify-brief <workdir> [--wave1|--wave2|--merge|--check-quotes|--metadata-check|--report|--full]`. Column schema: Phase 1c extracts `proposition`, `quoted_text` (JSON array), `brief_sentence` (surrounding brief sentence + parenthetical). Phase 1d populates `quote_check` (per-quote JSON with similarity + matched_passage) and `quote_check_worst`. Phase 2c Opus subagents write three agent-authored blocks: `brief_block` (orange box in report), `opinion_block` (green box — empty when no useful single passage), `finding_analysis` (prose), plus `assessment` and `badge_label`. The deterministic `matched_passage` is given to the agent as a hint; the agent decides what to show. The template falls back to `brief_sentence` + deterministic `matched_passage` (when similarity ≥ 0.65) for legacy claims.csv data that pre-dates agent-authored blocks. **Empty-opinion_block rule:** for "Case on unrelated subject" Reds (pure topic mismatch — real case cited for a fabricated proposition) and "Citation resolves to different case" Reds (CL returned a different opinion than the brief named), agents leave `opinion_block` empty and the analysis leads with a one-sentence subject-matter framing — opinion quotes add noise, not signal. **Badge labels** include "Case on unrelated subject" (the new pure-topic-mismatch badge — distinct from generic "Not supported by cited case" which is for topically-adjacent partial fits). Known issue: Phase 1c sometimes omits the full reporter citation in `cited_case` (e.g. "Camp v. Pitts" instead of "Camp v. Pitts, 411 U.S. 138 (1973)"), causing merge mismatches — the skill prompt enforces exact citation text, and `merge_claims()` reports unmatched claims with details. Retrospectives: `docs/retrospectives/`.

- **`/file-issue`** — Interactive coach for filing effective GitHub issues. Guides through duplicate search, evidence gathering, repo norm study, and drafting. Use it when filing issues on FLP repos (or any repo). Catches the antipatterns that get issues ignored: tentative framing, insufficient examples, no methodology, no cross-references, no root cause theory. Lives at `.claude/skills/file-issue/SKILL.md`.

## Common Pitfalls

- **CL API response structure**: Citation lookup returns `[{citation, clusters: [...]}]`, not a flat list. Always access `lr["clusters"]`.
- **Court IDs**: eyecite sometimes returns CL court IDs directly (e.g. `"almd"`) instead of abbreviations. `lookup_court_id()` handles both.
- **State courts**: The court map only covers federal courts. State court IDs from eyecite are compared via direct string match. Use `state_reporter_map.py` to infer state from regional reporters.
- **RECAP docket param**: The `docket` parameter on the search API is unreliable. Use `q` with a quoted string + client-side filter instead.
- **Windows console**: Avoid Unicode emoji in CLI output -- use ASCII status labels like `[OK]`, `[?]`, `[X]`.
- **Windows Git Bash**: `head`, `tail`, `grep`, `cut` are not available. Use Python or dedicated tools instead. `taskkill` flags need `//` prefix (e.g. `taskkill //PID 1234 //F`) to avoid MSYS2 path conversion. The Python executable is `venv/Scripts/python.exe` (not `python` or `python3`).
- **eyecite on Windows**: `hyperscan` module is not available. Use `AhocorasickTokenizer` instead of `HyperscanTokenizer` for citation extraction.
- **Matched case name**: read it ONLY via `result.matched_case_name` (accessor on `VerificationResult`). The caption lives under stage-specific `raw_response_summary` keys (`matched_case_name`, `best_case_name`, `cl_case_name`, `case_name`) — reading any single key directly is the bug that left `matched_name` blank in `verification_results.csv` on the batch path (design §11 bug 1, fixed 2026-06-11). Same rule for **matched court**: `result.matched_court` / `result.matched_court_id` (stashed at opinion-download time by `_download_opinion` — citation-lookup clusters carry NO court field, so the download metadata fetch is the only no-extra-API-call source; `client.get_opinion_text_with_metadata` returns both `court` full name and `court_id`). Persisted as `matched_court`/`matched_court_id` columns in `verification_results.csv`; empty for never-downloaded results (NOT_FOUND, WRONG_CASE) and legacy CSVs — the crosscheck court check skips those.
- **VerificationResult fields**: top-level `matched_*` and `diagnostics` are gone; everything moved under `result.final_ids` (cluster_id, opinion_id, docket_id, recap_document_id, absolute_url, text_source) and `result.warnings` (typed `Warning` with `.category` enum). Use `result.final_ids.docket_id is not None and result.final_ids.cluster_id is None` as the RECAP-vs-opinion discriminator. `VERIFIED_VIA_RECAP` populates `recap_document_id`; `VERIFIED_DOCKET_ONLY` leaves it `None`. `VERIFICATION_INCOMPLETE` nulls all final_ids per design §2.8 (consumers must not mistake INCOMPLETE for partial verification). See `src/citation_verifier/models.py`, `docs/plans/2026-05-20-citation-verifier-refactor-design-v2.md`, and `docs/consumer-surface-manifest.md` for the full schema and consumer audit.
