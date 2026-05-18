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

1. **Citation Lookup API** (`/api/rest/v4/citation-lookup/`) - Resolves reporter citations (e.g. `576 U.S. 644`). If found, verifies case name matches before returning VERIFIED. If the citation exists but belongs to a different case, returns POSSIBLE_MATCH with a name-mismatch diagnostic.

2. **Opinion Search** (`/api/rest/v4/search/?type=o`) - Fuzzy search by case name, court, and date range (+/- 1 year). Retries without court filter if no results.

3. **RECAP Search** (`/api/rest/v4/search/?type=r`) - Searches PACER docket data. When a docket is found, queries the **docket-entries API** (`/api/rest/v4/docket-entries/`) filtered by the cited year to find the actual opinion/order document. Important: RECAP `dateFiled` is the case filing date, NOT the opinion date -- always use `entry_date_filed` from individual docket entries.

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
| `text_cleaner.py` | Contamination phrase removal (adapted from CaseStrainer) |
| `parser.py` | Citation parsing (eyecite + regex + abbreviation normalization + eyecite factory) |
| `client.py` | CourtListener API wrapper (rate limiting, 15s timeout, 429 retry). Both sync (`CourtListenerClient`) and async (`AsyncCourtListenerClient`) have `get_opinion_text()` and `get_opinion_text_with_metadata()` for fetching full opinion/RECAP text + metadata. Supports `prefer_html=True` for raw HTML and PDF download fallback. **Canonical opinion-text fallback chain (`_extract_opinion_text`):** `plain_text` -> `html_with_citations` -> `html` -> `html_lawbox` -> `html_columbia` -> `html_anon_2020` -> `xml_harvard`. State opinions in CL frequently have empty `plain_text` but populate `html_lawbox`/`html_with_citations`/`xml_harvard` -- never roll your own fetcher that only checks `plain_text`. The 2026-05-06 state-court smoke test found this gap drops state-tier full-text coverage from 86% to 9%. Tests for each fallback field live in `tests/test_client_opinion_text.py::test_falls_back_to_secondary_html_fields`. |
| `verifier.py` | Core 3-step pipeline (shared helpers + thin sync/async wrappers) |
| `brief_pipeline.py` | Brief verification pipeline: `wave1_verify_and_download()`, `wave2_fallback_and_download()`, `merge_claims()`, `check_quotes()`, `metadata_check()`, `generate_report()`. Download phase includes `_find_substantive_sibling()`: when the matched cluster's opinion is < 3000 chars (e.g. a vacatur/amendment order), it searches sibling clusters on the same docket for a substantive merits opinion and swaps `result.matched_url` to the sibling cluster (motivating case: Hertz 3d Cir. has cluster 10124964 = 2-page vacatur order and cluster 10265999 = 52-page merits opinion on the same docket). `verification_results.csv` is written *after* downloads so the swapped URL persists. CLI: `python -m citation_verifier verify-brief <workdir> [--wave1\|--wave2\|--merge\|--check-quotes\|--metadata-check\|--report\|--full]` |
| `report_template.py` | HTML report template (proposition-verifier style) |
| `__main__.py` | CLI (single-citation verify + `verify-brief` subcommand) |

### Tests and tools (`tests/`)

| File | Purpose |
|------|---------|
| `test_verifier.py` | 101 unit tests (mocked API calls) |
| `test_async_verifier.py` | 29 async parity tests (sync/async behavior equivalence) |
| `test_client_html.py` | Tests for prefer_html and PDF fallback in client |
| `test_brief_pipeline.py` | Tests for brief pipeline (merge, wave1, wave2, full_pipeline) |
| `test_false_negatives.py` | Regression tests against real CourtListener API |
| `test_parser_diagnostics.py` | eyecite vs our parser comparison |
| `test_cl_api_issues.py` | Documents and tests CL API limitations |
| `extract_citations_batch.py` | Batch PDF citation extraction (reads from `scratch/hallucination_opinions/`) |
| `extract_hallucination_citations.py` | Hallucination keyword classifier (imported by batch extractor) |
| `verify_from_csv.py` | Iterative CSV-based verification (master workflow — see below) |
| `verify_sample_citations.py` | Sample and verify citations from JSON extraction results (ad-hoc exploration) |
| `data/known_real_citations.json` | 5-case real citation regression corpus |
| `data/known_fake_citations.json` | 8-case confirmed hallucination corpus |
| `data/cl_api_issues.json` | 5 documented CL API issues with workarounds |
| `data/results/` | Timestamped extraction and verification output (gitignored) |

### Other

| File | Purpose |
|------|---------|
| `scratch/` | Working directory for iterative verification workflow (see `scratch/README.md`) |
| `scratch/citations_for_review.csv` | Master CSV — 525 citations with verification results and QC status |
| `scratch/TODO.md` | Bug/feature tracking with prioritized items |
| `scratch/flp_contributions.md` | Drafted contributions to Free Law Project (with submission checklists) |
| `scratch/flp_findings.csv` | Flagged results from Debug page for CL issue evidence (auto-created) |
| `briefs/` | Working directories for `/verify-brief` skill runs. Each brief gets `<name>/claims.csv`, `opinions/`, `report.html`. |
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

