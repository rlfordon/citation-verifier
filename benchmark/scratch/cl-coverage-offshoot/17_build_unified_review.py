"""
Build a single unified review CSV with all 250 sample rows annotated
with the judgment made at every phase of the analysis pipeline.

Two output files:
  unified_review.csv          Full audit trail (32+ columns). Used for
                              methodology transparency.
  unified_review_concise.csv  Reviewer-facing view (~8 columns). Used
                              for sharing with CL/FLP.

Two headline columns are derived for each row:
  coverage   one of {found_via_lookup, in_opinions, in_recap,
                     not_found_anywhere, excluded}. Filter on this for
                     the headline coverage number.
  diagnosis  granular explanation (what went right or wrong). Reuses
                     the existing per-phase status strings:
    - in_cl_via_citation_lookup    Phase 4 returned VERIFIED/LIKELY_REAL/POSSIBLE_MATCH
    - in_cl_via_opinion_rescue     Phase 4 NF; Phase 4c Stage A rescued; audit TRUE
    - in_cl_via_recap_rescue       Phase 4 NF; Phase 4c Stage B rescued; audit TRUE
    - rescue_was_false_positive    Phase 4c rescued but audit said LIKELY_FALSE
    - audit_ambiguous              Audit returned AMBIGUOUS verdict
    - not_in_cl                    Phase 4c didn't rescue at all
    - extraction_artifact_no_name  LLM dropped cited_case_name
    - duplicate_of_fuller_sibling  Phase 6: short-form with fuller sibling
    - excluded_incomplete_citation Phase 6: unresolvable short-form

Manual overrides for the 7 user-investigated false negatives are read
from manual_corrections.csv (joined on citing_cluster + citation_string).
Each override carries: coverage_override, diagnosis_override,
diagnosis_detail_override, corrected_url. The original verifier picks
remain in p4c_stage_*_url for the audit trail; user_corrected_url is a
separate column populated only for overridden rows.
"""
from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


# === Short-form citation detection (Phase 6) ===
# A "short form" is a pin-cite or stub that cannot be resolved to a unique
# opinion via CL's citation lookup alone, e.g.:
#   "594 U.S. at 442"        — pin cite, no start page
#   "523 U.S."               — volume+reporter, no page at all
#   "360 F. Supp. 2d"        — reporter stub with no page
#   "B225051"                — California internal docket number
#   "Id. at 741", "Jd. at 7" — Id./OCR-garbled short forms
_SHORT_FORM_PATTERNS = (
    re.compile(r"^\s*\d+\s+[A-Za-z][\w.\s()]*?\s+at\s", re.IGNORECASE),  # "N REPORTER at P"
    re.compile(r"^\s*\d+\s+[A-Za-z][\w.\s]*[A-Za-z.]\s*$"),               # "N REPORTER" stub (no page)
    re.compile(r"^\s*[A-Z]\d{6}\s*$"),                                    # California docket "B225051"
    re.compile(r"^\s*[IJij]d\.?\s+at\b", re.IGNORECASE),                  # "Id. at P" / OCR-garbled "Jd."
)


def is_short_form(cite: str) -> bool:
    """True iff the citation string is a short form/stub that can't be
    resolved to a unique opinion via citation_lookup."""
    s = (cite or "").strip()
    if not s:
        return False
    return any(p.match(s) for p in _SHORT_FORM_PATTERNS)


def _normalize_name(name: str) -> str:
    """Loose normalize for sibling matching: lowercase, strip punctuation,
    collapse whitespace. Used only to group short-forms with their fuller
    antecedents in the same citing opinion."""
    n = (name or "").lower().strip()
    n = re.sub(r"[.,]", "", n)
    n = re.sub(r"\s+", " ", n)
    return n


