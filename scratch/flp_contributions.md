# Potential Contributions to Free Law Project

This document tracks potential contributions to FLP's projects (CourtListener, eyecite, courts-db, etc.) based on our findings.

## Table of Contents

| # | Title | Status | Target |
|---|-------|--------|--------|
| [1](#1-abbreviation-synonym-coverage-for-search) | Abbreviation Synonym Coverage for Search | DRAFT | CL [#3367](https://github.com/freelawproject/courtlistener/issues/3367) |
| [2](#2-docket-parameter-unreliability) | Docket Parameter Unreliability | DECLINED | CL |
| [3](#3-eyecite-newline-breaks-metadata-parsing-paragraphtoken-boundary) | eyecite: Newline Breaks Metadata Parsing | SUBMITTED | eyecite [#297](https://github.com/freelawproject/eyecite/issues/297) |
| [4](#4-eyecite-apostrophe-truncates-case-name-words) | eyecite: Apostrophe Truncates Case Names | SUBMITTED | eyecite [#296](https://github.com/freelawproject/eyecite/issues/296) |
| [5](#5-federal-district-court-opinions-only-in-recap-not-in-opinions-db) | Federal Court Opinions Only in RECAP | UPDATED | CL [#6963](https://github.com/freelawproject/courtlistener/issues/6963) |
| [6](#6-data-quality-issues) | Data Quality Issues | DRAFT | CL |
| [7](#7-simpler-case-law-apis--feedback-from-citation-verification-consumer) | Simpler Case Law APIs — Consumer Feedback | DRAFT | CL [#6946](https://github.com/freelawproject/courtlistener/issues/6946) |
| [8](#8-api-docs-blocked-by-bot-protection--llmstxt) | API Docs Blocked by Bot Protection / llms.txt | SUBMITTED | CL [#6040](https://github.com/freelawproject/courtlistener/issues/6040) |
| [9](#9-eyecite-slip-opinion-placeholder-absorbed-into-case-name) | eyecite: Slip Opinion Placeholder Absorbed into Case Name | DRAFT | eyecite |
| [10](#10-search-api-defaults-to-published-only-stat_-filters-undocumented) | Search API Defaults to Published Only; stat_ Filters Undocumented | SUBMITTED | CL [#7049](https://github.com/freelawproject/courtlistener/issues/7049) |
| [11](#11-batch-document-text-retrieval) | Batch Document Text Retrieval | DRAFT | CL |
| [12](#12-criminal-district-court-opinions-are-recap-only) | Criminal District Court Opinions Are RECAP-Only | SUBMITTED | CL [#7596](https://github.com/freelawproject/courtlistener/issues/7596) (comment) |

## Status Legend

- **DRAFT** - Needs more evidence/testing before submitting
- **READY** - Ready to submit, awaiting decision
- **SUBMITTED** - Already submitted to FLP
- **DECLINED** - Decided not to submit (explain why)

---

## 1. Abbreviation Synonym Coverage for Search

**Status:** DRAFT
**Target:** CourtListener issue [#3367](https://github.com/freelawproject/courtlistener/issues/3367)
**Type:** Data contribution / Comment on existing issue

### Summary

Provide prioritized subset of Indigo Book abbreviations based on real-world false negative analysis. Issue #3367 requests "full Indigo Book table" but that's 100+ terms - we can help prioritize which ones matter most. [Note: we decided not to include some of the below abbreviations, so need to review code and update this before submitting]

### Our Findings

**Coverage analysis:**
- Analyzed 53 common Indigo Book abbreviations
- Identified 46 high-priority terms (87% coverage of real cases)
- Intentionally skipped 7 ambiguous terms (N., S., E., W., St., Ave., Blvd.)
- Categorized by type for easier implementation

**False negatives encountered:**
- "Bossart v. King Cnty." → failed to match cluster #69346061 ("Bossart v. King County")
- "Busha v. SC Dep't of Mental Health" → failed to match cluster #14553775 ("SC Department of Mental Health")
- "Townsley v. Lifewise Assurance Co." (with other issues) → benefited from normalization

**Our workaround:**
- Implemented client-side normalization in `parser.py`
- See: `_normalize_case_name()` function
- Test coverage: `tests/check_indigo_book_coverage.py`

### Proposed Comment for Issue #3367

```markdown
### Data: Real-world abbreviation priority analysis

We built a citation verification tool that uncovered false negatives due to abbreviation mismatches in CL search. We analyzed Indigo Book abbreviations to identify which ones matter most in practice.

**High-priority abbreviations (46 terms, ~87% real-world coverage):**

*Government entities (11):*
- Cnty., Cty. → County
- Dept., Dep't → Department
- Comm., Comm'n → Commission
- Bd. → Board
- Div. → Division
- Dist. → District
- Off., Ofc. → Office

*Organizations (8):*
- Corp. → Corporation
- Co. → Company
- Inc. → Incorporated
- Ltd. → Limited
- LLC, L.L.C. → Limited Liability Company
- Assn., Ass'n → Association

*Positions (8):*
- Admin., Adm'r → Administrator
- Exec. → Executive
- Dir. → Director
- Sec'y → Secretary
- Treas. → Treasurer
- Atty. → Attorney
- Gen. → General

*Education (3):*
- Univ. → University
- Coll. → College
- Sch. → School

*Medical/Health (3):*
- Hosp. → Hospital
- Med. → Medical
- Ctr. → Center

*Business/Services (7):*
- Ins. → Insurance
- Mfg. → Manufacturing
- Serv., Servs. → Service/Services
- Transp. → Transportation
- Util. → Utility
- Pub. → Public

*Scope (4):*
- Nat'l, Natl. → National
- Int'l, Intl. → International

*Religious (2):*
- Cath. → Catholic
- Ch. → Church

**Terms we intentionally skipped (ambiguous/risky):**
- N., S., E., W. → could be initials (e.g., "John E. Smith")
- St. → context-dependent (Street vs Saint)
- Ave., Blvd. → addresses, rarely in case names

**False negatives we encountered:**
- Searched: "Bossart v. King Cnty." → No match
- CL has: cluster #69346061 "Bossart v. King County"
- Same issue with "Dep't" → "Department"

**Our workaround:**
We normalize abbreviations client-side before searching, but server-side Elasticsearch synonyms would benefit all CL users.

**Implementation priority:**
Suggest implementing these 46 first (covers 87% of cases) rather than the full 100+ term Indigo Book table. Can add the remaining 13% later based on actual usage data.

Full code/tests available at: [link to repo if/when public]

Happy to provide more data if helpful for prioritization.
```

### Decision Factors

**Pros:**
- Actionable data FLP doesn't have
- Helps prioritize their work
- Shows real false negatives
- Non-demanding tone ("here's data" not "fix this")

**Cons:**
- Issue already exists since 2023 (they know)
- Our workaround works fine
- May not be their priority
- Could be noise if they're already planning implementation

**Recommendation:** Submit as helpful data contribution. It's low-effort for us and might help FLP prioritize. Worst case: they ignore it. Best case: they use our prioritization.

### Submission Checklist

Before submitting:
- [ ] Verify issue #3367 is still open
- [ ] Check recent comments (has someone else provided similar data?)
- [ ] Ensure our repo is public OR remove "link to repo" line
- [ ] Review tone (helpful, not demanding)
- [ ] Submit comment
- [ ] Update this doc with link to our comment

---

## 2. Docket Parameter Unreliability

**Status:** DECLINED (won't report - related issues already exist)
**Target:** CourtListener
**Type:** Bug report / Limitation

### Summary

The RECAP search API `docket` parameter is completely ignored by the API, returning unfiltered recent results instead of filtering by docket number. This appears to be a known limitation tracked in related FLP issues about docket number normalization and search.

### Evidence

**Testing conducted 2025-02-09 using API v4.3:**

Test 1 - Bossart case docket:
```bash
# Using docket parameter - BROKEN
curl "https://www.courtlistener.com/api/rest/v4/search/?type=r&docket=2:24-cv-01776"
# Returns: 60,994,914 results (all recent RECAP docs, unrelated)

# Using q parameter - WORKS
curl "https://www.courtlistener.com/api/rest/v4/search/?type=r&q=\"2:24-cv-01776\""
# Returns: 16 results including correct case (docket ID 69346061)
```

Test 2 - Anderson case docket:
```bash
# Using docket parameter - BROKEN
curl "https://www.courtlistener.com/api/rest/v4/search/?type=r&docket=17-cv-12676"
# Returns: 60,994,914 results (all recent RECAP docs, unrelated)

# Using q parameter - WORKS
curl "https://www.courtlistener.com/api/rest/v4/search/?type=r&q=\"17-cv-12676\""
# Returns: 21 results including correct case (docket ID 6264209)
```

**Conclusion:** The `docket` parameter has no effect on filtering. It's present in the URL but completely ignored.

### Related FLP Issues

Search found several related issues about docket number handling:

1. **[Issue #764](https://github.com/freelawproject/courtlistener/issues/764)** - RECAP docket number search doesn't allow suffixes (judge initials)
2. **[Issue #635](https://github.com/freelawproject/courtlistener/issues/635)** - Normalize PACER docket numbers for search (leading zeroes)
3. **[Issue #6296](https://github.com/freelawproject/courtlistener/issues/6296)** - Docket number clean-up to prep for appellate chain linking
4. **[Issue #6313](https://github.com/freelawproject/courtlistener/issues/6313)** - Implement docket number cleaning to CL workflow

These issues focus on docket number normalization and search via the main search box, but they indicate FLP is aware that docket number search has challenges. The `docket` parameter specifically may never have been fully implemented for the API.

### Our Workaround

From `src/citation_verifier/client.py`:
```python
if docket_number:
    # Use q with quoted string -- the docket param is unreliable
    q_parts = [params.get("q", ""), f'"{docket_number}"']
    params["q"] = " ".join(p for p in q_parts if p)
```

Then in `verifier.py`, we filter results client-side:
```python
# API does fuzzy matching, so filter to actual docket matches
cited_dn = self._normalize_docket_number(parsed.docket_number)
results = [
    r for r in results
    if self._normalize_docket_number(
        r.get("docketNumber") or r.get("docket_number") or ""
    ) == cited_dn
]
```

This two-step approach (search with `q`, then client-side filter) works reliably.

### Decision Factors

**Why not report:**
- Related issues already exist (#764, #635, #6296, #6313)
- FLP is aware docket number search is challenging
- The `docket` parameter may never have been fully implemented (not documented)
- Our workaround is straightforward and works well
- Low priority - affects API users but not web interface users
- Reporting would likely duplicate or overlap with existing work

**Alternative action:**
- Could comment on existing issues with our API testing data
- But seems more useful to wait until FLP addresses the broader docket normalization work

**Recommendation:** Don't create a new issue. If FLP solicits feedback on docket search improvements (e.g., in #635 or #6296), we could contribute our API testing data showing the `docket` parameter doesn't work.

---

## 3. eyecite: Newline Breaks Metadata Parsing (ParagraphToken boundary)

**Status:** SUBMITTED
**Target:** eyecite [#297](https://github.com/freelawproject/eyecite/issues/297)
**Type:** Bug report
**Filed:** 2026-02-16

### Summary

eyecite's `match_on_tokens()` stops scanning at `ParagraphToken` boundaries. A single `\n` becomes a `ParagraphToken`, so court/year parentheticals on the next line are never reached. 101 citations (19%) affected across our 19-PDF corpus. Proposed three approaches: option flag, peek-past-boundary, or single-vs-double newline distinction.

Offered to submit a PR once maintainers indicate preferred approach.

### Draft

See `scratch/drafts/eyecite-newline-metadata.md` for the full issue text as filed.

---

## 4. eyecite: Apostrophe Truncates Case Name Words

**Status:** SUBMITTED
**Target:** eyecite [#296](https://github.com/freelawproject/eyecite/issues/296)
**Type:** Bug report
**Filed:** 2026-02-16

### Summary

eyecite's case name extraction regexes use `[\w\-.]+` to match words, but `\w` excludes apostrophes. Legal abbreviations with internal apostrophes (`Att'y`, `Dep't`, `Gov't`, `Comm'n`, `P'ship`, `Nat'l`, `Sec'y`) are truncated at the apostrophe, losing the suffix. 8 case names affected in our 19-PDF corpus. 11 standard Indigo Book abbreviations identified.

### Suggested fix

Add straight and curly apostrophes to three character classes in `eyecite/regexes.py`:
- `[\w\-.]+` → `[\w\-.'\u2019]+` (SHORT_CITE_ANTECEDENT_REGEX, SUPRA_ANTECEDENT_REGEX)
- `[a-z\-.]+` → `[a-z\-.'\u2019]+` (PRE_FULL_CITATION_REGEX)

Offered to submit a PR if maintainers want one.

### Draft

See `scratch/drafts/eyecite-apostrophe-truncation.md` for the full issue text as filed.

---

## 5. Federal District Court Opinions Only in RECAP (Not in Opinions DB)

**Status:** UPDATED
**Target:** CourtListener [#6963](https://github.com/freelawproject/courtlistener/issues/6963) (related to closed #3790)
**Type:** Bug report — originally 16 cases, corrected to 7
**Filed:** 2026-02-16
**Updated:** 2026-03-02 — 9 of 16 were actually in the opinions DB with `precedential_status: Unknown`, invisible to our searches because the search API defaults to Published-only. See [#7049](https://github.com/freelawproject/courtlistener/issues/7049) and [correction comment](https://github.com/freelawproject/courtlistener/issues/6963#issuecomment-3988354356).

### Summary

Originally reported 16 federal civil cases in RECAP but not in the opinions database. After discovering the `stat_Unknown` filter (see #10 below), 9 were found to be present with Unknown status. **7 cases remain genuinely missing.** 4 pre-sweep (2008–2023), 3 post-sweep (2024–2025).

### Background

CL's RECAP-to-opinions pipeline requires: federal district/bankruptcy, civil case, and case law citations in the document text. Sweep coverage: 1950-05-12 to 2024-08-05, plus daily ingestion. Criminal cases excluded by design (#4642).

### Cases reported in #6963

| Category | Count | Key examples |
|----------|-------|-------------|
| Pre-sweep (2008–2023) | 4 | Fagundes (2008), Mali (2018), King (2019), Ruggierlo (2023) |
| Sweep-edge (Aug 2024) | 1 | Dukuray |
| Post-sweep (2025) | 11 | Button, Tercero, Oneto, Russomanno, Glass, HoosierVac, Thomas, Lahti, Coronavirus Reporter, Davis, O'Brien |

### Excluded from report

- **United States v. Hayes** — criminal case (excluded by design)
- **Lacey v. State Farm** — our tool bug (picked wrong document)
- **O'Brien v. Flick (S.D. Fla.)** — order behind paywall

### `is_free_on_pacer` check (2026-02-21)

Checked all 16 reported documents via the recap-documents API. **13 of 16 have `is_free_on_pacer=True`** — PACER's Written Opinion Report flagged them as written opinions, so the `recap_into_opinions` pipeline should have seen them. **3 have `is_free_on_pacer=None`** (Button, HoosierVac, O'Brien) — these were never flagged by PACER as free written opinions.

| Case | Docket | Doc # | `is_free_on_pacer` | `is_available` | `page_count` |
|------|--------|-------|--------------------|----------------|--------------|
| **Pre-sweep** | | | | | |
| Fagundes v. Charter Builders (2008) | 5793562 | 104 | **True** | True | 12 |
| Mali v. British Airways (2018) | 7378483 | 44 | **True** | True | 28 |
| King v. Police & Fire FCU (2019) | 7632576 | 31 | **True** | True | 1 |
| Ruggierlo v. Lancaster (2023) | 64925451 | 25 | **True** | True | 9 |
| **Sweep-edge** | | | | | |
| Dukuray v. Experian (2024) | 67881565 | 43 | **True** | True | 9 |
| **Post-sweep** | | | | | |
| Button v. Humphries (2025) | 69037800 | 148 | None | False | None |
| Tercero v. Sacramento Logistics (2025) | 68387287 | 50 | **True** | True | 24 |
| Oneto v. Watson (2025) | 65342301 | 93 | **True** | True | 7 |
| Russomanno v. Comm'r of Soc. Sec. (2025) | 69146971 | 22 | **True** | True | 5 |
| Glass v. Foley & Lardner (2025) | 69584955 | 32 | **True** | True | 7 |
| Welfare Fund v. HoosierVac (2025) | 68879596 | 122 | None | True | 2 |
| Thomas v. Pangburn (2024) | 67565382 | 64 | **True** | True | 1 |
| Lahti v. Consensys (2025) | 68403961 | 44 | **True** | True | 17 |
| Coronavirus Reporter v. Apple (2025) | 69434738 | 102 | **True** | True | 14 |
| Davis v. Marion County (2025) | 69325037 | 71 | **True** | True | 10 |
| O'Brien v. Flick (2025, 11th Cir.) | 69638127 | 30 | None | False | 2 |

**Analysis:** The 3 `None` cases (Button, HoosierVac, O'Brien) may have a simpler explanation — PACER never flagged them, so the pipeline never saw them. But the 13 `True` cases (including all 4 pre-sweep cases) are the real mystery: PACER flagged them, the documents are available, yet they still didn't make it into the opinions DB. Possible explanations: (1) the pipeline's text-extraction step failed to find case law citations in the document text (a requirement), or (2) a processing error during the sweep.

For comparison, the Section 6 "opinions not marked as free" cases (Himes, Neravetla) also have `is_free_on_pacer=None` — same pattern as Button/HoosierVac/O'Brien.

### Additional examples to add later

As new RECAP-only cases are found in verification runs, add them here and consider commenting on #6963 with a batch update.

| Citation | Year | Court | RECAP Document | Found in run |
|----------|------|-------|---------------|-------------|
| *(none yet)* | | | | |

---

## 6. Data Quality Issues

**Status:** DRAFT
**Target:** CourtListener (TBD - may be multiple issues)
**Type:** Data quality reports

### Issues to Track

As we encounter data quality problems, document them here:

**State court coverage gaps:**
- Status: 7 confirmed cases missing from CL
- Examples:
  1. **Rupnow v. Mont. State Auditor & Comm'r of Ins., 542 P.3d 384 (Mont. 2024)** — Montana Supreme Court opinion not in CL at all. Verified real citation (likely_real classification). Source: Thornton v. Flathead County PDF.
  2. **Jindrich v. Weihele, 656 S.W.3d 519 (Tex. App. 2022)** — Texas Court of Appeals. CL has a different opinion for this case (cluster 5174585, "Edward S. Jindrich, Jr. v. Michaela Weihele") but it appears to be the wrong document — the opinion at 656 S.W.3d 519 is not available. Source: Suday v. Suday PDF.
  3. **Jha v. Khan, 520 P.3d 470, 477 (Wash. Ct. App. 2022)** — Washington Court of Appeals. Not in CL. Available at https://www.courts.wa.gov/opinions/pdf/837681.pdf
  4. **Fowler v. Guerin, 515 P.3d 502, 506 (Wash. 2022)** — Washington Supreme Court. Not in CL. Available at https://www.courts.wa.gov/opinions/index.cfm?fa=opinions.showOpinion&filename=1000693MAJ
  5. **M.G. v. Bainbridge Island School District #303, 566 P.3d 132, 147 (Wash. Ct. App. 2025)** — Washington Court of Appeals. Not in CL.
  6. **Reinlasoder v. City of Billings, 455 P.3d 477 (Mont. 2020)** — Montana Supreme Court. Not in CL. Source: Thornton v. Flathead County PDF.
  7. **Mungo v. State, 486 Md. 158 (2023)** — Maryland Court of Appeals. Not in CL. Source: mezu_v_mezu PDF.
  - **Arrowhead Capital Finance, Ltd. v. Picturepro, LLC, 2023 WL 109722 (9th Cir. Jan. 5, 2023)** — Available on 9th Circuit website (unpublished) 21-56063. Why not in opinions? (or RECAP?). Source: lacey_v_state_farm_general_insurance_co_5-6-2025_order.pdf. Verification: NOT_FOUND. Added automatically from QC review.
  - **Grimshaw v. Metro. Life Ins. Co., No. 11-14165-CIV, 2011 WL 13319575 (S.D. Fla. Aug. 2, 2011)** — opinion not part of RECAP, docket here: https://www.courtlistener.com/docket/12835633/font-colorred-restricted-filer-font-grimshaw-v-metropolitan-life/. Source: Button_v._Mccawley_USA_4_February_2026.pdf. Verification: NOT_FOUND. Added automatically from QC review.
  - **Kendrick v. Sec'y, Florida Dep't of Corr., 21-12686, 2022 WL 2388425 (11th Cir. July 1, 2022)** — 11th circuit nonpublished, should be in our data.. Source: caseDecisions_8629a673-c319-4961-8664-9671575f33c4_128338.pdf. Verification: NOT_FOUND. Added automatically from QC review.
  - **Sabir v. Daud, No. 01-22-00956- CV, 2024 WL 3478110 (Tex. App. 2024)** — not great coverage for this court .. Source: maryvel_suday_and_the_estate_of_olga_tamez_de_suday_v._jesus_lozano_suday.pdf. Verification: NOT_FOUND. Added automatically from QC review.
  - **Rogers v. City of Hobart, 996 F.3d 812 (7th Cir. 2021)** — we should have this!. Source: NAOC_v._Indiana_Imports_USA_15_January_2026.pdf. Verification: NOT_FOUND. Added automatically from QC review.
  - **O'Brien v. Flick, No. 24-61529-CIV, 2025 WL 242924 (S.D. Fla. Jan. 10, 2025)** — write document, but not in RECAP. Source: Button_v._Mccawley_USA_4_February_2026.pdf. Verification: LIKELY_REAL. Added automatically from QC review.
- Action: Collect more examples before reporting. Known CL limitation (#5 in Known CL API Limitations). Washington state courts appear to be a systemic gap (3 of 7 examples). Montana also showing pattern (2 of 7).

**Opinions not marked as free:**
- Status: 2 cases exist in CL but opinions not accessible — **confirmed `is_free_on_pacer=None`**
- Examples:
  1. **Himes v. Provident Life & Accident Insurance Co., No. 3:19-CV-00215, 2020 WL 9935829 (M.D. Tenn. Mar. 3, 2020)** — federal opinion not marked as free. Docket 14749274, doc #20 (2020-03-03). `is_free_on_pacer=None`, `is_available=False`. Description: "Order on Motion to Dismiss for Failure to State a Claim".
  2. **Neravetla v. Virginia Mason Med. Ctr., No. C13-1501-JCC, 2014 WL 12787876, *3-*4 (W.D. Wash. May 23, 2014)** — federal opinion not marked as free. Docket 5262939, entry #35 (2014-05-23). `is_free_on_pacer=None`, `is_available=False`. Description: "ORDER by Judge John C Coughenour... GRANTS Defendants' motion to dismiss".
- **Analysis (2026-02-21):** These documents were never flagged by PACER's Written Opinion Report, so CL's `recap_into_opinions` pipeline never saw them. This is a PACER-side gap (courts didn't report them as written opinions), not a CL pipeline bug. Distinct from the #6963 cases where `is_free_on_pacer=True` but opinions still weren't ingested.
- Action: Collect more examples. These are federal cases that should be available but aren't searchable via the opinions API.

**Case name format variations:**
- Status: May be expected behavior for multi-party cases
- Need: More examples to determine if systemic
- Examples so far: 2 (Estate of Elkins, Townsley)

**Missing citations field:**
- Status: Need to collect specific cluster IDs
- Impact: Citation lookup fails even when case exists
- Examples: None documented yet

**Cases exist but not searchable:**
- Status: Need to identify specific patterns
- Examples: None documented yet

### Decision Criteria

Before reporting data quality issues:
- Collect 10+ examples showing a pattern
- Verify it's not expected behavior
- Check if already reported
- Assess impact (widespread vs edge case)

---

## 7. Simpler Case Law APIs — Feedback from Citation Verification Consumer

**Status:** DRAFT
**Target:** CourtListener issue [#6946](https://github.com/freelawproject/courtlistener/issues/6946)
**Type:** Comment on existing issue

### Summary

Issue #6946 proposes simpler case law APIs — flatter responses, top-level court filtering, state-level parameters. We've been building a citation verification tool on top of the v4 API and have concrete feedback about what structural changes would help most.

### Proposed Comment for Issue #6946

```markdown
Hi -- great to see this issue opened. I've been building a citation verification tool ([rlfordon/citation-verifier](https://github.com/rlfordon/citation-verifier)) that checks legal citations against the CourtListener API to catch AI-hallucinated case references. I've been living inside the v4 API for a while now and wanted to share some friction points from a real consumer's perspective that might be useful as you think about what a simpler API looks like.

### The verification pipeline today

To verify a single citation like `Obergefell v. Hodges, 576 U.S. 644 (2015)`, my tool makes up to **4-7 API calls**:

1. `POST /citation-lookup/` — exact reporter match
2. `GET /search/?type=o` — fuzzy opinion search as fallback
3. `GET /search/?type=r` — RECAP search as second fallback
4. `GET /docket-entries/` — paginated follow-up to find the actual opinion document within a docket

Each step exists because the previous one lacks something. A lot of this could collapse into fewer round trips with some structural changes.

### Specific issues I've hit

**1. Citation lookup is text-only, no structured input**

`/citation-lookup/` takes a raw text string and does its own internal parsing. My parser has already extracted `volume=576, reporter="U.S.", page=644` but I have to serialize it back to a string and hope CL's parser agrees. When there's an off-by-one page number (common in briefs that cite a pinpoint page instead of the starting page), I can't say "find cases near page 644 in 576 U.S." — I have to make 4 extra calls trying pages 643, 645, 642, 646 individually. A structured lookup with a page tolerance would eliminate these.

**2. Opinions and RECAP are parallel universes**

I maintain two entirely separate processing pipelines (~200 lines each) because the response shapes and semantics are so different. Some real cases exist only in opinions, some only in RECAP, some in both with different metadata. There's no unified "does this case exist?" query. I've found [16 confirmed real federal cases](https://github.com/rlfordon/citation-verifier/blob/main/scratch/flp_contributions.md) that exist only in RECAP and not in the opinions DB.

**3. RECAP returns dockets, not documents**

When I search RECAP I get docket-level results, but I need a specific document (the opinion, not the "Certificate of Service"). So I have to: query docket-entries filtered by date, iterate all entries and their nested documents, then run my own keyword heuristic to distinguish opinions from procedural filings. There's no `doc_type` filter on RECAP search or docket-entries. A way to search for "opinion documents matching X" directly would save 1-2 round trips per citation and a lot of client-side heuristic code.

**4. `dateFiled` means different things on different endpoints**

On opinion search, `dateFiled` is the opinion date. On RECAP search, `dateFiled` is the case filing date (which can be years before the opinion). The actual document date is buried in `entry_date_filed` on individual docket entries, which I can only get by querying the docket-entries API. This semantic inconsistency requires a follow-up API call every time I need to verify a date against a RECAP result.

**5. Field naming is inconsistent across v4 endpoints**

I have dual-key lookups everywhere because search returns camelCase (`caseName`, `dateFiled`, `docketNumber`) while REST endpoints return snake_case (`case_name`, `date_filed`, `docket_number`). Same API version, different conventions:

\`\`\`python
case_name = r.get("caseName") or r.get("case_name", "")
date_filed = r.get("dateFiled") or r.get("date_filed", "")
\`\`\`

**6. URLs are sometimes relative, sometimes absent**

I have URL construction logic in 5 separate places — the API returns relative paths sometimes, absolute URLs sometimes, and nothing at all sometimes (just a cluster_id). Canonical absolute URLs on every result would help.

**7. Search doesn't handle standard legal abbreviations**

"Cnty." doesn't match "County", "Dep't" doesn't match "Department". I maintain a [47-term normalization table](https://github.com/rlfordon/citation-verifier/blob/main/src/citation_verifier/parser.py) expanding Indigo Book abbreviations client-side before every search call. I know this is tracked in #3089 and #3367, but it's worth noting here because it's one of the biggest sources of false negatives in practice and a simpler API could address it.

**8. No match-quality signal in search responses**

Search results come back with no indication of match strength. I built a [270-line multi-factor name matcher](https://github.com/rlfordon/citation-verifier/blob/main/src/citation_verifier/name_matcher.py) and a 200-line weighted scoring system because the API gives me a flat list with no signal about how well a result matches my query. Even a rough relevance score or match-type indicator ("exact match" vs "partial" vs "keyword") would help consumers avoid rebuilding this.

**9. The `docket` search parameter is silently broken**

The RECAP search `docket` parameter appears to be ignored — it returns unfiltered results regardless. I work around this by passing quoted docket numbers through the `q` parameter and filtering client-side. A parameter that silently does nothing is worse than a missing one. (Happy to provide test cases if this isn't already known.)

**10. The `citation` field on results is structurally unpredictable**

Sometimes it's a list, sometimes a string, sometimes empty even for cases that have reporter citations. WestLaw citations are almost never present. This makes it impossible to confirm whether a found case actually matches the cited reporter volume/page, which is a key verification signal.

### What would help most

From my use case, the highest-impact changes would be:

1. **Structured citation lookup** — accept volume/reporter/page with tolerance, not just raw text
2. **Unified case search** across opinions and RECAP in one call with one response shape
3. **Document-type filtering** on RECAP/docket-entries (e.g., `doc_type=opinion`)
4. **Consistent field naming and absolute URLs** across all v4 endpoints
5. **Abbreviation synonyms** in the search index (the Indigo Book table from #3367)

Happy to provide more specific examples or test cases for any of these. And thanks for all the work on CourtListener — even with these friction points, it's an incredible resource and the only reason a tool like mine is possible.
```

### Decision Factors

**Pros:**
- Real consumer feedback with concrete code examples
- Links to public repo so FLP can see the actual workarounds
- Constructive tone — acknowledges CL's value while highlighting friction
- Aligns directly with what #6946 is asking for (simpler APIs)
- Quantifies the problem (5-8 API calls per citation, 200+ lines of workaround code)

**Cons:**
- Long comment — may be too detailed for an early-stage discussion
- Some points overlap with existing issues (#3089, #3367, docket param)
- Could overwhelm if they're still in the "what should we build?" phase

**Recommendation:** Wait for some initial discussion on #6946 before posting. If the issue gets traction and FLP is actively soliciting feedback, this is exactly the kind of consumer perspective they need. If the issue goes quiet, may not be worth the noise.

### Submission Checklist

- [ ] Wait for initial discussion on #6946
- [ ] Verify repo is public and links work
- [ ] Review tone one more time
- [ ] Consider shortening to top 5 issues if comment is too long
- [ ] Post comment
- [ ] Update this doc with link

---

## 8. API Docs Blocked by Bot Protection / llms.txt

**Status:** SUBMITTED — [comment](https://github.com/freelawproject/courtlistener/issues/6040#issuecomment-3917584540)
**Target:** CourtListener [#6040](https://github.com/freelawproject/courtlistener/issues/6040)
**Filed:** 2026-02-17
**Type:** Comment on existing issue

### Summary

CourtListener's API documentation pages (`/help/api/`, `/help/api/rest/`, etc.) return HTTP 403 to programmatic requests, likely due to blanket bot protection (Cloudflare or similar) across the whole domain. This means LLM agents and developer tools cannot read the canonical API docs, forcing them to rely on stale blog posts and GitHub discussions — which is likely the root cause of the "LLMs reading our docs badly" problem that prompted #6040.

Issue #6040 proposes an `llms.txt` file, which would help, but the underlying 403 problem would affect that file too if served behind the same protection.

### How We Discovered This

While building citation-verifier, we used an LLM coding assistant (Claude Code) to help with API integration. When the assistant tried to fetch API documentation to understand endpoint parameters and response shapes:

- `courtlistener.com/help/api/` → **403**
- `courtlistener.com/help/api/rest/` → **403**

The assistant fell back on blog posts, GitHub discussions, and cached knowledge — leading to the exact "bad API usage" pattern mlissner described in #6040.

### Why This Matters

1. **LLM agents can't self-correct** — They make malformed API calls because they literally cannot read the docs
2. **Developer tools are affected too** — CI pipelines, doc aggregators, any programmatic access
3. **It wastes CL's rate limits** — Bad calls from uninformed consumers burn through the 5,000/hr budget
4. **The problem is self-reinforcing** — Blocked docs → bad usage → more noise on CL's API → more motivation for bot protection

### Proposed Comment for Issue #6040

```markdown
Wanted to add some concrete context on this — I think the problem may be more fundamental than LLMs misreading the docs.

**The API documentation pages actively block programmatic access.** Fetching `courtlistener.com/help/api/rest/` from any non-browser client returns 403. This means LLM agents and developer tools aren't reading the docs badly — they can't read them at all. They fall back on whatever they can find: old blog posts, GitHub discussions, cached training data — which explains the bad API usage you're seeing.

I hit this while building [citation-verifier](https://github.com/rlfordon/citation-verifier), a tool that checks legal citations against the CL API to catch AI-hallucinated cases. My LLM coding assistant tried to fetch the API docs to help with integration and got 403'd on every `/help/api/` page. It ended up relying on GitHub discussions and blog posts instead — functional but incomplete.

An `llms.txt` file would help, but only if it's served without the same bot protection. A potentially quicker complementary win: exempt the `/help/` paths from whatever bot filtering is in place. Those pages are static documentation with zero abuse risk, and making them accessible would immediately improve the quality of every LLM-assisted integration being built against the API.
```

### Decision Factors

**Pros:**
- Concrete evidence of a problem they may not fully understand
- Reframes #6040 from "LLMs are bad at reading" to "LLMs can't read at all" — increases urgency
- Actionable suggestion (exempt `/help/` from bot protection) that's independent of `llms.txt`
- Short, focused comment — not overwhelming
- Directly relevant to an open issue they created

**Cons:**
- #6040 has been open since July 2025 with minimal activity — may not be prioritized
- Bot protection may be a deliberate security posture they don't want to weaken
- Could be seen as "you should change your infrastructure for my use case"

**Recommendation:** Comment on #6040. The reframing adds genuine value — if they think the problem is LLM output quality, they'll build `llms.txt` and wonder why it doesn't help (because it'll also get 403'd). The 403 evidence changes the diagnosis.

### Draft

See `scratch/drafts/cl-6040-api-docs-bot-protection.md` for the full comment text.

---

## 9. eyecite: Slip Opinion Placeholder Absorbed into Case Name

**Status:** DRAFT
**Target:** eyecite
**Type:** Bug report

### Summary

When a citation uses slip opinion placeholders like `-- F. Supp. 3d ----` or `--- S.Ct. ---` (indicating the opinion hasn't been assigned final reporter pagination yet), eyecite absorbs the placeholder text into the defendant/case name field. This poisons downstream searches — CL's Solr index chokes on the `--` syntax and returns 500 errors.

### Our Findings

**Affected pattern:** Citations like:
- `Johnson v. Dunn, -- F. Supp. 3d ----, 2025 WL 2086116 (N.D. Ala. July 23, 2025)`
- `Smith v. Jones, --- S.Ct. ---, 2025 WL 123456 (2025)`

**What eyecite does:** The `-- F. Supp. 3d ----` text is not recognized as a reporter citation (because `--` is not a valid volume number). Instead, the metadata extraction regex treats it as part of the case name, so `parsed.metadata.defendant` ends up containing `Dunn, -- F. Supp. 3d ----`.

**Impact:**
- Case name searches against CL fail with HTTP 500 (Solr can't parse `--` in query strings)
- Even if the search didn't error, the polluted case name would prevent name matching
- This is an increasingly common pattern as more slip opinions are cited before final pagination

### Our Workaround

Added a regex in `parser.py` to strip slip opinion junk before using the case name:

```python
_SLIP_OPINION_JUNK = re.compile(r",?\s*-{2,}\s+\S.*?-{2,}\s*$")
```

Applied to both `parse_citation()` and `parsed_citation_from_eyecite()` paths.

### Evidence

Test case: `Johnson v. Dunn, -- F. Supp. 3d ----, 2025 WL 2086116 (N.D. Ala. July 23, 2025)`
- Without fix: 0% confidence, both opinion and RECAP search return 500 errors
- With fix: 90% confidence, exact date match via RECAP

### Proposed Fix (eyecite)

The ideal fix would be in eyecite's metadata parsing — either:
1. Recognize `--` / `---` as a placeholder volume number and parse `-- F. Supp. 3d ----` as a citation (with null volume/page), or
2. Strip the placeholder pattern before metadata extraction so it doesn't pollute the case name

Option 1 is more correct but more invasive. Option 2 is simpler and matches our workaround.

### Decision Factors

**Pros:**
- Clear bug with concrete reproduction steps
- Increasingly common as more slip opinions are cited
- Causes cascading failures (500 errors, not just bad matches)
- We have a working fix to offer

**Cons:**
- Our client-side workaround handles it fine
- Slip opinion placeholders are somewhat niche
- May require discussion about whether eyecite should parse placeholder citations

**Recommendation:** File after gathering a few more real-world examples of slip opinion citations to demonstrate the pattern is common. Could pair with issues #296/#297 as a batch of metadata parsing improvements.

### Submission Checklist

- [ ] Collect 3-5 real-world examples of slip opinion placeholder citations
- [ ] Check if eyecite already has an issue for this
- [ ] Draft issue with reproduction steps
- [ ] Submit issue
- [ ] Update this doc with link

---

## 10. Search API Defaults to Published Only; stat_ Filters Undocumented

**Status:** SUBMITTED
**Target:** CourtListener [#7049](https://github.com/freelawproject/courtlistener/issues/7049)
**Type:** Documentation / API behavior
**Filed:** 2026-03-02

### Summary

The opinion search API (`/api/rest/v4/search/?type=o`) defaults to returning only Published opinions when no `stat_` parameters are provided. The API help page mentions this in passing but doesn't document the `stat_` parameter names or values — they're only discoverable by inspecting URL parameters on the courtlistener.com website.

The core problem is that `recap_into_opinions` classifies ingested opinions as `precedential_status: Unknown`, and the search default hides Unknown. These two choices work against each other — CL ingests district court opinions from RECAP, then the search defaults make them invisible.

### Impact

For S.D. Ohio (`ohsd`):
- `type=o&court=ohsd` (default, Published only): 4,923
- `type=o&court=ohsd&stat_Unknown=on`: 14,518

Nearly 3x as many opinions hidden by default. Directly caused us to misreport 9 cases as missing in #6963.

### Our Fix

Added `stat_Published=on`, `stat_Unpublished=on`, `stat_Unknown=on` to both sync and async `search_opinions()` in `client.py`. Commit [753e2df](https://github.com/rlfordon/citation-verifier/commit/753e2df).

### Draft

See `scratch/drafts/cl-stat-unknown-default.md` for the full issue text as filed.

---

## 11. Batch Document Text Retrieval

**Status:** DRAFT
**Target:** CourtListener (new endpoint or enhancement to existing APIs)
**Type:** Feature request
**Related:**
- Foresight [#27](https://github.com/freelawproject/foresight/issues/27) — Lookup multiple citations and export text as PDF (our issue)
- CL [#6946](https://github.com/freelawproject/courtlistener/issues/6946) — Simpler Case Law APIs

### Summary

After resolving citations via the batch citation-lookup API (one call, very efficient), the next step — actually reading the opinion text — requires **2-5 individual API calls per document**. For a brief with 25 cited cases, that's 50-125 sequential API calls just to download the text. There's no way to say "give me the plain text for these 25 cluster IDs."

Propose a **batch document text endpoint** that accepts a list of cluster IDs (or opinion URLs) and returns the plain text for each in a single response.

### The Problem

Fetching a single opinion's text currently requires a multi-step chain:

**For opinions (cluster URL):**
1. `GET /clusters/{id}/` — get cluster metadata and `sub_opinions` list
2. `GET /opinions/{id}/` — for each sub_opinion, fetch the actual text (`plain_text` or `html_with_citations`)
3. `GET /dockets/{id}/` — fetch docket metadata (case name, court, date)
4. `GET /courts/{id}/` — resolve court name

= **3-4 API calls per opinion**

**For RECAP documents (docket URL):**
1. `GET /docket-entries/?docket={id}` — find the entry
2. `GET /recap-documents/{id}/` — for each document in the entry, fetch `plain_text`
3. `GET /dockets/{id}/` — docket metadata
4. `GET /courts/{id}/` — court name

= **3-5 API calls per RECAP document**

With rate limiting (1 req/sec for free tier), downloading text for 25 cases takes 1-2 minutes of pure API overhead. Our web app (`/api/download-texts`) and `/verify-brief` skill both hit this bottleneck — it's the slowest part of the pipeline by far.

### Our Workaround

We parallelize with `asyncio.gather` and a concurrency semaphore, but this just shifts the bottleneck to rate limiting. Each document still requires its own chain of dependent calls.

```python
# web/app.py — current approach: N parallel chains, each 2-4 calls deep
async with AsyncCourtListenerClient(api_token=token) as client:
    fetched = await asyncio.gather(
        *[_fetch_one(client, item) for item in items]  # each is 2-4 API calls
    )
```

### Proposed Endpoint

```
POST /api/rest/v4/bulk-text/
Content-Type: application/json

{
  "cluster_ids": [145875, 2812209, 4976543],
  "format": "plain_text",  // optional, default "plain_text"
                            // also: "html_with_citations", "html", "best"
  "fields": ["text", "case_name", "date_filed", "court"]  // optional
}
```

Response:
```json
{
  "results": [
    {
      "cluster_id": 145875,
      "case_name": "Ashcroft v. Iqbal",
      "date_filed": "2009-05-18",
      "court": "Supreme Court of the United States",
      "text": "SUPREME COURT OF THE UNITED STATES...",
      "format_returned": "plain_text",
      "source": "opinion"
    },
    {
      "cluster_id": 2812209,
      "case_name": "...",
      "text": "...",
      "format_returned": "html_with_citations",
      "source": "opinion"
    },
    {
      "cluster_id": 4976543,
      "error": "no_text_available"
    }
  ]
}
```

Key design points:
- Accepts cluster IDs (the natural output of citation-lookup, which already returns cluster IDs)
- Returns text + basic metadata in one call
- **`format` parameter** controls the text format:
  - `plain_text` — stripped text (default, smallest payload)
  - `html_with_citations` — HTML with hyperlinked cross-references to other CL opinions (useful for research tools)
  - `html` — raw HTML without citation links
  - `best` — return whichever format is available, preferring `plain_text` > `html_with_citations` > `html` (mirrors what consumers already do as a fallback chain)
- `format_returned` in the response indicates what was actually served (not all opinions have all formats)
- Handles the cluster → sub_opinions → opinion text resolution server-side
- Reports errors per-item rather than failing the whole batch
- Optional `fields` parameter to limit response size when you only need text

### Use Cases

1. **Citation verification pipelines** — after batch citation-lookup resolves 25 citations, fetch all 25 opinion texts to check whether they actually support what they're cited for (our `/verify-brief` workflow)
2. **Foresight citation lookup page** (Foresight #24/#27) — the designed UI shows citation matches and lets users export the text. Batch text retrieval would make this practical at scale.
3. **Legal research tools** — any tool that needs to read multiple opinions (brief analysis, case comparison, research assistants)
4. **LLM/MCP integrations** — an MCP tool that retrieves opinions for an AI agent. One batch call is far more practical than N sequential chains.

### Why This Matters

The citation-lookup batch API was a huge improvement — it collapsed N lookup calls into one. But it created an asymmetry: you can *find* 25 cases in one call, but *reading* them still takes 50-125 calls. The bottleneck just moved downstream.

This is especially visible in the hallucination-detection use case (#3960). Verifying that a citation exists is step one. Step two — confirming the cited case actually says what the brief claims — requires reading the opinion. That step is now the dominant cost.

### Decision Factors

**Pros:**
- Eliminates the biggest remaining API bottleneck for multi-citation workflows
- Natural companion to the existing batch citation-lookup
- Server-side resolution of the cluster → sub_opinions → text chain is simpler and faster than having every consumer reimplement it
- Aligns with #6946 (simpler APIs) and Foresight #24/#27

**Cons:**
- Response payloads could be large (opinion texts are long). May need streaming or pagination.
- Server-side cost — resolving text for many opinions in one request is more resource-intensive than individual calls
- Could be implemented as a simpler enhancement: just include `plain_text` in the cluster data returned by citation-lookup (though this would bloat the citation-lookup response for consumers who don't need it)

**Alternatives considered:**
- **Include text in citation-lookup response** — simpler but bloats the response and couples two different use cases (finding citations vs. reading them)
- **GraphQL-style field selection on existing endpoints** — more flexible but much larger engineering effort
- **Just document the multi-step chain** — shifts the burden to consumers, which is where it is today

**Recommendation:** Propose as a standalone endpoint. Frame it as the natural next step after batch citation-lookup: "You gave us a great way to find cases in bulk. Now we need a way to read them in bulk."

### Next Steps

- [ ] Find prior GitHub comment where we mentioned wanting this (Foresight #27?)
- [ ] Quantify the API call reduction with real numbers from `/verify-brief` runs
- [ ] Consider whether this fits in the #6946 discussion or needs its own proposal
- [ ] Discuss with Mike informally before formal proposal

---

## Submission Guidelines

### Use `/file-issue` for New Submissions

The `/file-issue` Claude Code skill walks through the full issue-filing workflow: duplicate search, evidence gathering, repo norm study, drafting, and antipattern review. Use it instead of filing issues manually — it catches the seven antipatterns that get issues ignored.

### Before Submitting Anything

1. **Search existing issues** - Don't duplicate
2. **Verify still relevant** - Is issue already fixed?
3. **Assess value** - Will this help FLP or is it noise?
4. **Check tone** - Helpful data, not complaints
5. **Provide context** - We're users building a tool, sharing findings

### What Makes a Good Contribution

**Good:**
- Concrete data with examples
- Prioritized/categorized findings
- Shows real-world impact
- Includes cluster IDs for verification
- Offers to provide more info
- Respectful of FLP's roadmap

**Bad:**
- Vague "this doesn't work"
- Demands for fixes
- Single edge case without pattern
- No examples or evidence
- Tone-deaf to their constraints

### After Submitting

Update this document:
- Link to our comment/issue
- Note any FLP response
- Track outcome
- Update status

---

## Template for New Potential Contributions

```markdown
## N. [Title]

**Status:** DRAFT/READY/SUBMITTED/DECLINED
**Target:** [Project and issue # if applicable]
**Type:** Bug report / Feature request / Data contribution / Comment

### Summary
[One paragraph: what is this about?]

### Our Findings
[What did we discover? Include examples, data, cluster IDs]

### Evidence
[Test results, code references, reproduction steps]

### Proposed Submission
[Draft of what we'd submit to FLP]

### Decision Factors
**Pros:** [Why submit?]
**Cons:** [Why not?]
**Recommendation:** [Submit / Wait / Decline]

### Next Steps
- [ ] Checklist items before submitting
```

---

## Maintenance

Review this document:
- Before each potential FLP contribution
- After submitting anything (update status, add links)
- Quarterly to remove outdated items

---

## 12. Criminal District Court Opinions Are RECAP-Only

**Status:** SUBMITTED (comment on an existing open issue, 2026-09-19)
**Target:** CourtListener [#7596](https://github.com/freelawproject/courtlistener/issues/7596#issuecomment-5745591911) — "Bulk opinions coverage — are criminal rulings included, or RECAP-only?" (opened 2026-07-20 by another researcher; no maintainer reply at the time)
**Type:** Diagnosis + feature question
**Related:** #5 above / #6963 (civil opinions missing), #3790 and #4642 (RECAP into Opinions; criminal planned as next batch)

### Summary

District-court criminal written opinions are not in the opinions database, by design: `recap_document_into_opinions` (`cl/corpus_importer/tasks.py`) skips any federal district document whose docket number lacks "cv"; the bulk command only queues documents filed after each court's latest non-RECAP cluster. Surfaced by a corpus-research round (Eighth Circuit clothing re-entry; closest ruling, *United States v. Avalos*, D. Neb. 2013, exists only in RECAP and reportedly not on Westlaw/Lexis).

### Evidence posted

- 2020: "United States v." opinions = 4 of 704 (D. Neb.), 8 of 409 (W.D. Mo.), 4 of 1,897 (E.D. Mo.), vs 36–47 RECAP suppression rulings with text per court.
- Recent filings (Mar 1 – Aug 15, 2026), `is_free_on_pacer=True`, ≥4 pages: criminal suppression rulings 0 of 84 (46 courts) have an opinion cluster on the docket; civil SJ control 37 of 41 (90%). June 2026, 15 courts: 2,212 opinions, 1 captioned "United States v."
- D. Neb. 2013–2018: 129 opinions in the DB vs 5,373 for 2019+ (the date cutoff) — possibly also the explanation for the older civil misses in #6963.

### Asked

Is criminal import still planned now that `classify_case_name_by_llm` exists; is a backfill of pre-cutoff free written opinions in scope; can the docs say RECAP search is the only route to criminal rulings.

### Where the work lives

`Projects/corpus-research/experiments/01-grounding-to-paragraph/` — `08-courtlistener-issue-7596-comment-draft.md` (as posted), `r5-evidence/recent-criminal-coverage-check.json` (the 84/41 sample), `07-r5-district-court-triage.md` (the research round). Relation to the coverage study in `case-law-proposition-benchmark/scratch/cl-coverage-offshoot/`: that study measures cited-case findability on a civil-skewed sample (5 of 50 district cites were "United States v."), so it could not see this gap.
