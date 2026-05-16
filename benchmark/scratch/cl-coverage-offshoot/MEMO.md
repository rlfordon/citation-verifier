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
cite even though the case does live in CL. Two patterns account for all
of them, both concentrated in federal district and state appellate
tiers:

| Pattern | Count | Mechanism |
|---|---|---|
| `cl_cluster_citations_empty` | 27 | Opinion cluster in CL, `citations[]` array empty |
| `cl_docket_only_no_cluster` | 7 | Document in CL via RECAP but no opinion cluster created |

Five of the 27 `cl_cluster_citations_empty` cases also had captions in
the cited form that diverged from CL's stored caption — Rule 25(d)
substitutions, Doe reveals, and SSA pseudonyms — which is what made
them require manual investigation rather than recover automatically via
name-based fallback in my citation-verifier. They are still fundamentally the same CL-side
issue (empty `citations[]`), but they put extra weight on
recommendations that would let discovery bypass name matching.

The bulk of the gap — 24 of 34 lookup misses are from Westlaw cites
to opinions by federal district courts. A second cluster of 6 lookup misses sits on
recent California state appellate reporters (`Cal.5th` / `Cal.App.5th`,
2022–2026).

## How the 250 citations flowed through the pipeline

The full story is a sequence: first `/citation-lookup/`, then
[citation-verifier](https://github.com/rfordon/citation-verifier)'s
name+date+court fallback against opinion search and RECAP search, then
audit, then manual review of borderline cases. The diagram below shows
where each of the 250 citations landed and which signal got it there.

```mermaid
flowchart LR
    A[250 cited citations] --> B[221 measurable<br/>after Phase 6 dedup]
    A -.->|excluded| X[29 unmeasurable<br/>13 short-form dups<br/>15 unresolvable short-forms<br/>1 no case name]

    B --> C[170 resolved by<br/>/citation-lookup/]
    B --> D[51 not resolved by<br/>/citation-lookup/]

    D --> E[34 found by citation-verifier]
    D --> F[17 not found anywhere]

    E --> G[27 found via opinion search<br/>cluster exists in CL, but citations[] empty]
    E --> H[7 found via RECAP search<br/>docket only, no opinion cluster]

    G --> G1[22 name search bridges<br/>automatically]
    G --> G2[5 required manual review<br/>3 Rule 25(d) / Doe reveal<br/>2 SSA pseudonym]

    H --> H1[5 RECAP search lands<br/>on correct docket]
    H --> H2[2 required manual review<br/>caption variations]

    F --> F1[11 not_in_cl<br/>no plausible match]
    F --> F2[5 wrong-cluster rescue<br/>audit caught it]
    F --> F3[1 audit_ambiguous<br/>partial party + date mismatch]

    style C fill:#c8e6c9
    style G fill:#fff9c4
    style H fill:#fff9c4
    style F fill:#ffcdd2
```

Reading the diagram: the 170 green box are the happy path — direct
citation-lookup hits. The 34 yellow boxes are the lookup misses where
the case nonetheless lives in CL. The 17 red box is what couldn't be
found by any method.

## Methodology

- **Corpus**: 78 citing opinions, predominantly 2023–2026, mined from
  CL across a mix of federal (60) and state (18) courts via the
  benchmark's `mine_citing_opinions` step.
- **Extraction**: per-opinion JSON via the Anthropic Haiku model
  (`extract_citations.py`). LLM extraction rather than eyecite for
  three reasons that surfaced in an earlier v1 pass: (a) eyecite's
  paren-attribution bug propagates wrong years when a citation has
  multiple parallel reporters; (b) smart quotes and apostrophes in
  case names sometimes truncate the extracted name; and (c) slip-
  opinion placeholders like `-- F. Supp. 3d ----` get absorbed into
  the defendant field, poisoning case_name for downstream search.
  The LLM extraction also captures fields eyecite doesn't surface
  directly — `court_hint` (separated cleanly from editorial
  parentheticals), `month`/`day`, and `docket_number` — which are
  needed for the WL/LEXIS docket-search fallback that recovers
  caption-divergent cases (Rule 25(d) substitutions, SSA pseudonyms).
  Tradeoffs: temp=1.0 (the `claude -p` default; no flag exposed),
  higher cost per opinion than eyecite, and LLM hallucination risk —
  mitigated by validating each `citation_string` as a verbatim
  substring of the source opinion before downstream use. Incomplete
  or extraction-artifact citations (~13.5% of raw LLM output) are
  excluded by using `citations_valid` only.
- **Pre-filter & dedup**: drop short-form citations (`Id.`, bare pin
  cites) and foreign reporters (`Eng. Rep.`, `Q.B.`, etc.); dedup on
  `(citing_cluster, citation_string, parenthetical)`; K=5 cap per
  `(citing_cluster, cited_tier)` for opinion-level diversity.
- **Stratified sample**: 50 cited citations per tier across SCOTUS,
  Circuit, State_COLR, State_IAC, and Federal_District (250 total).
- **Verification pipeline**:
  - Phase 4 — `/api/rest/v4/citation-lookup/` (strict)
  - Phase 4c — [citation-verifier](https://github.com/rfordon/citation-verifier)
    fallback: name-based search against the opinion-search and
    RECAP-search APIs, with court-id + date filters and multi-factor
    scoring on name, court, date, and docket number
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
either opinion search or RECAP), 5 `rescue_was_false_positive` (citation-verifier's 
fallback found a wrong cluster; audit correctly rejected it
on cite-in-cluster, court_id, or party mismatch), and 1
`audit_ambiguous` (`In re Loc. TV Advert.` — partial party match plus
date mismatch).

## The 34 lookup misses that were in CL or RECAP, in detail

### Issue 1 — `cl_cluster_citations_empty` (27 cases)

Pattern: the opinion cluster exists in CL with the case the brief
cites, but its `citations[]` array is empty. Without a populated cite
index, the citation_lookup API has no way to resolve the cite back to
the cluster, even though everything else about the cluster is correct.
citation-verifier's name+court+date fallback against opinion search
recovers it — *most of the time*.

In our sample this pattern is **universal among lookup misses where the
cluster exists** — all 27 in-cluster misses fit it. Zero cases where
the cite IS in the cluster's `citations[]` but lookup nonetheless
missed (would have indicated a lookup-side bug). Zero cases where the
cluster has populated `citations[]` containing different cites (would
have indicated a partial-cite-list bug).

Sub-patterns within the 27:

- **Reporter type**: 17 Westlaw, 6 California state (`Cal.5th`,
  `Cal.App.5th`), 2 `F. Supp. 3d`, 1 `F.4th`, 1 `So. 3d`.
- **Year**: 63% of these cases were filed 2022 or later (17/27).
  Consistent with citation-index ingestion lag for recent opinions.
- **Tier**: 19 Federal_District, 5 State_IAC, 2 State_COLR, 1 Circuit,
  0 SCOTUS.

Representative "easy recovery" examples (name search bridges
automatically):

- *Bay Valley Foods, LLC v. FFI Group*, 2025 WL 3089109
- *People v. Grajeda*, 111 Cal.App.5th 829 (2025)
- *Democracy Forward Found. v. Office of Personnel Mgmt.*, 780 F. Supp.
  3d 61 (2025)

#### Sub-case 1a: name search bridges the gap (22 cases)

For these 22, the cluster's case name matches the cited form closely
enough that citation-verifier's name-based fallback (opinion search by
case name + court + date) lands on the right cluster on the first try.
The audit step then confirms via party-name match and date proximity.
These represent the largest single discoverability gap fixable by a
single change: populating `citations[]`.