def find_fuller_siblings(
    sample_rows: list[dict[str, str]],
) -> dict[tuple[str, str], str]:
    """For each (citing_cluster, citation_string) row, return its
    dedup status:
      ""                 — not a short-form (no action)
      "duplicate"        — short-form WITH a fuller sibling in same opinion
      "excluded"         — short-form WITHOUT a fuller sibling (no name or
                           no antecedent extracted)
    """
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for r in sample_rows:
        k = (r.get("citing_cluster", ""), _normalize_name(r.get("cited_case_name", "")))
        groups[k].append(r)

    status: dict[tuple[str, str], str] = {}
    for r in sample_rows:
        cite = r.get("citation_string", "")
        row_key = (r.get("citing_cluster", ""), cite)
        if not is_short_form(cite):
            status[row_key] = ""
            continue
        name_key = _normalize_name(r.get("cited_case_name", ""))
        if not name_key:
            # No name to match against; can't find a fuller sibling
            status[row_key] = "excluded"
            continue
        sibs = groups.get((r.get("citing_cluster", ""), name_key), [])
        has_fuller = any(
            s is not r and not is_short_form(s.get("citation_string", ""))
            for s in sibs
        )
        status[row_key] = "duplicate" if has_fuller else "excluded"
    return status

HERE = Path(__file__).parent
SAMPLE_CSV = HERE / "final_200.csv"                          # 250-row sample
STEP4_CSV = HERE / "coverage_per_citation.csv"               # citation_lookup status
STEP4C_CSV = HERE / "staged_fallback_rigorous_per_row.csv"   # rigorous fallback
STEP5_CSV = HERE / "audit_per_row.csv"                       # audit verdicts
CORRECTIONS_CSV = HERE / "manual_corrections.csv"            # 7 user overrides
RECAP_DIAGNOSIS_CSV = HERE / "recap_diagnosis.csv"           # in_recap subreasons
OUT_CSV = HERE / "unified_review.csv"
OUT_CONCISE_CSV = HERE / "unified_review_concise.csv"


# ---- coverage classification ------------------------------------------------
# Map each granular `diagnosis` value to one of four coverage buckets.
# Rows mapped to `excluded` drop out of the denominator (unmeasurable);
# everything else stays in. `not_found_anywhere` is the conservative
# bucket for both true misses and audit-rejected wrong-matches.

DIAGNOSIS_TO_COVERAGE = {
    # Found normally — citation_lookup API resolved it
    "in_cl_via_citation_lookup":       "found_via_lookup",
    # Opinion-cluster lookup misses, auto-classified by why lookup failed
    "cl_cluster_citations_empty":      "in_opinions",
    "cl_cluster_cites_incomplete":     "in_opinions",  # defensive
    "cl_lookup_indexed_but_missed":    "in_opinions",  # defensive — would be a CL bug
    # RECAP-only — case in PACER docket but no opinion cluster ingested
    "cl_docket_only_no_cluster":       "in_recap",
    # Manual-override diagnoses (3 user-investigated discoverability patterns)
    "caption_divergence_rule_25d":     "in_opinions",
    "ssa_pseudonym":                   "in_opinions",
    # Not in CL (or audit rejected the rescue)
    "not_in_cl":                       "not_found_anywhere",
    "rescue_was_false_positive":       "not_found_anywhere",
    "audit_ambiguous":                 "not_found_anywhere",
    # Unmeasurable (Phase 6 dedup / LLM artifacts)
    "extraction_artifact_no_name":     "excluded",
    "extraction_artifact_unparseable": "excluded",
    "duplicate_of_fuller_sibling":     "excluded",
    "excluded_incomplete_citation":    "excluded",
}


def load_index(path: Path, key_fn) -> dict[Any, dict[str, str]]:
    """Load a CSV into a dict keyed by `key_fn(row)`."""
    out: dict[Any, dict[str, str]] = {}
    if not path.exists():
        return out
    for r in csv.DictReader(path.open(encoding="utf-8")):
        out[key_fn(r)] = r
    return out


