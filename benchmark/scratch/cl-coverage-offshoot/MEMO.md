# CourtListener coverage of cited cases: findings from a 250-citation sample

*Rebecca Fordon, with Claude. 2026-05-16. Drafted for sharing with the
Free Law Project / CourtListener team.*

## TL;DR

We sampled 250 cited citations from 78 recent (2023–2026) opinions in
CourtListener and tried to find each cited case in CL. After excluding 29
unmeasurable rows (short-form pin-cites whose fuller siblings already
sit elsewhere in the sample, and one LLM extraction artifact), **204 of
221 measurable citations (92.3%) are findable in CL**.

For 34 of those 204, CL's `/citation-lookup/` API didn't resolve the
cite even though the case does live in CL. Five discoverability patterns
account for those misses, all concentrated in federal district and
state appellate tiers:

| Pattern | Count | Mechanism |
|---|---|---|
| `cl_cluster_citations_empty` | 22 | Opinion cluster in CL, `citations[]` array empty |
| `cl_docket_only_no_cluster` | 7 | Document in CL via RECAP but no opinion cluster created |
| `caption_divergence_rule_25d` | 3 | Caption changed (Rule 25(d) substitution / Doe reveal); name-search broke |
| `ssa_pseudonym` | 2 | Brief used SSA pseudonym (`Michael B.`); CL caption is the real surname |

The bulk of the gap — 24 of 34 misses (71%) — sits on Westlaw cites
filed by federal district courts. A second cluster of 6 misses sits on
recent California state appellate reporters (`Cal.5th` / `Cal.App.5th`,
2022–2026).

## Methodology

- **Corpus**: 78 citing opinions, predominantly 2023–2026, mined from
  CL across a mix of federal (60) and state (18) courts via the
  benchmark's `mine_citing_opinions` step.
- **Extraction**: per-opinion JSON via the Anthropic Haiku model
  (`extract_citations.py`). Hallucinated citations (~13.5% of LLM
  output) excluded by using `citations_valid` only.
- **Pre-filter & dedup**: drop short-form citations (`Id.`, bare pin
  cites) and foreign reporters (`Eng. Rep.`, `Q.B.`, etc.); dedup on
  `(citing_cluster, citation_string, parenthetical)`; K=5 cap per
  `(citing_cluster, cited_tier)` for opinion-level diversity.
- **Stratified sample**: 50 cited citations per tier across SCOTUS,
  Circuit, State_COLR, State_IAC, and Federal_District (250 total).
- **Verification pipeline**:
  - Phase 4 — `/api/rest/v4/citation-lookup/` (strict)
  - Phase 4c — name-based fallback against opinion search and RECAP
    search, with court-id + date filters and multi-factor scoring
    (`15_staged_fallback_rigorous.py`)
  - Phase 5 — per-rescue audit: cite-in-cluster cross-check, party-name
    presence on both sides, court_id match, ±2-year date proximity
    (`16_audit_rescues.py`)
  - Phase 6 — short-form citation dedup
    (`17_build_unified_review.py`): rows whose fuller sibling exists in
    the same citing opinion are dropped from both numerator and
    denominator; unresolvable short-forms (no antecedent) are dropped
    from the denominator only.
- **Manual review**: an eyeball pass over `rescue_was_false_positive`
  audit verdicts identified 7 false negatives, recorded in
  `manual_corrections.csv`. A separate `18_diagnose_recap_cases.py` pass
  fetches the recap_document for each `in_recap` row and sub-classifies
  why no opinion cluster was ingested.

The pipeline is reproducible end-to-end from the scripts in
`benchmark/scratch/cl-coverage-offshoot/` against an unchanged
CourtListener corpus.

## Headline coverage

