"""
Step 5 (audit): rigorously verify each fallback rescue.

The 2026-05-15 eyeball check of the rigorous fallback (step 4c) found
that POSSIBLE_MATCH rescues (score 0.40-0.85) include substantial
false positives: court and date match (because the search filtered
to that window) but party names are completely different. Examples:

  Hunter v. SF             -> Davis v. San Jose          (different parties)
  In re Loc. TV Advert.    -> Linet Americas v. Hill-Rom (different bankruptcy)
  Michael B. v. Berryhill  -> Hansen v. Berryhill        (same SSA defendant,
                                                          different plaintiff)
  Iglesias v. City of Hialeah -> DIPIETRO v. CITY OF HIALEAH (different plaintiff)

This script applies rigorous, non-eyeball tests per rescue:

  TEST 1 (citation match) — strong TRUE signal:
    Does the cited reporter citation appear in the matched CL
    cluster's `citations[]` field? Citations are unique identifiers;
    if the cite is in the cluster's citation list, it's the same case.
    Limitation: CL may have the OPINION indexed but not the West
    citation yet (ingestion lag for 2024-2025 opinions). So a NO here
    does not mean false; we proceed to test 2.

  TEST 2 (both-party presence) — secondary TRUE signal:
    Extract surname tokens from both sides of the cited "X v. Y" name
    (after the verifier's legal-abbreviation normalization). Check
    whether both tokens appear in the matched case_name. If both
    sides match, TRUE. If only one side matches (or zero), suspect
    false positive.

  TEST 3 (court + date) — tertiary corroboration:
    Court_id should match. Date_filed should be within +/- 2 years
    of cited year.

Verdict logic:
  - TEST 1 passes              -> VERIFIED_TRUE  (citation in cluster)
  - TEST 2 passes + TEST 3 ok  -> VERIFIED_TRUE  (party + court + date)
  - TEST 2 fails               -> LIKELY_FALSE
  - cited_case_name empty      -> CANT_AUDIT     (LLM dropped name)

For Stage B (RECAP) rescues, matched_url points to a docket, not a
cluster. We fetch the docket and apply the same name+court+date tests
(citation list isn't available on dockets).

Outputs:
  audit_per_row.csv       per-rescue verdict
  audit_summary.md        per-tier corrected coverage
"""
from __future__ import annotations

import asyncio
import csv
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv

ROOT = Path(__file__).resolve().parents[3]
load_dotenv(find_dotenv(usecwd=False), override=True)
sys.path.insert(0, str(ROOT / "src"))

from citation_verifier.client import AsyncCourtListenerClient  # noqa: E402
from citation_verifier.court_map import lookup_court_id  # noqa: E402
from citation_verifier.name_matcher import CaseNameMatcher  # noqa: E402


# lookup_court_id() recognizes canonical CL abbreviations ("SCOTUS",
# "7th Cir.", "D.D.C.") but not natural-language LLM-extracted forms
# like "U.S. Supreme Court" or "Cal. Supreme Court". Map the common
# extra forms here.
_HINT_TO_COURT_ID_EXTRA = {
    "u.s. supreme court": "scotus",
    "us supreme court": "scotus",
    "united states supreme court": "scotus",
    "supreme court of the united states": "scotus",
    "cal. supreme court": "cal",
    "california supreme court": "cal",
    "cal. ct. app.": "calctapp",
    "california court of appeal": "calctapp",
    "n.y. court of appeals": "ny",
    "ny court of appeals": "ny",
    "new york court of appeals": "ny",
    "fla. supreme court": "fla",
    "florida supreme court": "fla",
    "tex. supreme court": "tex",
    "texas supreme court": "tex",
    "tex. crim. app.": "texcrimapp",
    "texas court of criminal appeals": "texcrimapp",
    "ill. supreme court": "ill",
    "illinois supreme court": "ill",
    "mass. supreme court": "mass",
    "massachusetts supreme court": "mass",
    "fla. 3d dca": "fladistctapp",
    "fla. 2d dca": "fladistctapp",
    "fla. 1st dca": "fladistctapp",
    "fla. 4th dca": "fladistctapp",
    "fla. 5th dca": "fladistctapp",
    "fla. 6th dca": "fladistctapp",
}