- **`/verify-brief`** — Multi-phase legal brief citation verifier. Uses `brief_pipeline.py` for mechanical work (batch verify, download, merge, quote check, metadata check, report generation). LLM orchestrates extraction (Phase 1a/1c) and assessment (Phase 2 via triage + Opus subagents). Generates proposition-verifier-style interactive HTML report with collapsible findings, paired blockquotes, and methodology disclosure. Output: `claims.csv` + `report.html` in `briefs/<name>/`. CLI: `python -m citation_verifier verify-brief <workdir> [--wave1|--wave2|--merge|--check-quotes|--metadata-check|--report|--full]`. Column schema: Phase 1c extracts `proposition`, `quoted_text` (JSON array), `brief_sentence` (surrounding brief sentence + parenthetical). Phase 1d populates `quote_check` (per-quote JSON with similarity + matched_passage) and `quote_check_worst`. Phase 2c Opus subagents write three agent-authored blocks: `brief_block` (orange box in report), `opinion_block` (green box — empty when no useful single passage), `finding_analysis` (prose), plus `assessment` and `badge_label`. The deterministic `matched_passage` is given to the agent as a hint; the agent decides what to show. The template falls back to `brief_sentence` + deterministic `matched_passage` (when similarity ≥ 0.65) for legacy claims.csv data that pre-dates agent-authored blocks. **Empty-opinion_block rule:** for "Case on unrelated subject" Reds (pure topic mismatch — real case cited for a fabricated proposition) and "Citation resolves to different case" Reds (CL returned a different opinion than the brief named), agents leave `opinion_block` empty and the analysis leads with a one-sentence subject-matter framing — opinion quotes add noise, not signal. **Badge labels** include "Case on unrelated subject" (the new pure-topic-mismatch badge — distinct from generic "Not supported by cited case" which is for topically-adjacent partial fits). Known issue: Phase 1c sometimes omits the full reporter citation in `cited_case` (e.g. "Camp v. Pitts" instead of "Camp v. Pitts, 411 U.S. 138 (1973)"), causing merge mismatches — the skill prompt enforces exact citation text, and `merge_claims()` reports unmatched claims with details. Retrospectives: `docs/retrospectives/`.

- **`/file-issue`** — Interactive coach for filing effective GitHub issues. Guides through duplicate search, evidence gathering, repo norm study, and drafting. Use it when filing issues on FLP repos (or any repo). Catches the antipatterns that get issues ignored: tentative framing, insufficient examples, no methodology, no cross-references, no root cause theory. Lives at `.claude/skills/file-issue/SKILL.md`.

## Common Pitfalls

- **CL API response structure**: Citation lookup returns `[{citation, clusters: [...]}]`, not a flat list. Always access `lr["clusters"]`.
- **Court IDs**: eyecite sometimes returns CL court IDs directly (e.g. `"almd"`) instead of abbreviations. `lookup_court_id()` handles both.
- **State courts**: The court map only covers federal courts. State court IDs from eyecite are compared via direct string match. Use `state_reporter_map.py` to infer state from regional reporters.
- **RECAP docket param**: The `docket` parameter on the search API is unreliable. Use `q` with a quoted string + client-side filter instead.
- **Windows console**: Avoid Unicode emoji in CLI output -- use ASCII status labels like `[OK]`, `[?]`, `[X]`.
- **Windows Git Bash**: `head`, `tail`, `grep`, `cut` are not available. Use Python or dedicated tools instead. `taskkill` flags need `//` prefix (e.g. `taskkill //PID 1234 //F`) to avoid MSYS2 path conversion. The Python executable is `venv/Scripts/python.exe` (not `python` or `python3`).
- **eyecite on Windows**: `hyperscan` module is not available. Use `AhocorasickTokenizer` instead of `HyperscanTokenizer` for citation extraction.
- **VerificationResult fields**: URL attribute is `matched_url` (not `court_listener_url`). For citation-lookup and opinion-search matches, the CL cluster id is in `matched_cluster_id` and resolves at `/api/rest/v4/clusters/<id>/`. For RECAP-fallback matches (either a specific document or a docket-only match), the docket id is in `matched_docket_id` and resolves at `/api/rest/v4/dockets/<id>/`; `matched_cluster_id` is `None` for those. Only one of the two namespace fields is populated per result — use `matched_docket_id is not None` as the discriminator if you need to choose an endpoint. `diagnostics` is `List[Diagnostic]` — each has `.category` (name/court/date/docket/cite/recap/info) and `.message` (human-readable text). `__str__` returns `.message` for backwards compatibility. Join `.message` with `"; "` when displaying as a single string.
