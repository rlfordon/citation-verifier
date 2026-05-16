# Step 5 — audit of fallback rescues

Audited 43 rescues from the rigorous staged fallback 
(step 4c) to distinguish true matches from false positives. Each 
rescue had three tests applied:

1. **citation_in_cluster** — strong TRUE: cited reporter cite appears 
   in the matched cluster's `citations[]` field.
2. **parties_present** — secondary: both sides of cited 'X v. Y' name 
   appear (any token, after legal-abbrev normalization) in matched name.
3. **court_date** — corroboration: matched date within +/- 2 years.

Verdict logic:
- citation match -> VERIFIED_TRUE
- parties match + court/date corroborates -> VERIFIED_TRUE
- only one party matches -> LIKELY_FALSE
- no parties match -> LIKELY_FALSE
- parties match but court/date doesn't -> AMBIGUOUS
- cited_case_name missing -> CANT_AUDIT (LLM dropped name)

## Audit verdicts

| tier | VERIFIED_TRUE | LIKELY_FALSE | AMBIGUOUS | CANT_AUDIT |
|---|---|---|---|---|
| SCOTUS | 0 | 1 | 0 | 0 |
| Circuit | 1 | 0 | 0 | 0 |
| State_COLR | 2 | 1 | 0 | 0 |
| State_IAC | 7 | 1 | 0 | 0 |
| Federal_District | 19 | 10 | 1 | 0 |

## Corrected per-tier coverage

Each tier's coverage = (citation_lookup baseline + audit-VERIFIED_TRUE rescues) / 50.
AMBIGUOUS rows are excluded from numerator (conservative).

| tier | n | cite_lookup | +TRUE rescues | corrected | pct |
|---|---|---|---|---|---|
| SCOTUS | 50 | 47 | +0 | 47 | 94.0% |
| Circuit | 50 | 43 | +1 | 44 | 88.0% |
| State_COLR | 50 | 41 | +2 | 43 | 86.0% |
| State_IAC | 50 | 26 | +7 | 33 | 66.0% |
| Federal_District | 50 | 19 | +19 | 38 | 76.0% |

## Overall

- citation_lookup baseline: 176/250 = 70.4%
- + audited TRUE rescues:   205/250 = 82.0%
- audited false positives:  13 (would have inflated coverage by 5.2 pp)
- ambiguous (excluded from numerator): 1
- can't audit (LLM dropped name): 0