def hint_to_court_id(hint: str) -> str:
    """Resolve a court_hint to a CL court_id, with extra fallbacks
    beyond what lookup_court_id() natively handles."""
    if not hint:
        return ""
    # Try the canonical lookup first
    cid = lookup_court_id(hint)
    if cid:
        return cid
    # Fall back to our extra natural-language map
    return _HINT_TO_COURT_ID_EXTRA.get(hint.lower().strip(), "")

HERE = Path(__file__).parent
RESCUE_CSV = HERE / "staged_fallback_rigorous_per_row.csv"
OUT_CSV = HERE / "audit_per_row.csv"
OUT_SUMMARY_MD = HERE / "audit_summary.md"

TARGET_TIERS = ("SCOTUS", "Circuit", "State_COLR", "State_IAC", "Federal_District")


# Common boilerplate to strip from party tokens before matching
_PARTY_STOPWORDS = {
    "the", "of", "and", "&", "a", "an", "v", "vs", "et", "al", "etc",
    "in", "re", "ex", "rel", "ex.", "rel.",
}

# Supplementary acronym expansions for federal agencies that the
# verifier's name_matcher.LEGAL_ABBREVIATIONS dict doesn't cover.
# These are intentionally narrow — only widely-recognized federal
# agency abbreviations where the cite-vs-match pair routinely differs.
_AGENCY_EXPANSIONS = {
    "irs": "internal revenue service",
    "fbi": "federal bureau of investigation",
    "sec": "securities and exchange commission",
    "ftc": "federal trade commission",
    "fcc": "federal communications commission",
    "fda": "food and drug administration",
    "epa": "environmental protection agency",
    "uscis": "us citizenship and immigration services",
    "ice": "immigration and customs enforcement",
    "tsa": "transportation security administration",
    "atf": "alcohol tobacco firearms",
    "dea": "drug enforcement administration",
    "dhs": "department of homeland security",
    "doj": "department of justice",
    "ssa": "social security administration",
    "va": "veterans affairs",
    "hhs": "health and human services",
    "hud": "housing and urban development",
    "dot": "department of transportation",
    "noaa": "national oceanic and atmospheric administration",
    "nasa": "national aeronautics and space administration",
    "nlrb": "national labor relations board",
    "eeoc": "equal employment opportunity commission",
    "cfpb": "consumer financial protection bureau",
}


def _expand_agency_acronyms(text: str) -> str:
    """Replace lone agency acronyms with their expansions."""
    out_words = []
    for w in text.split():
        wl = w.lower().rstrip(",.;:")
        if wl in _AGENCY_EXPANSIONS:
            out_words.append(_AGENCY_EXPANSIONS[wl])
        else:
            out_words.append(w)
    return " ".join(out_words)


