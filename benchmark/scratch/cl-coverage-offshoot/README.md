# CL Coverage Offshoot

A measurement of how well CourtListener resolves cited cases via its
`/citation-lookup/` API, sub-classified by the discoverability mechanism
behind each miss. Output is a memo for the Free Law Project.

**Status:** complete (2026-05-16). May be re-run on a larger corpus
later. The plan is to eventually spin this off into its own repo —
until then it lives at `benchmark/scratch/cl-coverage-offshoot/`.

## Results

| Tier | Found via lookup | + verifier-auto | + manual | Total in CL | Denom | Lookup rate | Total coverage |
|---|---|---|---|---|---|---|---|
| SCOTUS | 45 | +0 | +0 | 45 | 45 | 100.0% | 100.0% |
| Circuit | 43 | +1 | +1 | 45 | 46 | 93.5% | 97.8% |
| State_COLR | 38 | +2 | +2 | 42 | 44 | 86.4% | 95.5% |
| State_IAC | 26 | +5 | +5 | 36 | 38 | 68.4% | 94.7% |
| Federal_District | 18 | +19 | +8 | 45 | 48 | 37.5% | 93.8% |
| **OVERALL** | **170** | **+27** | **+16** | **213** | **221** | **76.9%** | **96.4%** |

The 43 lookup misses (cases in CL that `/citation-lookup/` didn't
resolve) fall into a few patterns:

- **35** cases where the cluster exists but the citation index is
  incomplete — `citations[]` empty entirely (30, including 5 with a
  caption-divergent cited form from Rule 25(d) substitution / Doe
  reveal / SSA pseudonym), or populated with only the slip-op cite
  for 5 NY A.D.3d cases.
- **7** cases that are docket-only in RECAP, with no opinion cluster
  ever created.
- **1** is a pipeline-side normalization issue (extracted as
  `50 F 4th 432` instead of `50 F.4th 432`) — the cluster is fine in
  CL.

Full narrative: [`coverage_memo.docx`](coverage_memo.docx).

## Pipeline

The canonical pipeline is the numbered scripts below, run in order. All
write CSV/JSON to this directory. Re-running from scratch takes ~30–60
minutes depending on CL API responsiveness.

| # | Script | What it does |
|---|---|---|
| 10 | `10_mine_citing_opinions.py` | Mines 78 citing opinions from CL (federal + state, 2023–2026). Writes one `.txt` per cluster to `citing_opinions/`. |
| 11 | `11_run_extraction.py` | Drives `extract_citations.py` over each mined opinion. Writes one `.json` per cluster to `real_extractions/`. Uses Anthropic Haiku via `claude -p` rather than eyecite — eyecite has known issues extracting case names and parentheticals from PDF text and from opinions with slip-opinion placeholders or smart quotes. |
| 12 | `12_stratify.py` | Pre-filters short-form / foreign citations; dedups; applies K=5 per-opinion cap; stratifies 50 per tier across SCOTUS / Circuit / State_COLR / State_IAC / Federal_District. Writes `final_pool.csv` (all tiers post-dedup-and-cap) and `final_200.csv` (the 250-row stratified sample). |
| 13 | `13_lookup_coverage.py` | Phase 4: runs `/citation-lookup/` on every row of `final_200.csv`. Writes `coverage_per_citation.csv`, `coverage_per_tier.csv`, `coverage_summary.md`. |
| 15 | `15_staged_fallback_rigorous.py` | Phase 4c: citation-verifier name-based fallback against opinion search and RECAP search for the NOT_FOUND rows from Phase 4. Multi-factor scoring (name + court + date + docket). Writes `staged_fallback_rigorous_per_row.csv`. |
| 16 | `16_audit_rescues.py` | Phase 5: audits each rescue with cite-in-cluster cross-check, party-name presence, court_id match, and date proximity. Writes `audit_per_row.csv`. |
| 18 | `18_diagnose_recap_cases.py` | Sub-classifies each `in_recap` row (recap_doc_opinion_not_ingested / recap_doc_unavailable / recap_doc_not_opinion_typed). Writes `recap_diagnosis.csv`. Run after 17 (it reads `unified_review.csv` for the coverage column). |
| 17 | `17_build_unified_review.py` | Joins all phases + Phase 6 short-form dedup + `manual_corrections.csv` overrides + `recap_diagnosis.csv` subreasons. Derives the `coverage` and `diagnosis` columns. Writes the full audit trail (`unified_review.csv`) and a 9-column reviewer view (`unified_review_concise.csv`). |