#### Sub-case 1b: name search blocked by caption change (5 cases)

For these 5, the cluster's case name has diverged from the cited form
in ways that defeat a name-based search. Two distinct sub-patterns:

**Rule 25(d) substitution / Doe reveal (3 cases)** — an official has
been substituted under Federal Rule 25(d), or a Doe defendant has been
replaced with the real name, *after* CL ingested the opinion. The
brief cites the historical caption; CL stores the current one.

| Cited | CL caption | URL |
|---|---|---|
| Gilliard v. McWilliams, 2019 WL 3304707 | Gilliard v. Gruenberg | [opinion/4642011](https://www.courtlistener.com/opinion/4642011/) |
| Preston v. Smith, 2023 WL 5337430 | Preston v. Unidentified | [opinion/9729396](https://www.courtlistener.com/opinion/9729396/) |
| Viken Detection Corp. v. Doe, 2019 WL 5268725 | Viken Detection Corp. v. Bradshaw | [opinion/9731515](https://www.courtlistener.com/opinion/9731515/) |

**SSA pseudonym (2 cases)** — in Social Security appeals, the brief
uses an SSA pseudonym (`Michael B.`, `John S.`); CL indexes the case
under the plaintiff's real surname.

| Cited | CL caption | URL |
|---|---|---|
| Michael B. v. Berryhill, 2019 WL 2269962 | Buschman v. Berryhill | [opinion/9674181](https://www.courtlistener.com/opinion/9674181/) |
| John S. v. Bisignano, 2025 WL 1505405 | Sims v. Bisignano | [opinion/10593230](https://www.courtlistener.com/opinion/10593230/) |

All five clusters also have empty `citations[]`. Once `citations[]` is
populated, the underlying issue evaporates — `/citation-lookup/` would
resolve them without needing to reason about the caption at all. Until
then, anyone trying to discover these cases by name (in either an
automated verifier or a manual CL search) is stuck.

> *Aside: this sample also surfaced a parallel set of issues on the
> search side — cases where citation-verifier itself initially picked
> the wrong cluster and only the audit step caught it, or where it
> picked the right cluster but the audit overrode on weak signals.
> Those are citation-verifier–side concerns more than CL-side ones,
> and we're saving the detailed discussion for a separate write-up.*

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
may have already completed when the doc was eventually uploaded via
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

These are unprioritized observations:

1. **Populate `citations[]` for existing opinion clusters more
   aggressively, especially for recent state appellate opinions and
   federal district court WL cites.** This single change would close
   27 of the 34 lookup misses (79%) — by far the largest mechanism.
   The clusters already exist; the cites just aren't indexed. This
   would also resolve the 5 caption-divergent sub-cases automatically,
   since `/citation-lookup/` would hit the cluster directly without
   needing to reason about the caption. This will likely be addressed
   by the scanning project.

2. **Back-fill opinion clusters from free RECAP documents that
   bypassed the live `scrape_pacer_free_opinions` window.** A periodic
   sweep of `recap_documents` with `is_free_on_pacer=true`,
   `is_available=true`, opinion-typed entry descriptions, and no
   associated cluster would catch:
   - Old docs (e.g., 2009 *Darensburg*, 2016 *Mehar Holdings*)
     uploaded to CL via RECAP after the live scrape window for
     their date range had already completed.
   - New docs (e.g., 2025 *Doe v. Lawrence*) where the live scraper
     for the relevant court is lagging (see #7316 for the `nysd`-
     specific stall and the broader 2-3 month `cand` / `nyed` lag).

3. **Fix the per-court scraper stall/lag pattern surfaced in #7316.**
   Three of the seven `in_recap` cases were docs created on CL in
   2017 but underlying opinions filed years earlier — these wouldn't
   be caught by a live-scraper fix alone. But Rec 2 above and the
   #7316 fix together would catch both the historical and the
   ongoing gap.


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

- **Audit conservatism.** Our audit's `parties_present` test
  (requires both `X` and `Y` from the cited `X v. Y` to appear in the
  citation-verifier-matched cluster's case name) is strict. Without
  the 7 manual corrections to false-negative audit verdicts, measured
  coverage would be 197/221 = 89.1%. Whether to include the 7
  corrected rows in the headline depends on whether you trust hand
  verification — we do, but it's worth flagging.

- **Audit false positives.** A separate pre-existing
  `parties_present`-only verdict rule produced 4 audit false
  positives (Wilson, Wilmington Trust, Rose Way, Thurman) before we
  added the cite-in-cluster cross-check rule
  (`16_audit_rescues.py:395-407`). In each, citation-verifier picked
  a different cluster whose own `citations[]` array contained cites
  different from the one cited in the brief — definitive evidence of
  a wrong match. These are now correctly marked
  `rescue_was_false_positive` and counted in `not_found_anywhere`.

- **Invalid citations excluded upfront.** ~13.5% of the LLM's
  raw extracted citations were invalid (short cites or extraction artifacts) and are excluded from
  this sample by using `citations_valid` only. 

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