def normalize_for_compare(s: str) -> str:
    """Lowercase + strip punctuation + collapse whitespace."""
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_parties(case_name: str) -> tuple[str, str]:
    """Split a 'X v. Y' name into (left, right) party text.

    Handles 'In re X' (returns ('', X) so we only require one party
    side to match) and 'Ex parte X' similarly.
    """
    n = (case_name or "").strip()
    if not n:
        return "", ""
    n_low = n.lower()
    if n_low.startswith("in re ") or n_low.startswith("in re,"):
        return "", n[6:].strip()
    if n_low.startswith("ex parte "):
        return "", n[9:].strip()
    # Split on ' v. ' / ' v ' / ' vs. ' / ' vs '
    parts = re.split(r"\s+v\.?\s+|\s+vs\.?\s+", n, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return n, ""


def party_tokens(party: str) -> set[str]:
    """Extract meaningful tokens from a party-side string."""
    n = normalize_for_compare(party)
    # Expand federal agency acronyms BEFORE the verifier's normalization
    # (which only knows about corporate / state abbreviations).
    n = _expand_agency_acronyms(n)
    matcher = CaseNameMatcher()
    n = matcher._normalize(n)
    toks = set(t for t in n.split() if t and t not in _PARTY_STOPWORDS and len(t) > 1)
    return toks


def parties_present(cited_name: str, matched_name: str) -> tuple[bool, bool]:
    """Return (left_present, right_present) — whether each cited party
    is present in the matched name. After legal-abbreviation
    normalization on both sides.

    For 'In re' or 'Ex parte' cases, left is empty; we only check right.
    """
    cited_left, cited_right = extract_parties(cited_name)
    matched_normalized = _expand_agency_acronyms(normalize_for_compare(matched_name))
    matched_normalized = CaseNameMatcher()._normalize(matched_normalized)
    matched_tokens = set(matched_normalized.split())

    def side_present(side: str) -> bool:
        if not side.strip():
            return True  # nothing to check
        toks = party_tokens(side)
        if not toks:
            return False
        # At least one meaningful token from this side must appear
        # in the matched name's tokens
        return bool(toks & matched_tokens)

    return side_present(cited_left), side_present(cited_right)


def citation_in_cluster(cited_cite: str, cluster: dict[str, Any]) -> bool:
    """Check whether the cited reporter cite appears in cluster.citations[]."""
    cluster_cites = cluster.get("citations", []) or []
    norm_cited = normalize_for_compare(cited_cite)
    # Normalize each cluster citation to "VOL REPORTER PAGE" form
    for c in cluster_cites:
        if isinstance(c, str):
            if normalize_for_compare(c) in norm_cited or norm_cited in normalize_for_compare(c):
                return True
        elif isinstance(c, dict):
            vol = c.get("volume", "")
            rep = c.get("reporter", "")
            pg = c.get("page", "")
            if vol and rep and pg:
                composed = f"{vol} {rep} {pg}"
                nc = normalize_for_compare(composed)
                if nc and (nc in norm_cited or norm_cited in nc):
                    return True
    return False


def years_close(matched_date: str, cited_year: str) -> bool:
    """+/- 2 years tolerance."""
    if not matched_date or not cited_year:
        return False
    m_year = matched_date[:4]
    try:
        return abs(int(m_year) - int(cited_year)) <= 2
    except ValueError:
        return False


async def fetch_cluster(client: AsyncCourtListenerClient, url: str) -> dict[str, Any] | None:
    """Fetch a cluster (or docket) JSON from CL by URL.

    For URLs like /opinion/<id>/.../, fetches /clusters/<id>/.
    For URLs like /docket/<id>/, fetches /dockets/<id>/.
    """
    if not url:
        return None
    cluster_m = re.search(r"/opinion/(\d+)/", url)
    docket_m = re.search(r"/docket/(\d+)/", url)
    try:
        if cluster_m:
            cid = cluster_m.group(1)
            data = await client._request_with_retry(
                "GET", f"{client.BASE_URL}/clusters/{cid}/", timeout=30
            )
            data["_source"] = "cluster"
            return data
        if docket_m:
            did = docket_m.group(1)
            data = await client._request_with_retry(
                "GET", f"{client.BASE_URL}/dockets/{did}/", timeout=30
            )
            data["_source"] = "docket"
            return data
    except Exception as e:
        return {"_fetch_error": str(e)[:120]}
    return None


def audit_one(
    row: dict[str, str],
    matched_meta: dict[str, Any] | None,
    matched_name_from_step4: str = "",
    matched_court_from_step4: str = "",
    matched_date_from_step4: str = "",
    cited_court_id: str = "",
) -> dict[str, Any]:
    """Apply tests, return a verdict + reasoning.

    Use the matched_name from step 4 (CL search result caseName field
    which is reliably populated) rather than re-fetching the cluster's
    case_name — cluster JSON sometimes has case_name="" for newer or
    partially-ingested opinions, which would fail the party test
    spuriously.
    """
    cited_name = row.get("cited_case_name") or ""
    cited_cite = row.get("citation_string") or ""
    cited_year = row.get("year") or ""

    if not cited_name.strip():
        return {
            "verdict": "CANT_AUDIT",
            "reason": "no cited_case_name (LLM dropped it)",
            "test_cite_match": "",
            "test_parties": "",
            "test_court_id": "",
            "test_date": "",
        }

    # Use the step-4 matched_name as authoritative (caseName from search
    # results), with fall-through to fetched cluster case_name if missing.
    matched_name = matched_name_from_step4 or ""
    if matched_meta and not matched_meta.get("_fetch_error"):
        matched_name = matched_name or (
            matched_meta.get("case_name")
            or matched_meta.get("case_name_full")
            or matched_meta.get("caseName")
            or ""
        )
        matched_date = matched_meta.get("date_filed", "") or matched_date_from_step4
    else:
        matched_date = matched_date_from_step4

    matched_court = matched_court_from_step4

    if not matched_name:
        return {
            "verdict": "CANT_AUDIT",
            "reason": "no matched_name in either step-4 record or fetched cluster",
            "test_cite_match": "",
            "test_parties": "",
            "test_court_id": "",
            "test_date": "",
        }

    source = (matched_meta or {}).get("_source", "")

    # TEST 1: citation match (clusters only; dockets don't carry cite list)
    cite_test = ""
    if source == "cluster" and matched_meta:
        cite_test = "yes" if citation_in_cluster(cited_cite, matched_meta) else "no"

    # TEST 2: party presence (both sides)
    left_ok, right_ok = parties_present(cited_name, matched_name)
    in_re_style = cited_name.strip().lower().startswith(("in re", "ex parte"))
    if not in_re_style:
        parties_test = f"{('L' if left_ok else 'l')}{('R' if right_ok else 'r')}"
        parties_pass = left_ok and right_ok
    else:
        parties_test = f"-{'R' if right_ok else 'r'}"
        parties_pass = right_ok

    # TEST 3a: court_id (the cited court_hint should match the matched cluster's
    # court_id — different courts = different opinions even if dispute is the same)
    if cited_court_id and matched_court:
        court_id_test = "yes" if cited_court_id == matched_court else "no"
    else:
        court_id_test = "?"  # missing data; treat as neutral

    # TEST 3b: date proximity (+/- 2 years)
    if cited_year:
        date_test = "yes" if years_close(matched_date, cited_year) else "no"
    else:
        date_test = "?"  # cited year missing; date corroboration unavailable

    # Verdict
    if cite_test == "yes":
        verdict = "VERIFIED_TRUE"
        reason = "citation present in matched cluster's citation list"
    elif source == "cluster" and cite_test == "no" and (matched_meta or {}).get("citations"):
        # Cluster has a populated citations[] list and the cited cite isn't in it.
        # Strong signal that this is a different case, even if party names happen
        # to overlap. Catches the Wilson/Wilmington/Rose Way/Thurman false-positive
        # pattern surfaced by the 2026-05-15 eyeball pass (task #1).
        verdict = "LIKELY_FALSE"
        cluster_cites = "; ".join(
            f"{c.get('volume','')} {c.get('reporter','')} {c.get('page','')}"
            for c in (matched_meta.get("citations", []) or [])
            if isinstance(c, dict)
        )[:200]
        reason = f"cluster's citations[] contradict cited cite: [{cluster_cites}]"
    elif court_id_test == "no":
        # Strong signal of wrong opinion even if names overlap. Catches
        # Coinbase (SCOTUS) -> Bielski (cand district phase) case.
        verdict = "LIKELY_FALSE"
        reason = f"court_id mismatch: cited={cited_court_id} matched={matched_court}"
    elif parties_pass and (date_test == "yes" or date_test == "?"):
        verdict = "VERIFIED_TRUE"
        reason = "both parties present + court corroborates (date " + (
            "ok" if date_test == "yes" else "unknown — cited year missing"
        ) + ")"
    elif parties_pass and date_test == "no":
        verdict = "AMBIGUOUS"
        reason = "parties match but date doesn't corroborate"
    elif left_ok or right_ok:
        verdict = "LIKELY_FALSE"
        reason = "only one party matches (other party absent)"
    else:
        verdict = "LIKELY_FALSE"
        reason = "no party from cited name found in matched name"

    return {
        "verdict": verdict,
        "reason": reason,
        "test_cite_match": cite_test,
        "test_parties": parties_test,
        "test_court_id": court_id_test,
        "test_date": date_test,
        "matched_cluster_citations": "; ".join(
            f"{c.get('volume','')} {c.get('reporter','')} {c.get('page','')}"
            for c in ((matched_meta or {}).get("citations", []) or [])
            if isinstance(c, dict)
        )[:200] if source == "cluster" else "",
    }


async def main() -> int:
    rows = list(csv.DictReader(RESCUE_CSV.open(encoding="utf-8")))
    rescues = [r for r in rows if r["final_outcome"] in (
        "rescued_by_opinion_search", "rescued_by_recap"
    )]
    print(f"Loaded {len(rescues)} rescues to audit")

    audited: list[dict[str, Any]] = []
    t0 = time.monotonic()
    async with AsyncCourtListenerClient() as client:
        for i, r in enumerate(rescues, 1):
            if r["final_outcome"] == "rescued_by_opinion_search":
                url = r["stage_a_url"]
                score = r["stage_a_score"]
                status = r["stage_a_status"]
            else:
                url = r["stage_b_url"]
                score = r["stage_b_score"]
                status = r["stage_b_status"]

            meta = await fetch_cluster(client, url)
            # Pull step-4's matched fields (more reliable than re-fetched cluster name)
            if r["final_outcome"] == "rescued_by_opinion_search":
                step4_name = r.get("stage_a_matched_name", "")
                step4_court = r.get("stage_a_matched_court", "")
                step4_date = r.get("stage_a_matched_date", "")
            else:
                step4_name = r.get("stage_b_matched_name", "")
                step4_court = r.get("stage_b_matched_court", "")
                step4_date = r.get("stage_b_matched_date", "")
            cited_court_id = hint_to_court_id(r.get("court_hint", "")) or ""
            audit = audit_one(
                r, meta,
                matched_name_from_step4=step4_name,
                matched_court_from_step4=step4_court,
                matched_date_from_step4=step4_date,
                cited_court_id=cited_court_id,
            )

            audited.append({
                **r,
                "audit_url": url,
                "audit_score": score,
                "audit_status": status,
                **audit,
            })

            display_match = step4_name or (meta or {}).get('case_name') or (meta or {}).get('caseName') or '?'
            print(f"  [{i:>2}/{len(rescues)}] {audit['verdict']:<14} "
                  f"cite={audit['test_cite_match']:>3}  pp={audit['test_parties']:<2}  "
                  f"cid={audit['test_court_id']:<3}  "
                  f"dt={audit['test_date']:<3}  "
                  f"{r['cited_tier']:<16}  "
                  f"\"{(r['cited_case_name'] or '')[:30]:<30}\" -> \"{display_match[:40]:<40}\"")

    elapsed = time.monotonic() - t0
    print(f"\nDone in {elapsed:.1f}s ({elapsed/60:.1f} min)")

    fields = list(audited[0].keys())
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(audited)
    print(f"wrote {OUT_CSV.name}")

    # Verdict tallies
    by_tier_verdict: dict[tuple[str, str], int] = Counter()
    by_stage_verdict: dict[tuple[str, str], int] = Counter()
    for r in audited:
        by_tier_verdict[(r["cited_tier"], r["verdict"])] += 1
        stage = "A" if r["final_outcome"] == "rescued_by_opinion_search" else "B"
        by_stage_verdict[(stage, r["verdict"])] += 1

    print("\n=== AUDIT VERDICTS ===")
    print(f"  {'tier':<16}  {'TRUE':>5} {'FALSE':>6} {'AMBIG':>6} {'CANT':>5}")
    for t in TARGET_TIERS:
        v_true = by_tier_verdict.get((t, "VERIFIED_TRUE"), 0)
        v_false = by_tier_verdict.get((t, "LIKELY_FALSE"), 0)
        v_amb = by_tier_verdict.get((t, "AMBIGUOUS"), 0)
        v_cant = by_tier_verdict.get((t, "CANT_AUDIT"), 0)
        print(f"  {t:<16}  {v_true:>5} {v_false:>6} {v_amb:>6} {v_cant:>5}")
    total_true = sum(1 for r in audited if r["verdict"] == "VERIFIED_TRUE")
    total_false = sum(1 for r in audited if r["verdict"] == "LIKELY_FALSE")
    total_amb = sum(1 for r in audited if r["verdict"] == "AMBIGUOUS")
    total_cant = sum(1 for r in audited if r["verdict"] == "CANT_AUDIT")
    print(f"  {'TOTAL':<16}  {total_true:>5} {total_false:>6} {total_amb:>6} {total_cant:>5}")

    # Corrected per-tier coverage. Need original tier totals.
    # citation_lookup_in_cl per tier = (50 - NOT_FOUND_count_after_step_4)
    # Adjusted coverage = baseline + audited_TRUE
    print("\n=== CORRECTED COVERAGE (audit-adjusted) ===")
    # Reload step 4 coverage to get baselines
    baseline_csv = HERE / "coverage_per_tier.csv"
    baselines: dict[str, int] = {}
    if baseline_csv.exists():
        for r in csv.DictReader(baseline_csv.open(encoding="utf-8")):
            baselines[r["tier"]] = int(r["in_cl"])

    print(f"  {'tier':<16}  n={'50':>3} {'cite_lookup':>12}  {'+true':>6}  {'corrected':>12}")
    corrected_rows = []
    for t in TARGET_TIERS:
        n = 50
        base = baselines.get(t, 0)
        true_for_tier = by_tier_verdict.get((t, "VERIFIED_TRUE"), 0)
        corrected = base + true_for_tier
        corrected_pct = 100 * corrected / n if n else 0
        print(f"  {t:<16}  n={n:>3} {base:>12} {true_for_tier:>+6}  {corrected}/{n} = {corrected_pct:.1f}%")
        corrected_rows.append({
            "tier": t,
            "n": n,
            "citation_lookup_baseline": base,
            "audited_true_rescues": true_for_tier,
            "audited_false_rescues": by_tier_verdict.get((t, "LIKELY_FALSE"), 0),
            "audited_ambiguous": by_tier_verdict.get((t, "AMBIGUOUS"), 0),
            "audited_cant_audit": by_tier_verdict.get((t, "CANT_AUDIT"), 0),
            "corrected_in_cl": corrected,
            "corrected_pct": f"{corrected_pct:.1f}",
        })

    total_n = sum(r["n"] for r in corrected_rows)
    total_base = sum(r["citation_lookup_baseline"] for r in corrected_rows)
    total_corrected = sum(r["corrected_in_cl"] for r in corrected_rows)
    print(f"  {'OVERALL':<16}  n={total_n:>3} {total_base:>12} {total_true:>+6}  "
          f"{total_corrected}/{total_n} = {100*total_corrected/total_n:.1f}%")

    # MD summary
    lines = [
        "# Step 5 — audit of fallback rescues",
        "",
        f"Audited {len(rescues)} rescues from the rigorous staged fallback ",
        "(step 4c) to distinguish true matches from false positives. Each ",
        "rescue had three tests applied:",
        "",
        "1. **citation_in_cluster** — strong TRUE: cited reporter cite appears ",
        "   in the matched cluster's `citations[]` field.",
        "2. **parties_present** — secondary: both sides of cited 'X v. Y' name ",
        "   appear (any token, after legal-abbrev normalization) in matched name.",
        "3. **court_date** — corroboration: matched date within +/- 2 years.",
        "",
        "Verdict logic:",
        "- citation match -> VERIFIED_TRUE",
        "- parties match + court/date corroborates -> VERIFIED_TRUE",
        "- only one party matches -> LIKELY_FALSE",
        "- no parties match -> LIKELY_FALSE",
        "- parties match but court/date doesn't -> AMBIGUOUS",
        "- cited_case_name missing -> CANT_AUDIT (LLM dropped name)",
        "",
        "## Audit verdicts",
        "",
        "| tier | VERIFIED_TRUE | LIKELY_FALSE | AMBIGUOUS | CANT_AUDIT |",
        "|---|---|---|---|---|",
    ]
    for t in TARGET_TIERS:
        v_true = by_tier_verdict.get((t, "VERIFIED_TRUE"), 0)
        v_false = by_tier_verdict.get((t, "LIKELY_FALSE"), 0)
        v_amb = by_tier_verdict.get((t, "AMBIGUOUS"), 0)
        v_cant = by_tier_verdict.get((t, "CANT_AUDIT"), 0)
        lines.append(f"| {t} | {v_true} | {v_false} | {v_amb} | {v_cant} |")

    lines += [
        "",
        "## Corrected per-tier coverage",
        "",
        "Each tier's coverage = (citation_lookup baseline + audit-VERIFIED_TRUE rescues) / 50.",
        "AMBIGUOUS rows are excluded from numerator (conservative).",
        "",
        "| tier | n | cite_lookup | +TRUE rescues | corrected | pct |",
        "|---|---|---|---|---|---|",
    ]
    for r in corrected_rows:
        lines.append(
            f"| {r['tier']} | {r['n']} | {r['citation_lookup_baseline']} | "
            f"+{r['audited_true_rescues']} | {r['corrected_in_cl']} | {r['corrected_pct']}% |"
        )

    lines += [
        "",
        f"## Overall",
        f"",
        f"- citation_lookup baseline: {total_base}/{total_n} = {100*total_base/total_n:.1f}%",
        f"- + audited TRUE rescues:   {total_corrected}/{total_n} = {100*total_corrected/total_n:.1f}%",
        f"- audited false positives:  {total_false} (would have inflated coverage by {100*total_false/total_n:.1f} pp)",
        f"- ambiguous (excluded from numerator): {total_amb}",
        f"- can't audit (LLM dropped name): {total_cant}",
        "",
    ]
    OUT_SUMMARY_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT_SUMMARY_MD.name}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
