# Citation Verifier

Verify legal citations against [CourtListener](https://www.courtlistener.com/). Catches hallucinated case citations from AI tools by checking whether a citation actually exists and belongs to the case it claims to.

The project has two layers:

1. **Citation verifier** — does a citation resolve to a real case, and is it the case it claims to be? Fully deterministic: API lookups plus rule-based name/court/date matching, no LLM. (The core 3-step pipeline below.)
2. **Proposition verifier** — does the cited case actually *support the proposition it's cited for*? A hybrid pipeline over the matched opinions: **deterministic checks** (quote fidelity via fuzzy string matching, citation crosschecks) plus **LLM judgment** only where it's genuinely needed (extracting claims from the document, assessing whether the opinion supports the proposition). Produces an interactive HTML report. See [Proposition Verification](#proposition-verification).

**Evals/benchmarks:** see [`EVALS.md`](EVALS.md) for the citation-resolution and proposition-support corpora (including 511 fabricated citations mined from Damien Charlotin's hallucination database), all offline-replayable via cassettes.

> **Upgrading from v0.2?** The v0.3 release reshapes `VerificationResult` and the `Status` enum (no more `LIKELY_REAL` / `POSSIBLE_MATCH`; new `WRONG_CASE` / `VERIFIED_VIA_RECAP` / `VERIFICATION_INCOMPLETE`). See [`CHANGELOG.md`](CHANGELOG.md#migrating-from-v02-to-v03) for the field-by-field migration table.

## Try It

**[Verify and Retrieve](https://verify-and-retrieve.replit.app/)** -- paste citations, verify them against CourtListener, and download the opinion text or PDFs. No installation needed.

## How It Works

```
Input: "Smith v. Jones, 2018 WL 301424 (S.D.N.Y. Mar. 5, 2018)"
  |
  +-- Step 1: Citation Lookup API (fast, precise)
  |     Found + name matches? -> VERIFIED + CourtListener link
  |     Found but caption diverges? -> caption_investigation:
  |       party-overlap holds -> VERIFIED + warning
  |       party-overlap fails  -> WRONG_CASE ("citation belongs to ...")
  |
  +-- Step 2: Opinion Search (fuzzy fallback)
  |     Search by case name + court + date range
  |     Retries without court filter if no results
  |     Score results by name/court/date/citation similarity
  |
  +-- Step 3: RECAP Search (docket/PACER fallback)
        Search by docket number first (if available),
        then by case name (with/without court filter).
        Query docket-entries API for documents near the cited date
```

### Statuses

| Status | Meaning |
|--------|---------|
| `VERIFIED` | Citation lookup or opinion search resolved to the cited case |
| `VERIFIED_PARTIAL` | A parallel cite resolved; the primary reporter didn't (e.g. NY A.D.3d + slip op) |
| `VERIFIED_VIA_RECAP` | Matched to a specific RECAP document (federal PACER) |
| `VERIFIED_DOCKET_ONLY` | Docket found, but the specific cited opinion couldn't be pinned |
| `CITE_UNCONFIRMED` | The case was matched by a fallback name search, but the *cited reporter/WL location* couldn't be tied to it (CL lists a different cite in the same reporter family, or the match is a bare RECAP docket with no document). UI label: **"Check Cite."** Never lowers the score |
| `WRONG_CASE` | Citation resolves, but to a different case (caption divergence + party-overlap fails) |
| `NOT_FOUND` | No match found in any search step |
| `INSUFFICIENT_DATA` | The parse was too weak to anchor a confident search (no court *and* no year); retrying won't help until the input improves |
| `VERIFICATION_INCOMPLETE` | CourtListener infrastructure failure (5xx / timeout); rerun |

Trust ordering: VERIFIED family > `CITE_UNCONFIRMED` > `WRONG_CASE` > `NOT_FOUND`.

### Diagnostics

When a citation isn't fully verified, the tool explains why:

- **"Citation exists but belongs to a different case"** -- the reporter citation is real but for a different case (common AI hallucination pattern)
- **"Name mismatch"** / **"Name differs"** -- case name similarity issues
- **"Court mismatch"** -- cited court doesn't match the found case
- **"Date mismatch"** / **"Date close"** -- year discrepancies
- **"Reporter citation could not be confirmed"** -- CourtListener doesn't have the citation on file
- **"Cited citation contradicted"** -- the matched case lists a *different* citation in the same reporter family than the one cited (a Check Cite signal)
- **"Found in RECAP"** -- case found in PACER docket data, not the opinions database
- **"We found a possible docket match"** -- docket found but no specific document verified

## Installation

```bash
pip install -e .          # core library + CLI
pip install -e ".[web]"   # adds web app (FastAPI, uvicorn)
```

## Configuration

Create a `.env` file in the project root:

```
COURTLISTENER_API_TOKEN=your_token_here
```

Get a free API token at https://www.courtlistener.com/ (Profile > API keys).

The token is required for the Citation Lookup API (Step 1). The Search API (Steps 2-3) works without a token but is rate-limited.

## Usage

### Web App

The quickest way to use the tool — no installation required for end users.

```bash
pip install -e ".[web]"
python web/app.py
# Open http://localhost:8000
```

The app has three pages:

- **Retrieve** (`/`) -- Verify citations and download the matched opinion text or PDFs from CourtListener. Quick search + deep search workflow.
- **QC** (`/qc`) -- Review verification batches and assign QC status (internal use).
- **Debug** (`/debug`) -- Detailed verification with confidence scores, diagnostics, CSV export, and FLP flagging.

Results stream via SSE as each citation completes. Batches capped at 50 citations.

**Public mode:** Set `MODE=public` to expose only the Retrieve page (used for the hosted Replit deployment). Debug and QC routes return 404.

### Command Line

```bash
# Single citation
python -m citation_verifier "Obergefell v. Hodges, 576 U.S. 644 (2015)"

# Multiple citations
python -m citation_verifier "Case One, 576 U.S. 644 (2015)" "Case Two, 999 F.3d 1 (2021)"

# From a file (one citation per line)
python -m citation_verifier --file citations.txt

# JSON output
python -m citation_verifier --json "Obergefell v. Hodges, 576 U.S. 644 (2015)"
```

Exit codes (useful for scripting/CI), highest wins in a mixed batch:
- `0` — all citations verified (or skipped)
- `1` — at least one `NOT_FOUND`
- `2` — at least one `VERIFICATION_INCOMPLETE` (infra failure; rerun)
- `3` — at least one `INSUFFICIENT_DATA` (parse too weak to verify; fix the input). Outranks the others — retry and hallucination analysis are moot until the parse is fixed.

### Python API

```python
from citation_verifier import CitationVerifier

verifier = CitationVerifier()
result = verifier.verify("Obergefell v. Hodges, 576 U.S. 644 (2015)")

print(result.status)                       # Status.VERIFIED
print(result.final_ids.absolute_url)       # https://www.courtlistener.com/opinion/2812209/...
print(result.headline_confidence)          # 0.96 (or None)
for w in result.warnings:                  # List[Warning], each with .category and .message
    print(f"[{w.category.value}] {w.message}")
```

#### Pre-parsed citations (batch pipelines)

When processing PDFs with eyecite, you can pass pre-parsed citations directly to avoid the lossy string round-trip that drops court, month, and day metadata:

```python
from eyecite import get_citations
from eyecite.models import FullCaseCitation
from citation_verifier import CitationVerifier
from citation_verifier.parser import parsed_citation_from_eyecite

text = "Obergefell v. Hodges, 576 U.S. 644 (2015)"
cite = next(c for c in get_citations(text) if isinstance(c, FullCaseCitation))
parsed = parsed_citation_from_eyecite(cite, raw_text=text)

verifier = CitationVerifier()
result = verifier.verify(text, parsed=parsed)
```

## Supported Citation Formats

- Standard reporters: `576 U.S. 644`, `999 F.3d 1`, `584 S.W.2d 716`
- WestLaw: `2018 WL 301424`
- California style: `(2022) 76 Cal.App.5th 685`
- Docket numbers: `Case No. 24-cv-9429`, `No. 17-cv-12676`, shorthand `C15-1228-JCC`
- Federal parentheticals: `(S.D.N.Y. 2018)`, `(M.D. Ala. July 6, 2018)`
- Reversed date/court: `(Feb. 5, 2026 SDNY)`
- Complex party names: `Macy's Texas, Inc. v. D.A. Adams & Co.`
- Abbreviations auto-expanded: `Cnty.` → `County`, `Dep't` → `Department`, `Corp.` → `Corporation`, etc.

## Proposition Verification

The citation verifier answers *"is this a real case?"* The **proposition verifier** answers the harder question: *"does the cited case actually support the proposition it's cited for?"* It's built for vetting briefs, motions, and opinions for misrepresented or hallucinated authority.

It runs as a pipeline of small, idempotent **verbs** over a working directory (one per document, under `matters/<name>/`). Each verb writes its output to disk and no-ops if that output already exists, so you can resume by re-running. Only two verbs use an LLM — `extract` and `assess`. Everything else is deterministic code, **including quote verification**: `check-quotes` compares each quoted passage against the downloaded opinion text with normalized fuzzy string matching (`quote_matcher.verify_quote`, built on `difflib.SequenceMatcher`), so the same inputs always produce the same quote verdicts — no model judgment involved. The LLM-assisted verbs run through an **executor protocol** with three transports: in-session Agent subagents ("jobs" mode, the default), a headless `claude-agent-sdk` executor (`--executor sdk`, needs `claude login`), and a recorded-cassette replay for offline/deterministic runs (`--replay`).

### The verbs

| Verb | What it does |
|------|--------------|
| `extract` | (LLM) Document → `claims.csv` + table-of-authorities / body citation lists |
| `verify` | Runs every citation through the core verifier (batched) and downloads matched opinion text |
| `merge` | Joins claims to verification results and links each claim to its opinion file |
| `check-quotes` | (Deterministic) Checks quoted language against the opinion text by fuzzy string matching; flags fabricated/altered quotes and sets the quote floor |
| `crosscheck` | (Deterministic) Flags: TOA-vs-body cite variants, cited-vs-matched court, pincite sanity |
| `triage` | Decides assessment depth per claim (full vs. fast) |
| `assess` | (LLM) Reads the opinion and judges whether it supports the proposition |
| `apply-assessments` | Folds LLM verdicts back into `claims.csv`, enforcing the quote floor |
| `report` | Renders `claims.csv` → interactive `report.html` |
| `full` | Chains them: `[extract →] verify → merge → check-quotes → crosscheck → triage → assess → apply-assessments → report` |

### Scoring (two-axis)

Each finding is colored from two independent axes — the **citation status** (does the case/cite resolve?) and the **support** judgment (does the opinion back the proposition?):

- **Red** — wrong case, or the case doesn't support the proposition
- **Amber / "Check Cite"** — `CITE_UNCONFIRMED`: the case matched but the cited reporter location couldn't be confirmed (never forced Red)
- **Gray / "Unable to verify"** — no opinion text was available to assess
- **Yellow** — the deterministic quote floor (fabricated or low-similarity quotes) caps an otherwise-clean claim
- **Green** — verified and supported

A key invariant: reporter-cite mismatches and crosscheck flags **never move the score** — they surface as flags/badges, not as confidence changes.

### Running it

The friendly path is the **`/proposition-verifier`** Claude Code skill, which orchestrates the whole `full` chain (including dispatching the LLM jobs) from a single document. Directly via the CLI:

```bash
# One document end-to-end (jobs mode pauses for LLM verbs; rerun to ingest verdicts)
python -m citation_verifier verify-propositions matters/my-brief full --document brief.pdf

# Run a single verb
python -m citation_verifier verify-propositions matters/my-brief report

# Headless, no in-session subagents (requires `claude login`)
python -m citation_verifier verify-propositions matters/my-brief full --document brief.pdf --executor sdk

# Offline deterministic replay of recorded verdicts
python -m citation_verifier verify-propositions matters/my-brief assess --replay verdicts.jsonl
```

Output lands in the workdir: `claims.csv` (the master state file) and `report.html` (the deliverable). Prompt templates are versioned files in `src/citation_verifier/prompts/` — editing one is a new prompt version and requires re-recording the assessment corpora cassettes.

`claims.csv` is a stable external interface (consumed downstream, e.g. by the `us-legal-research` memo-import skill). Every column — its producing verb, type, and allowed values — is documented in [`docs/claims-csv-schema.md`](docs/claims-csv-schema.md). The contract is versioned by `proposition_pipeline.CLAIMS_SCHEMA_VERSION`, stamped into each workdir's `run.json` as `schema_version`.

> A frozen `/verify-brief` skill and `brief_pipeline.py` alias remain for older `briefs/` runs; use `/proposition-verifier` for new work.

## Testing

```bash
# Unit tests (mocked, no API calls)
pytest tests/test_verifier.py -v

# False negative regression (hits real API, needs token)
pytest tests/test_false_negatives.py -v

# Parser diagnostics (eyecite vs our parser comparison)
pytest tests/test_parser_diagnostics.py -v

# CourtListener API limitation workarounds
pytest tests/test_cl_api_issues.py -v
```

`test_verifier.py` has 188 unit tests covering the full pipeline: citation lookup, name matching, caption investigation, opinion search, RECAP search, court corroboration, scoring and weight redistribution, docket number normalization, abbreviation expansion, surname matching, the eyecite factory function, the Check Cite (`CITE_UNCONFIRMED`) classifier, the `INSUFFICIENT_DATA` promotion, and the pre-parsed citation path. All API calls are mocked. `test_async_verifier.py` has 67 tests verifying sync/async behavior parity. The proposition pipeline, executor protocol, two-axis scoring, and frozen assessment corpora have their own suites (`test_proposition_pipeline.py`, `test_executor.py`, `test_scoring.py`, `test_assessment_regression.py`).

`test_false_negatives.py` runs against the real CourtListener API using the corpus in `tests/data/known_real_citations.json` (5 cases). `tests/data/known_fake_citations.json` contains 8 confirmed hallucinations for reference.

## Project Structure

```
src/citation_verifier/
  models.py          -- Data structures (statuses, parsed citations, results)
  court_map.py       -- Court abbreviation -> CourtListener ID mapping (federal courts)
  state_reporter_map.py -- Regional reporter -> state court mapping
  name_matcher.py    -- Multi-factor case name similarity scoring
  quote_matcher.py   -- Deterministic quote-fidelity checking (fuzzy string matching, no LLM)
  text_cleaner.py    -- Contamination phrase removal from extracted names
  parser.py          -- Citation parsing (eyecite + regex fallbacks + eyecite factory)
  client.py          -- CourtListener API wrapper with rate limiting
  cache.py           -- File-based verification result cache
  resolution_path.py -- Accumulator for per-stage resolution-path entries
  verifier.py        -- Core three-step verification pipeline
  __main__.py        -- CLI interface (verify / verify-propositions / verify-brief / verify-batch / audit-misses)
  # --- proposition-verification layer (PR #21) ---
  proposition_pipeline.py -- Proposition pipeline verbs (extract/verify/merge/check-quotes/crosscheck/triage/assess/apply-assessments/report)
  brief_pipeline.py  -- Deprecated alias of proposition_pipeline (legacy /verify-brief runs)
  executor.py        -- LLM executor protocol (jobs mode, headless SDK, recorded replay)
  scoring.py         -- Two-axis (cite-status x support) color derivation + offline corpus scoring
  report_template.py -- HTML report template (findings, Check Cite dashboard, flag chips)
  prompts/           -- Versioned LLM prompt templates (extract_v1, assess_v1, assess_v2, prescreen_v1)

web/
  app.py             -- FastAPI application (SSE streaming, public mode)
  static/get.html    -- Retrieve page (homepage, vanilla HTML/CSS/JS)
  static/index.html  -- Debug page (detailed verification)
  static/qc.html     -- QC review page

tests/
  test_verifier.py             -- Unit tests (mocked API)
  test_async_verifier.py       -- Sync/async parity tests
  test_false_negatives.py      -- Regression tests (live API)
  test_parser_diagnostics.py   -- Parser comparison diagnostics
  test_cl_api_issues.py        -- CL API limitation tests
  test_proposition_pipeline.py -- Proposition pipeline verbs + slug linkage
  test_executor.py             -- Executor protocol + recorded replay
  test_scoring.py              -- Two-axis color table + workdir scoring
  test_assessment_regression.py-- Offline assessment baselines (frozen corpora)
  extract_citations_batch.py   -- Batch PDF citation extraction
  verify_sample_citations.py   -- Sample and verify extracted citations
  data/                        -- Test fixtures, frozen corpora, and results

.claude/skills/                -- /proposition-verifier and /verify-brief skill definitions
matters/                       -- Working directories for /proposition-verifier runs
briefs/                        -- Working directories for legacy /verify-brief runs
scratch/                       -- Working notes and utility scripts (not part of the tool)
```

## Acknowledgments

This project is built on:

- [CourtListener](https://www.courtlistener.com/) from the [Free Law Project](https://free.law/) -- the legal research platform and API we verify citations against
- [eyecite](https://github.com/freelawproject/eyecite) -- the citation extraction library that powers our parser
- [CaseStrainer](https://github.com/jafrank88/CaseStrainer) by Jonathan Franklin -- our case name matching algorithm (`name_matcher.py`), contamination phrase removal (`text_cleaner.py`), and state reporter mapping (`state_reporter_map.py`) are adapted from CaseStrainer's approach to detecting hallucinated legal citations

## Match Confidence Scoring

When the verifier falls back to fuzzy search (Steps 2-3), match confidence is a weighted score:

| Component | Weight | Notes |
|-----------|--------|-------|
| Case name | 50% | SequenceMatcher similarity; compares defendants for "State v." style cases |
| Court | 20% | Exact match on CourtListener court ID |
| Date | 20% | Full credit for exact date; granular scoring for same month, same year, +/- 1 year |
| Docket no. | 5% | Normalized comparison (strips division prefix, judge suffix, expands shorthand) |
| Citation | 5% | Reporter citation or WL number found in CourtListener record |

RECAP docket-only matches (no specific document found) receive a 40% score discount.

## License

[MIT](LICENSE)