Re-run order: 10 → 11 → 12 → 13 → 15 → 16 → 17 → 18 → 17 (run 17 a
second time to incorporate `recap_diagnosis.csv` into `unified_review.csv`).

`extract_citations.py` is the LLM extractor module imported by step 11.
Uses `claude -p` with the Haiku model. Schema includes month / day /
docket_number fields added 2026-05-15 to support the docket-number
fallback path for unpublished district court opinions.

## Data outputs

| File | What it is |
|---|---|
| `coverage_memo.docx` | The findings memo, drafted for FLP/CL (Word doc with charts; primary deliverable) |
| `unified_review.csv` | Full audit trail — 37 columns × 250 rows. The canonical row-by-row record of every decision the pipeline made. |
| `unified_review_concise.csv` | Reviewer-facing view — 9 columns × 250 rows. Filter on `coverage` for the headline number; on `diagnosis` for the mechanism. |
| `manual_corrections.csv` | 16 user-investigated rescues (7 original from the initial false-negative review: 3 Rule 25(d), 2 SSA pseudonym, 2 docket-only edge cases; plus 9 from a follow-up review of cases that initially appeared not-found: 5 NY A.D.3d parallel-cite gap, 2 Texas S.W.3d cluster-empty, 1 F.4th extraction mismatch, 1 RECAP audit-date edge case). Joined into `unified_review.csv` by 17 to override the pipeline's verdict. |
| `recap_diagnosis.csv` | Sub-classification of the 7 `in_recap` rows by why CL didn't create an opinion cluster. Joined into `unified_review.csv` by 17. |

## Intermediate data (the pipeline depends on these)

| File | Phase |
|---|---|
| `citing_opinions/` (78 `.txt` + `_manifest.csv`) | Raw mined opinions from step 10 |
| `real_extractions/` (78 `.json`) | LLM-extracted citations from step 11 |
| `final_pool.csv` | Post-dedup, post-cap pool of all tiers — step 12 |
| `final_200.csv` | Stratified 250-row sample — step 12 (the input to phase 4 onward) |
| `coverage_per_citation.csv` / `coverage_per_tier.csv` | Phase 4 — step 13 |
| `staged_fallback_rigorous_per_row.csv` | Phase 4c — step 15 |
| `audit_per_row.csv` | Phase 5 — step 16 |
| Various `*_run.log` / `*_summary.md` | Run logs and per-phase narrative summaries |

## Reference docs

- [`coverage_memo.docx`](coverage_memo.docx) — primary deliverable,
  the findings memo (Word doc with embedded charts)
- [`HANDOFF.md`](HANDOFF.md) — mid-project handoff note (pre-pivot
  session context); kept for the design-decision provenance
- [`LIMITATIONS.md`](LIMITATIONS.md) — caveats predating the memo;
  most are folded into the memo's Caveats section
- [`REAL_RUN_DESIGN.md`](REAL_RUN_DESIGN.md) — design doc for the
  current (post-pilot) pipeline; useful if you want to understand the
  shape decisions (K=5 cap, stratification, etc.)

## Re-running on a larger corpus

The pipeline is parameterized: the SDNY skip (CL #7316) and the 78-opinion
corpus size are choices, not constraints. To re-run larger:

1. Edit `10_mine_citing_opinions.py` to target more courts or a wider
   date range. Mine into `citing_opinions/`.
2. Run 11 → 12 → 13 → 15 → 16 → 17 → 18 → 17 as above.
3. The Haiku extraction prompt schema (`extract_citations.py`) was
   smoke-tested on 2026-05-16 against the new `month`/`day`/`docket_number`
   fields — production-ready.

The current sample is small enough that per-tier confidence intervals
are wide (~±2 pp on the overall number). A larger corpus would narrow
those and might surface mechanisms we didn't see at n=250.