def main() -> int:
    # Key on (citing_cluster, citation_string) — unique per row across the pipeline
    def key(r: dict[str, str]) -> tuple[str, str]:
        return (r.get("citing_cluster", ""), r.get("citation_string", ""))

    sample = list(csv.DictReader(SAMPLE_CSV.open(encoding="utf-8")))
    step4 = load_index(STEP4_CSV, key)
    step4c = load_index(STEP4C_CSV, key)
    step5 = load_index(STEP5_CSV, key)
    corrections = load_index(CORRECTIONS_CSV, key)
    recap_diag = load_index(RECAP_DIAGNOSIS_CSV, key)

    print(f"Loaded sample: {len(sample)}, step4: {len(step4)}, "
          f"step4c: {len(step4c)}, step5: {len(step5)}, "
          f"corrections: {len(corrections)}, recap_diag: {len(recap_diag)}")

    # Phase 6 — short-form dedup + exclusion
    short_form_status = find_fuller_siblings(sample)
    n_dup = sum(1 for v in short_form_status.values() if v == "duplicate")
    n_exc = sum(1 for v in short_form_status.values() if v == "excluded")
    print(f"Phase 6 short-form: {n_dup} duplicates of fuller siblings, "
          f"{n_exc} unresolvable (excluded from denominator)")

    out_rows = []
    for r in sample:
        k = key(r)
        s4 = step4.get(k, {})
        s4c = step4c.get(k, {})
        s5 = step5.get(k, {})

        cited_tier = r["cited_tier"]
        cited_case = r.get("cited_case_name") or ""

        # Phase 4 — strict citation_lookup
        p4_status = s4.get("lookup_status", "")
        p4_in_cl = s4.get("in_cl", "")
        p4_matched_name = s4.get("lookup_matched_name", "")
        p4_matched_url = s4.get("lookup_matched_url", "")
        p4_diag = s4.get("lookup_diagnostics", "")

        # Phase 4c — rigorous fallback (only NOT_FOUND rows went through this)
        p4c_outcome = s4c.get("final_outcome", "")
        p4c_stage_a_status = s4c.get("stage_a_status", "")
        p4c_stage_a_score = s4c.get("stage_a_score", "")
        p4c_stage_a_match = s4c.get("stage_a_matched_name", "")
        p4c_stage_a_url = s4c.get("stage_a_url", "")
        p4c_stage_b_status = s4c.get("stage_b_status", "")
        p4c_stage_b_score = s4c.get("stage_b_score", "")
        p4c_stage_b_match = s4c.get("stage_b_matched_name", "")
        p4c_stage_b_url = s4c.get("stage_b_url", "")

        # Phase 5 — audit (only rescues went through this)
        p5_verdict = s5.get("verdict", "")
        p5_reason = s5.get("reason", "")
        p5_test_cite = s5.get("test_cite_match", "")
        p5_test_parties = s5.get("test_parties", "")
        p5_test_court_id = s5.get("test_court_id", "")
        p5_test_date = s5.get("test_date", "")
        p5_cluster_citations = s5.get("matched_cluster_citations", "")

        # PIPELINE-DERIVED CLASSIFICATION
        dedup_status = short_form_status.get(k, "")

        # Phase 6 override: a short-form citation that has a fuller sibling
        # in the same opinion is a duplicate — drop it (the sibling is the
        # canonical row for this case in this opinion). Applies regardless
        # of the short-form's own Phase 4/4c/5 outcome.
        if dedup_status == "duplicate":
            diagnosis = "duplicate_of_fuller_sibling"
            diagnosis_detail = "short-form citation; fuller sibling exists in same opinion"
        # Phase 6 override: a short-form WITHOUT a fuller sibling is only
        # excluded if it wasn't a lucky citation_lookup hit. A lucky hit is
        # a real match and should stay in the numerator/denominator.
        elif dedup_status == "excluded" and p4_in_cl != "yes":
            diagnosis = "excluded_incomplete_citation"
            diagnosis_detail = "short-form citation; no antecedent in extraction — not measurable"
        elif p4_in_cl == "yes":
            # Step 4 already had it in CL via citation_lookup
            diagnosis = "in_cl_via_citation_lookup"
            diagnosis_detail = f"citation_lookup returned {p4_status}"
        elif not cited_case.strip():
            diagnosis = "extraction_artifact_no_name"
            diagnosis_detail = "LLM dropped cited_case_name — can't verify"
        elif p4c_outcome == "still_not_found":
            diagnosis = "not_in_cl"
            diagnosis_detail = "rigorous fallback found no credible match"
        elif p4c_outcome == "parse_error":
            diagnosis = "extraction_artifact_unparseable"
            diagnosis_detail = "couldn't parse citation_string"
        elif p4c_outcome == "rescued_by_opinion_search":
            if p5_verdict == "VERIFIED_TRUE":
                # Why did citation_lookup miss an opinion that's in CL?
                # Derive the discoverability issue from the audit's
                # cite-in-cluster test:
                #   cite_test=='no' + empty citations[] -> cluster ingested
                #     but citations[] not populated (CL ingestion lag).
                #     This is THE dominant lookup-miss pattern (the
                #     2026-05-15 analysis found 22/22 opinion rescues fit
                #     this shape).
                #   cite_test=='no' + populated citations[] -> cluster has
                #     a partial cite list and the cited cite isn't on it.
                #     (None observed in this sample.)
                #   cite_test=='yes' -> cite IS in citations[]; would be a
                #     CL lookup bug. (None observed in this sample.)
                if p5_test_cite == "no" and not p5_cluster_citations.strip():
                    diagnosis = "cl_cluster_citations_empty"
                    diagnosis_detail = (
                        "Opinion cluster in CL but citations[] field is empty. "
                        "Citation lookup can't resolve the cite to this cluster. "
                        "Verifier's name-based fallback found it."
                    )
                elif p5_test_cite == "no":
                    diagnosis = "cl_cluster_cites_incomplete"
                    diagnosis_detail = (
                        f"Cluster has populated citations[] but the cited "
                        f"cite isn't among them: [{p5_cluster_citations[:120]}]. "
                        f"Partial cite list."
                    )
                elif p5_test_cite == "yes":
                    diagnosis = "cl_lookup_indexed_but_missed"
                    diagnosis_detail = (
                        f"Cite IS in cluster's citations[] but citation_lookup "
                        f"failed to find it. Would indicate a CL lookup-side bug. "
                        f"Cluster cites: [{p5_cluster_citations[:120]}]"
                    )
                else:
                    diagnosis = "cl_cluster_audit_unclear"
                    diagnosis_detail = f"audit confirmed but cite-test inconclusive: {p5_reason}"
            elif p5_verdict == "LIKELY_FALSE":
                diagnosis = "rescue_was_false_positive"
                diagnosis_detail = f"audit overturned: {p5_reason}"
            else:
                diagnosis = f"audit_{p5_verdict.lower()}"
                diagnosis_detail = p5_reason
        elif p4c_outcome == "rescued_by_recap":
            if p5_verdict == "VERIFIED_TRUE":
                # RECAP rescue = case lives in PACER as a docket but no
                # opinion cluster was ingested. citation_lookup is
                # cluster-scoped, so it can't reach RECAP-only cases at all.
                diagnosis = "cl_docket_only_no_cluster"
                diagnosis_detail = (
                    "Case present in CL's RECAP/PACER data as a docket, but "
                    "no opinion cluster has been ingested. Citation lookup "
                    "is cluster-scoped and can't reach docket-only cases."
                )
            elif p5_verdict == "LIKELY_FALSE":
                diagnosis = "rescue_was_false_positive"
                diagnosis_detail = f"audit overturned: {p5_reason}"
            else:
                diagnosis = f"audit_{p5_verdict.lower()}"
                diagnosis_detail = p5_reason
        else:
            diagnosis = "unknown"
            diagnosis_detail = f"unexpected pipeline state: p4={p4_status} p4c={p4c_outcome}"

        # Derive coverage bucket from the pipeline diagnosis
        coverage = DIAGNOSIS_TO_COVERAGE.get(diagnosis, "not_found_anywhere")

        # Manual override: when the user has hand-verified a row, replace
        # diagnosis/coverage/url. Preserves the verifier's pick in
        # p4c_stage_*_url so the audit trail remains intact.
        correction = corrections.get(k, {})
        user_corrected_url = correction.get("corrected_url", "")
        cl_matched_name_override = correction.get("cl_matched_name_override", "")
        if correction:
            cov_o = correction.get("coverage_override", "").strip()
            diag_o = correction.get("diagnosis_override", "").strip()
            detail_o = correction.get("diagnosis_detail_override", "").strip()
            if cov_o:
                coverage = cov_o
            if diag_o:
                diagnosis = diag_o
            if detail_o:
                diagnosis_detail = detail_o

        # Pull a few helpful identity columns
        citing_case = r.get("citing_case_name", "")
        citing_court = r.get("citing_court_id", "")
        citing_cluster = r.get("citing_cluster", "")
        citing_level = r.get("citing_level", "")

        out_rows.append({
            # ----- identity -----
            "cited_tier": cited_tier,
            "cited_case_name": cited_case,
            "citation_string": r.get("citation_string", ""),
            "cited_year": r.get("year", ""),
            "cited_court_hint": r.get("court_hint", ""),
            "parenthetical": (r.get("parenthetical") or "")[:200],
            # ----- citing context -----
            "citing_case_name": citing_case[:80],
            "citing_court_id": citing_court,
            "citing_level": citing_level,
            "citing_cluster": citing_cluster,
            # ----- headline classification -----
            "coverage": coverage,
            "diagnosis": diagnosis,
            "diagnosis_detail": diagnosis_detail,
            "user_corrected_url": user_corrected_url,
            "cl_matched_name_override": cl_matched_name_override,
            # RECAP sub-classification (only populated for in_recap rows; the
            # 18_diagnose_recap_cases.py script writes recap_diagnosis.csv).
            "recap_subreason": recap_diag.get(k, {}).get("subreason", ""),
            "recap_subreason_detail": recap_diag.get(k, {}).get("subreason_detail", ""),
            # ----- phase 4: strict citation_lookup -----
            "p4_status": p4_status,
            "p4_in_cl": p4_in_cl,
            "p4_matched_name": p4_matched_name,
            "p4_matched_url": p4_matched_url,
            "p4_diag": p4_diag[:200],
            # ----- phase 4c: rigorous staged fallback -----
            "p4c_outcome": p4c_outcome,
            "p4c_stage_a_status": p4c_stage_a_status,
            "p4c_stage_a_score": p4c_stage_a_score,
            "p4c_stage_a_match": p4c_stage_a_match[:80],
            "p4c_stage_a_url": p4c_stage_a_url,
            "p4c_stage_b_status": p4c_stage_b_status,
            "p4c_stage_b_score": p4c_stage_b_score,
            "p4c_stage_b_match": p4c_stage_b_match[:80],
            "p4c_stage_b_url": p4c_stage_b_url,
            # ----- phase 5: audit verdict -----
            "p5_verdict": p5_verdict,
            "p5_reason": p5_reason,
            "p5_test_cite_in_cluster": p5_test_cite,
            "p5_test_parties": p5_test_parties,
            "p5_test_court_id_match": p5_test_court_id,
            "p5_test_date_close": p5_test_date,
            "p5_matched_cluster_citations": p5_cluster_citations[:200],
        })

    fields = list(out_rows[0].keys())
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out_rows)
    print(f"wrote {OUT_CSV.name} ({len(out_rows)} rows, {len(fields)} cols)")

    # ----- concise CSV for non-coder reviewers -----------------------------
    # 8 columns: identity, headline classification, the right URL, and
    # whatever name CL has for that URL. correct_url = user_corrected_url
    # if a user override exists, else the verifier's matched URL based on
    # which phase the row landed in.
    def best_url(r: dict[str, Any]) -> str:
        if r.get("user_corrected_url"):
            return r["user_corrected_url"]
        if r["coverage"] == "found_via_lookup":
            return r.get("p4_matched_url", "")
        if r["coverage"] == "in_opinions":
            return r.get("p4c_stage_a_url", "")
        if r["coverage"] == "in_recap":
            return r.get("p4c_stage_b_url", "") or r.get("p4c_stage_a_url", "")
        return ""

    def best_cl_name(r: dict[str, Any]) -> str:
        # Manual override wins — when the user hand-verified a row, the
        # verifier's stage_*_match likely points at the wrong cluster.
        if r.get("cl_matched_name_override"):
            return r["cl_matched_name_override"]
        if r["coverage"] == "found_via_lookup":
            return r.get("p4_matched_name", "")
        if r["coverage"] == "in_opinions":
            return r.get("p4c_stage_a_match", "")
        if r["coverage"] == "in_recap":
            return r.get("p4c_stage_b_match", "") or r.get("p4c_stage_a_match", "")
        return ""

    concise_rows = [
        {
            "cited_tier": r["cited_tier"],
            "cited_case_name": r["cited_case_name"],
            "citation_string": r["citation_string"],
            "cited_year": r["cited_year"],
            "coverage": r["coverage"],
            "diagnosis": r["diagnosis"],
            "diagnosis_detail": r["diagnosis_detail"],
            "correct_url": best_url(r),
            "cl_matched_name": best_cl_name(r),
        }
        for r in out_rows
    ]
    concise_fields = list(concise_rows[0].keys())
    with OUT_CONCISE_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=concise_fields)
        w.writeheader()
        w.writerows(concise_rows)
    print(f"wrote {OUT_CONCISE_CSV.name} ({len(concise_rows)} rows, {len(concise_fields)} cols)")

    # ----- summary tables --------------------------------------------------
    from collections import Counter

    # Coverage distribution (headline)
    cov_counts = Counter(r["coverage"] for r in out_rows)
    print("\n=== coverage distribution ===")
    for c in ("found_via_lookup", "in_opinions", "in_recap",
              "not_found_anywhere", "excluded"):
        n = cov_counts.get(c, 0)
        print(f"  {c:<22} {n:>4}  ({100*n/len(out_rows):.1f}%)")

    # Diagnosis distribution (granular)
    diag_counts = Counter(r["diagnosis"] for r in out_rows)
    print("\n=== diagnosis distribution ===")
    for d, n in diag_counts.most_common():
        print(f"  {d:<34} {n:>4}  ({100*n/len(out_rows):.1f}%)")

    # Per-tier x coverage
    tiers = ("SCOTUS", "Circuit", "State_COLR", "State_IAC", "Federal_District")
    coverages = ("found_via_lookup", "in_opinions", "in_recap",
                 "not_found_anywhere", "excluded")
    by_tier_cov: dict[tuple[str, str], int] = Counter()
    for r in out_rows:
        by_tier_cov[(r["cited_tier"], r["coverage"])] += 1
    print("\n=== Per-tier x coverage ===")
    print(f"  {'tier':<16}  " + " ".join(f"{c[:18]:>18}" for c in coverages))
    for t in tiers:
        cells = [f"{by_tier_cov.get((t, c), 0):>18}" for c in coverages]
        print(f"  {t:<16}  " + " ".join(cells))

    # Headline coverage: numerator = any "in" bucket; denominator = all
    # measurable rows (excluded dropped).
    in_buckets = {"found_via_lookup", "in_opinions", "in_recap"}
    print("\n=== Corrected coverage (manual overrides + Phase 6 applied) ===")
    print(f"  {'tier':<16}  {'in_cl':>6} / {'denom':>5}  = {'pct':>6}   (excluded)")
    overall_in = overall_denom = overall_excluded = 0
    for t in tiers:
        in_n = sum(by_tier_cov.get((t, c), 0) for c in in_buckets)
        excluded_n = by_tier_cov.get((t, "excluded"), 0)
        denom = sum(by_tier_cov.get((t, c), 0) for c in coverages) - excluded_n
        pct = 100.0 * in_n / denom if denom else 0.0
        print(f"  {t:<16}  {in_n:>6} / {denom:>5}  = {pct:>5.1f}%   (excluded: {excluded_n})")
        overall_in += in_n
        overall_denom += denom
        overall_excluded += excluded_n
    overall_pct = 100.0 * overall_in / overall_denom if overall_denom else 0.0
    print(f"  {'OVERALL':<16}  {overall_in:>6} / {overall_denom:>5}  = {overall_pct:>5.1f}%   (excluded: {overall_excluded})")

    if corrections:
        print(f"\n=== Manual corrections applied: {len(corrections)} rows ===")
        for r in out_rows:
            if r["user_corrected_url"]:
                print(f"  {r['cited_case_name'][:35]:<35}  cite={r['citation_string'][:25]:<25}  "
                      f"-> {r['coverage']:<18}  {r['diagnosis']}")

    # RECAP sub-classification rollup
    in_recap_rows = [r for r in out_rows if r["coverage"] == "in_recap"]
    if in_recap_rows:
        sub_counts = Counter(r["recap_subreason"] or "(unclassified)" for r in in_recap_rows)
        print(f"\n=== RECAP sub-classification ({len(in_recap_rows)} in_recap rows) ===")
        for s, n in sub_counts.most_common():
            print(f"  {n:>2}  {s}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