| Tier | In CL | Denominator | Coverage |
|---|---|---|---|
| SCOTUS | 45 | 45 | 100.0% |
| Circuit | 44 | 46 | 95.7% |
| State_COLR | 40 | 44 | 90.9% |
| State_IAC | 31 | 38 | 81.6% |
| Federal_District | 44 | 48 | 91.7% |
| **OVERALL** | **204** | **221** | **92.3%** |

Coverage bucket distribution (denominator = 221):

| Bucket | Count | % of denom |
|---|---|---|
| `found_via_lookup` (Phase 4 happy path) | 170 | 76.9% |
| `in_opinions` (cluster exists; lookup missed) | 27 | 12.2% |
| `in_recap` (docket only; no cluster) | 7 | 3.2% |
| `not_found_anywhere` | 17 | 7.7% |

The 17 not-found rows split as: 11 `not_in_cl` (no plausible match in
either opinion search or RECAP), 5 `rescue_was_false_positive` (the
verifier's fallback found a wrong cluster; audit correctly rejected it
on cite-in-cluster, court_id, or party mismatch), and 1
`audit_ambiguous` (`In re Loc. TV Advert.` — partial party match plus
date mismatch).

## The 34 lookup misses, in detail

### Issue 1 — `cl_cluster_citations_empty` (22 cases)

Pattern: the opinion cluster exists in CL with the right case name, but
its `citations[]` array is empty. Without a populated cite index, the
citation_lookup API has no way to resolve the cite back to the cluster,
even though everything else about the cluster is correct. A name-based
fallback finds it.

In our sample this pattern is **universal among lookup misses where the
cluster exists** — all 22 in-cluster misses fit it. Zero cases where the
cite IS in the cluster's `citations[]` but lookup nonetheless missed
(would have indicated a lookup-side bug). Zero cases where the cluster
has populated `citations[]` containing different cites (would have
indicated a partial-cite-list bug).

Sub-patterns:

- **Reporter type**: 12 Westlaw, 6 California state (`Cal.5th`,
  `Cal.App.5th`), 2 `F. Supp. 3d`, 1 `F.4th`, 1 `So. 3d`.
- **Year**: 64% of these cases were filed 2022 or later (14/22).
  Consistent with citation-index ingestion lag for recent opinions.
- **Tier**: 14 Federal_District, 5 State_IAC, 2 State_COLR, 1 Circuit,
  0 SCOTUS.

Representative examples:

- *Bay Valley Foods, LLC v. FFI Group*, 2025 WL 3089109 (cluster
  exists; empty `citations[]`)
- *People v. Grajeda*, 111 Cal.App.5th 829 (2025) (cluster exists;
  empty `citations[]`)
- *Democracy Forward Found. v. Office of Personnel Mgmt.*, 780 F. Supp.
  3d 61 (2025) (cluster exists; empty `citations[]`)

### Issue 2 — `cl_docket_only_no_cluster` (7 cases)

Pattern: the cited case appears in CL's RECAP archive as a docket and
typically as a downloadable document, but no opinion cluster has been
created from it. The `/citation-lookup/` API is cluster-scoped, so it
can't reach docket-only cases at all.

`18_diagnose_recap_cases.py` inspects each docket's recap_documents and
sub-classifies these:

| Sub-pattern | Count | What it means |
|---|---|---|
| `recap_doc_opinion_not_ingested` | 3 | PDF on CL, `is_free_on_pacer=true`, OCR'd text, opinion-typed description — but no cluster created |
| `recap_doc_unavailable` | 2 | PACER has it, but no one has RECAP'd it; CL has no PDF to work from |
| `recap_doc_not_opinion_typed` | 2 | PDF on CL with text, but description uses non-canonical opinion language ("ORDER RE:" / "ORDER CERTIFYING") |

The three "opinion not ingested" cases are striking because the docs are
on CL with everything needed to make a cluster:

| Case | Docket | recap_document | Date created on CL | Entry description |
|---|---|---|---|---|
| Mehar Holdings v. Evanston Ins. Co., 2016 WL 5957681 (W.D. Tex.) | [5474769](https://www.courtlistener.com/docket/5474769/mehar-holdings-llc-v-evanston-insurance-company/) | [18720567](https://www.courtlistener.com/recap-documents/18720567/) (12 pp) | 2017-04-23 | "ORDER GRANTING 14 Motion for Reconsideration … GRANTS 4 Motion to Remand. Signed by Judge Ezra" |
| Darensburg v. Metro. Transp. Comm'n, 2009 WL 2392094 (N.D. Cal.) | [4182878](https://www.courtlistener.com/docket/4182878/452/darensburg-v-metropolitan-transportation-commission/) | [13644995](https://www.courtlistener.com/recap-documents/13644995/) (10 pp) | 2017-02-17 | "OPINION ON DEFENDANT'S MOTION FOR ATTORNEYS' FEES. Signed by Mag. J. Laporte on July 7, 2009" |
| Doe v. Lawrence Gen. Hosp., 2025 WL 2808055 (D. Mass.) | [69539673](https://www.courtlistener.com/docket/69539673/doe-v-lawrence-general-hospital/) | [454203499](https://www.courtlistener.com/recap-documents/454203499/) (2 pp) | 2025-10-02 | "Memorandum & Order" |

Two of the three were created on CL in 2017, but their underlying
opinions were filed in 2009 and 2016 — long enough ago that the
contemporaneous `scrape_pacer_free_opinions` run for those date ranges
would have already completed when the doc was eventually uploaded via
RECAP. The third was created on CL 2025-10-02; whether the live scraper
for `mad` has caught up to October 2025 is unknown from our data, but
the `cand` and `nyed` lag documented in
[CL #7316](https://github.com/freelawproject/courtlistener/issues/7316)
(2–3 months) suggests `mad` may show a similar pattern.

The two `recap_doc_not_opinion_typed` cases (*Cabot v. Lewis*: "ORDER
CERTIFYING INTERLOCUTORY APPEAL"; *Hunter v. CCSF*: "ORDER RE:
PLAINTIFFS MOTION FOR REVIEW OF CLERKS TAXATION OF COSTS") are
substantive 4–8 page Magistrate Judge orders that received WL numbers —
arguably opinion-worthy, but the description text uses non-canonical
language that PACER's `WrtOpRpt.pl` may not flag.

### Issue 3 — caption divergence (3 + 2 = 5 cases)

Pattern: the cited case is in CL as a cluster, but the cluster's
caption differs from the cited form in ways that defeat name-based
fallback search. Two distinct sub-patterns surfaced:

**Sub-pattern 3a — Rule 25(d) party substitution / Doe reveal (3
cases)**: an official has been substituted under Rule 25(d), or a Doe
defendant has been replaced with the real name, before CL ingested the
opinion. The brief cites the historical caption; CL stores the current
one.

| Cited | CL caption | URL |
|---|---|---|
| Gilliard v. McWilliams, 2019 WL 3304707 | Gilliard v. Gruenberg | [opinion/4642011](https://www.courtlistener.com/opinion/4642011/) |
| Preston v. Smith, 2023 WL 5337430 | Preston v. Unidentified | [opinion/9729396](https://www.courtlistener.com/opinion/9729396/) |
| Viken Detection Corp. v. Doe, 2019 WL 5268725 | Viken Detection Corp. v. Bradshaw | [opinion/9731515](https://www.courtlistener.com/opinion/9731515/) |

All three clusters also have empty `citations[]` arrays, so this
sub-pattern compounds Issue 1: with the cite not indexed and the name
search broken by the caption change, only manual investigation
recovered these.

**Sub-pattern 3b — SSA pseudonym (2 cases)**: in Social Security
appeals the brief uses an SSA pseudonym (`Michael B.`, `John S.`); CL
indexes the case under the plaintiff's real surname.

| Cited | CL caption | URL |
|---|---|---|
| Michael B. v. Berryhill, 2019 WL 2269962 | Buschman v. Berryhill | [opinion/9674181](https://www.courtlistener.com/opinion/9674181/) |
| John S. v. Bisignano, 2025 WL 1505405 | Sims v. Bisignano | [opinion/10593230](https://www.courtlistener.com/opinion/10593230/) |

A docket-number search would have landed on the right docket
immediately, but CL doesn't currently index `docket_number` as a
searchable field on opinion clusters — so neither `/citation-lookup/`
nor opinion search can bridge the pseudonym → real-name gap.

### Citation-type breakdown

Across all 34 lookup misses:

| Cite type | Federal_District | Circuit | State_COLR | State_IAC | Total |
|---|---|---|---|---|---|
| Westlaw (`YYYY WL N`) | 24 | 0 | 0 | 0 | **24** |
| California reporters | 0 | 0 | 1 | 5 | **6** |
| `F. Supp.` | 2 | 0 | 0 | 0 | 2 |
| `F.[Nd]` | 0 | 1 | 0 | 0 | 1 |
| `So.` reporters | 0 | 0 | 1 | 0 | 1 |

Westlaw cites account for 71% of the misses. All are federal district —
which is consistent with the underlying mechanism: most district court
opinions don't appear in official reporters, so the WL cite is often
the only citable form, and citation_lookup's resolution to a cluster
depends on someone having put the WL cite in `citations[]`.

The California subgroup (6 misses, all 2022–2025) is the second-largest
discoverability gap. All six clusters exist in CL with the right case
name; all six have empty `citations[]`.

## Recommendations

These are unprioritized observations; FLP is in a much better position
to triage them.

1. **Populate `citations[]` for existing opinion clusters more
   aggressively, especially for recent state appellate opinions and
   federal district court WL cites.** This single change would close
   22 of the 34 lookup misses (65%) — the largest mechanism by far.
   The clusters already exist; the cites just aren't indexed.

2. **Back-fill opinion clusters from free RECAP documents that
   bypassed the live `scrape_pacer_free_opinions` window.** A periodic
   sweep of `recap_documents` with `is_free_on_pacer=true`,
   `is_available=true`, opinion-typed entry descriptions, and no
   associated cluster would catch:
   - Old docs (e.g., 2009 *Darensburg*, 2016 *Mehar Holdings*)
     uploaded to CL via RECAP years after the live scrape window for
     their date range had already completed.
   - New docs (e.g., 2025 *Doe v. Lawrence*) where the live scraper
     for the relevant court is lagging (see #7316 for the `nysd`-
     specific stall and the broader 2-3 month `cand` / `nyed` lag).

3. **Add `docket_number` as a searchable field on opinion clusters.**
   This would have caught all 5 caption-divergence cases (Rule 25(d) +
   SSA pseudonym) immediately, since the docket number is invariant
   across caption changes. It would also make verifier-side
   docket-number fallback (which we already implemented in
   `15_staged_fallback_rigorous.py` for RECAP search) work against
   clusters too.

4. **Track caption history on opinion clusters.** When an opinion is
   re-captioned (Rule 25(d) substitution, Doe reveal, pseudonym
   reveal), retaining the prior caption as an alias would let
   name-based search bridge the gap. This is more speculative — the
   prior captions aren't always available — but CL's existing
   prior-caption infrastructure on dockets could likely be extended.

5. **Improve `WrtOpRpt.pl`-side typing for substantive orders.** Two
   of the seven `in_recap` cases (Hunter, Cabot) carry WL numbers
   despite description text PACER may not flag as opinion-worthy
   (`ORDER RE:`, `ORDER CERTIFYING`). Whether to chase these depends
   on whether `WrtOpRpt.pl`'s output is under FLP control or strictly
   inherited from PACER.

## Caveats and limitations

- **Sample size.** 250 rows across five tiers; per-tier sample sizes
  range 38–48 after Phase 6 exclusions. Confidence intervals on the
  individual tier coverage numbers are wide. The overall 92.3% should
  be read as a point estimate with ~±2 pp of slop.

- **NYSD skip.** We initially targeted SDNY (`nysd`) as one of our
  federal district sources for citation mining. We had to drop it
  because the live scraper isn't currently capturing nysd free
  opinions — see
  [CL #7316](https://github.com/freelawproject/courtlistener/issues/7316).
  This biases the sample by under-representing SDNY citations. SDNY
  is a heavy producer of recent unpublished WL opinions, and those
  recent opinions are the exact citation pattern most affected by
  `cl_cluster_citations_empty`. **The 91.7% federal-district coverage
  number is therefore likely an overestimate of what a corpus that
  included SDNY citations would show.**

- **Regional reporter tier ambiguity.** `A.3d`, `P.3d`, `N.E.3d`,
  etc., carry both COLR and IAC opinions. Initial tier classification
  used the LLM-extracted `court_hint` from the Bluebook parenthetical
  when available; otherwise reporter pattern. Mis-classifications
  within state appellate are possible.

- **Phase 6 exclusions.** 29 of 250 rows are excluded from the
  denominator: 13 short-form duplicates of a fuller sibling in the
  same citing opinion (out of both numerator and denominator), 15
  unresolvable short-forms with no antecedent in the same opinion
  (out of denominator only — they're not measurable misses), and 1
  LLM extraction artifact where the case name was dropped. Treating
  these as "unmeasurable" rather than misses is a judgment call;
  reasonable people could include some of them.

- **Audit conservatism.** The audit's `parties_present` test
  (requires both `X` and `Y` from the cited `X v. Y` to appear in the
  matched cluster's case name) is strict. Without the 7 manual
  corrections to false-negative audit verdicts, measured coverage
  would be 197/221 = 89.1%. Whether to include the 7 corrected rows
  in the headline depends on whether you trust hand verification —
  we do, but it's worth flagging.

- **Audit false positives.** A separate pre-existing
  `parties_present`-only verdict rule produced 4 audit false
  positives (Wilson, Wilmington Trust, Rose Way, Thurman) before we
  added the cite-in-cluster cross-check rule
  (`16_audit_rescues.py:395-407`). In each, the verifier picked a
  different cluster whose own `citations[]` array contained
  different cites from the one cited in the brief — definitive
  evidence of a wrong match. These are now correctly marked
  `rescue_was_false_positive` and counted in `not_found_anywhere`.

- **Hallucinated citations excluded upfront.** ~13.5% of the LLM's
  raw extracted citations were hallucinations and are excluded from
  this sample by using `citations_valid` only. Hallucinations are
  themselves an interesting signal for a different question (how
  much do briefs / opinions hallucinate?) but aren't relevant to CL
  coverage.

## Reproducibility

All code and data live under
`benchmark/scratch/cl-coverage-offshoot/`. Key artifacts:

| File | What it is |
|---|---|
| `final_200.csv` | Stratified 250-row sample (post-dedup, post-cap) |
| `coverage_per_citation.csv` | Phase 4 (`/citation-lookup/`) results |
| `staged_fallback_rigorous_per_row.csv` | Phase 4c (name+RECAP fallback) results |
| `audit_per_row.csv` | Phase 5 audit verdicts on each rescue |
| `recap_diagnosis.csv` | Phase 18 sub-classification of the 7 `in_recap` cases |
| `manual_corrections.csv` | 7 user-investigated false negatives + 3 discoverability category labels |
| `unified_review.csv` | Full audit trail (35 columns × 250 rows) |
| `unified_review_concise.csv` | Reviewer-facing view (9 columns × 250 rows) |

To regenerate from the raw extractions:
`12_stratify.py` → `13_lookup_coverage.py` → `15_staged_fallback_rigorous.py`
→ `16_audit_rescues.py` → `18_diagnose_recap_cases.py`
→ `17_build_unified_review.py`.
