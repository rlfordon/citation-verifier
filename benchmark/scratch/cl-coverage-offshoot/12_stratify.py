"""
Step 3 of the real run: pre-filter, dedup, tier-classify, K=5 cap,
stratified 50/tier sample.

Pipeline per REAL_RUN_DESIGN.md step 3:
  1. Load all real_extractions/*.json (uses citations_valid only —
     hallucinations are dropped here, they're a quality signal but not
     usable for coverage measurement).
  2. Pre-filter short-form cites (Id., supra, bare pin cites) per
     settled decision #4 — verifier can't resolve these and they
     pollute the miss rate.
  3. Pre-filter foreign / non-US reports per settled decision #5 —
     Eng. Rep., K.B., WLR, etc. Out of scope for CL coverage.
  4. Dedup at (citing_cluster, citation_string, parenthetical) per
     settled decision (pilot pattern).
  5. Tier-classify each cited citation by reporter pattern via
     tier_from_cite() (lifted from 07_stratify.py).
  6. Apply K=5 cap per (citing_cluster, cited_tier).
  7. Stratified-sample 50 per cited tier across SCOTUS, Circuit,
     State_COLR, State_IAC.

Outputs:
  final_pool.csv         — post-prefilter+dedup+cap pool (all tiers)
  final_200.csv          — the stratified sample (target 50/tier x 4)
  stratify_summary.md    — narrative with distribution + yield math

Caveats inherited from the pilot:
  - Regional reporters (A.3d, P.3d, N.E.3d, etc.) carry both COLR and
    IAC opinions but default to State_COLR. Actual split won't be known
    until step 4's CL lookup.
  - tier_from_cite is reporter-pattern based, not authoritative. CL
    lookup in step 4 is the ground truth.
"""
from __future__ import annotations

import csv
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
EXTRACT_DIR = HERE / "real_extractions"
MANIFEST = HERE / "citing_opinions" / "_manifest.csv"

FINAL_POOL_CSV = HERE / "final_pool.csv"
# File name kept for backward compatibility with downstream scripts;
# actual sample size is now 250 (5 target tiers x 50 per tier) after
# Federal_District added 2026-05-15.
FINAL_200_CSV = HERE / "final_200.csv"
SUMMARY_MD = HERE / "stratify_summary_real.md"

# ---- tier_from_cite (inlined from the deleted pilot 07_stratify.py) ---------
# Coarse tier inference from the citation string. Used as a fallback when
# `court_hint` from the LLM extraction doesn't resolve cleanly. The combined
# logic (court_hint first, reporter-pattern fallback) lives in
# tier_combined() further down.

# State IAC reporters — most specific first. Match before COLR patterns.
_STATE_IAC_PATTERNS = [
    r"\bCal\.\s*(?:Rptr\.|App\.)\s*(?:2d|3d|4th|5th|6th)?",  # Cal. App. 5th, Cal. Rptr.
    r"\bCal\.\s*App\.\b",
    r"\bA\.D\.\s*(?:2d|3d|4th)?",                              # NY appellate division
    r"\bN\.Y\.S\.\s*(?:2d|3d)?",                              # NY Supplement
    r"\bIll\.\s*App\.\s*(?:2d|3d)?",
    r"\bMass\.\s*App\.\s*Ct\.",
    r"\bTex\.\s*App\.",
    r"\bOhio\s*App\.\s*(?:2d|3d)?",
    r"\bMich\.\s*App\.",
    r"\bN\.J\.\s*Super\.",
    r"\bPa\.\s*Super\.",
    r"\bWash\.\s*App\.",
    r"\bMo\.\s*App\.",
    r"\bMd\.\s*App\.",
    r"\bN\.C\.\s*App\.",
    r"\bGa\.\s*App\.",
    r"\bConn\.\s*App\.",
    r"\bTenn\.\s*(?:Crim\.\s*)?App\.",
    r"\bAla\.\s*Crim\.\s*App\.",
    r"\bAla\.\s*Civ\.\s*App\.",
    r"\bKy\.\s*App\.",
    r"\bMinn\.\s*App\.",
    r"\bOhio\s*App\.",
    r"\bColo\.\s*App\.",
    r"\bAriz\.\s*App\.",
    r"\bN\.M\.\s*Ct\.\s*App\.",
    r"\bN\.M\.\s*App\.",
    r"\bWis\.\s*App\.",
    r"\bFla\.\s*App\.",
    r"\bFla\.\s*Dist\.\s*Ct\.\s*App\.",
]
_STATE_IAC_PAT_RE = re.compile("|".join(_STATE_IAC_PATTERNS))

# State COLR — state-specific high-court reporters. Trailing `\b` doesn't
# work after `\.`, so rely on negative lookahead.
_STATE_COLR_PATTERNS = [
    r"\bCal\.\s*(?:2d|3d|4th|5th)\b",
    r"\bCal\.(?!\s*(?:App|Rptr))",                            # bare Cal. = old CA Supreme
    r"\bN\.Y\.\s*(?:2d|3d)(?!\s*S)",                          # N.Y.3d
    r"\bN\.Y\.(?=\s+\d)",                                      # bare N.Y. followed by digit
    r"\bMass\.(?!\s*App)",
    r"\bPa\.(?!\s*Super)",
    r"\bMd\.(?!\s*App)",
    r"\bVa\.(?!\s*App)",
    r"\bOhio\s*St\.\s*(?:2d|3d)?",
    r"\bMich\.(?!\s*App)",
    r"\bIll\.\s*(?:2d|3d)?(?!\s*App)",
    r"\bTex\.(?!\s*App)",
    r"\bWash\.\s*(?:2d)?(?!\s*App)",
    r"\bWis\.\s*(?:2d|3d)?(?!\s*App)",
    r"\bOr\.(?!\s*App)",
    r"\bN\.C\.(?!\s*App)",
    r"\bN\.J\.(?!\s*Super)",
    r"\bConn\.(?!\s*App)",
    r"\bGa\.(?!\s*App)",
    r"\bIowa\b",
    r"\bColo\.(?!\s*App)",
    r"\bAriz\.(?!\s*App)",
    r"\bMinn\.(?!\s*App)",
    r"\bMo\.(?!\s*App)",
    r"\bKy\.(?!\s*App)",
    r"\bAla\.(?!\s*(?:Crim|Civ)\s*App)",
    r"\bN\.M\.(?!\s*(?:Ct|App))",
    r"\bFla\.(?!\s*App|\s*Dist)",
]
_STATE_COLR_PAT_RE = re.compile("|".join(_STATE_COLR_PATTERNS))

# Regional reporters — ambiguous (mostly COLR, sometimes IAC). Default to COLR.
_REGIONAL_RE = re.compile(r"\b(?:A|P|N\.E|N\.W|S\.E|S\.W|So)\.\s*(?:2d|3d)?\b")

# Federal
_SCOTUS_REPORTER_RE = re.compile(r"\bU\.?\s?S\.?\b|S\.\s*Ct\.|L\.\s*Ed\.")
_CIRCUIT_REPORTER_RE = re.compile(r"\bF\.?\s?(?:2d|3d|4th)\b|F\.\s*App'x\b")
_FED_DISTRICT_REPORTER_RE = re.compile(r"\bF\.\s*Supp\.\s*(?:2d|3d)?\b")


def tier_from_cite(cite: str) -> str:
    """Coarse tier inference from the citation string.

    Returns one of: SCOTUS, Circuit, State_COLR, State_IAC, Federal_District,
    Other. Order matters: most specific first.
    """
    c = cite or ""
    if _SCOTUS_REPORTER_RE.search(c):
        return "SCOTUS"
    if _CIRCUIT_REPORTER_RE.search(c):
        return "Circuit"
    if _FED_DISTRICT_REPORTER_RE.search(c):
        return "Federal_District"
    if _STATE_IAC_PAT_RE.search(c):
        return "State_IAC"
    if _STATE_COLR_PAT_RE.search(c):
        return "State_COLR"
    if _REGIONAL_RE.search(c):
        return "State_COLR"  # ambiguous regional reporters default to COLR
    return "Other"


# ---- court_hint classifier --------------------------------------------------
# The LLM extracts `court_hint` from the Bluebook date parenthetical
# (e.g. `(Cal. Ct. App. 2010)` → "Cal. Ct. App."). This is more
# authoritative than the reporter pattern for ambiguous regional
# reporters: a "245 P.3d 100" citation could be COLR or IAC, but
# "Cal. Ct. App." in the parenthetical resolves it.
#
# Critical edge cases:
#   - "N.Y. Ct. App." = New York Court of Appeals = NY's HIGHEST court
#     (State_COLR), not IAC. NY's intermediate court is "App. Div." /
#     "<Nth> Dept." All other "Ct. App." patterns = State_IAC.
#   - "Tex. Crim. App." = Texas Court of Criminal Appeals, the
#     criminal-side highest court in Texas's bifurcated system
#     (State_COLR).
#   - "D.C." alone is ambiguous (DC Court of Appeals = DC's highest
#     court). "D.C. Cir." is the federal circuit. Defer to D.C. Cir.
#     pattern for Circuit.

_SCOTUS_HINT_RE = re.compile(r"\bU\.?S\.?\s*Supreme|SCOTUS|S\.\s*Ct\.|\bSupreme\s+Court\s+of\s+the\s+United\s+States",
                              re.IGNORECASE)
_CIRCUIT_HINT_RE = re.compile(r"\b(?:\d+(?:st|nd|rd|th)\s*Cir\.?|D\.?C\.?\s*Cir\.?|Fed\.?\s*Cir\.?|Cir\.)\b",
                               re.IGNORECASE)
# Federal district: D.D.C. / N.D. Cal. / S.D.N.Y. / E.D. Tex. etc.
# Match either with explicit "D." or compass+state form.
_FED_DIST_HINT_RE = re.compile(
    r"\b(?:N|S|E|W|C|M)\.D\.\s*(?:N\.?Y\.?|Cal\.?|Tex\.?|Ill\.?|Fla\.?|Ohio|Pa\.?|Mass\.?|Mich\.?|Wis\.?|Va\.?|Mo\.?|"
    r"Ind\.?|Iowa|Tenn\.?|N\.?C\.?|S\.?C\.?|Ga\.?|Md\.?|Ariz\.?|Colo\.?|N\.?M\.?|Nev\.?|Utah|Or\.?|Wash\.?|Alaska|Haw\.?|"
    r"La\.?|Miss\.?|Ala\.?|Ark\.?|Okla\.?|Kan\.?|Neb\.?|S\.?D\.?|N\.?D\.?|Wyo\.?|Mont\.?|Idaho|Conn\.?|Vt\.?|N\.?H\.?|"
    r"Me\.?|R\.?I\.?|Del\.?|N\.?J\.?|Pa\.?|Ky\.?|W\.?Va\.?|Minn\.?)\b"
    r"|\bD\.\s*(?:D\.?C\.?|Mass\.?|Conn\.?|Md\.?|Me\.?|Vt\.?|N\.?H\.?|R\.?I\.?|N\.?J\.?|Del\.?|Kan\.?|Neb\.?|"
    r"S\.?D\.?|N\.?D\.?|Wyo\.?|Mont\.?|Idaho|Or\.?|Alaska|Haw\.?|Ariz\.?|Colo\.?|Utah|Nev\.?|N\.?M\.?|Minn\.?)\b",
    re.IGNORECASE
)
# State COLR: "<state> Supreme Court" / "Sup. Ct." / NY's "Ct. App." /
# Texas Crim. App. / Mass. SJC
_STATE_COLR_HINT_RE = re.compile(
    r"\b(?:Supreme\s+Court|Sup\.?\s*Ct\.?|SJC)\b"  # X Supreme Court / X Sup. Ct.
    r"|\bN\.?Y\.?\s*Ct\.\s*App\.?\b"               # NY Court of Appeals (highest)
    # "NY Court of Appeals" without periods (LLM emits this form sometimes)
    r"|\bN\.?Y\.?\s+Court\s+of\s+Appeals\b"
    r"|\b(?:Tex\.?|Okla\.?|Tenn\.?)\s*Crim\.?\s*App\.?\b",  # Bifurcated crim courts
    re.IGNORECASE
)
# State IAC: "<state> Ct. App." (default), "App. Div.", "<N>th Dept.",
# "<N>th Dist." (as appellate district, e.g. Illinois 1st-5th Districts).
# NY uses "2d"/"3d" suffix instead of "2nd"/"3rd" — include `d` in the
# alternation so "3d Dept." matches.
_STATE_IAC_HINT_RE = re.compile(
    r"\bCt\.?\s*App\.?\b"        # Cal. Ct. App., Ill. Ct. App., etc.
    r"|\bApp\.?\s*Div\.?\b"      # NY Appellate Division
    r"|\bApp\.?\s*Ct\.?\b"       # Mass. App. Ct., Ill. App. Ct.
    r"|\b\d+(?:st|nd|rd|th|d)\s+Dept\.?\b"   # NY Appellate Division departments (3d/4th)
    r"|\b\d+(?:st|nd|rd|th|d)\s+Dist\.?\b"   # Illinois Appellate Court districts
    # "Ill. App. (Nd)" — Illinois public-domain format hint
    r"|\bIll\.\s*App\.\s*\(\d+(?:st|nd|rd|th|d)\)",
    re.IGNORECASE
)


def tier_from_court_hint(hint: str) -> str | None:
    """Classify by court_hint (parenthetical court abbreviation).

    Returns one of SCOTUS, Circuit, State_COLR, State_IAC,
    Federal_District — or None if the hint doesn't resolve cleanly
    (caller should fall back to reporter-based classification).

    Order matters: SCOTUS / Circuit / Fed_District must come first
    because state-court patterns ("Ct. App.") would otherwise eat
    things like "Cir." in unrelated contexts.
    """
    h = (hint or "").strip()
    if not h:
        return None

    if _SCOTUS_HINT_RE.search(h):
        return "SCOTUS"
    if _CIRCUIT_HINT_RE.search(h):
        return "Circuit"
    if _FED_DIST_HINT_RE.search(h):
        return "Federal_District"
    # State_COLR first because "N.Y. Ct. App." would otherwise match
    # the State_IAC `Ct. App.` pattern. Same for Tex. Crim. App.
    if _STATE_COLR_HINT_RE.search(h):
        return "State_COLR"
    if _STATE_IAC_HINT_RE.search(h):
        return "State_IAC"
    return None


# Reporter fallback augmentations for forms the pilot's tier_from_cite
# misses. NY citations sometimes drop the periods ("10 NY3d 706"); the
# Illinois public-domain neutral format is "2011 IL App (2d) 091123".
# Order: we run these BEFORE tier_from_cite so they win.
# Volume-first patterns also catch pin cites like "28 NY3d at 229"
# where my earlier "\bNY3d\s+\d" required a digit right after the
# reporter (and missed "at" pin-cite forms).
_NY_NOPERIODS_COLR_RE = re.compile(r"\b\d+\s+NY\s*(?:2d|3d)\b", re.IGNORECASE)
_NY_NOPERIODS_IAC_RE = re.compile(r"\b\d+\s+AD\s*(?:2d|3d|4th)?\b", re.IGNORECASE)
_IL_PUBLIC_DOMAIN_RE = re.compile(r"\bIL\s+App\b", re.IGNORECASE)
# F.R.D. = Federal Rules Decisions — district-court reporter; fits the
# Federal_District tier alongside F. Supp.
_FED_DIST_REPORTER_RE = re.compile(r"\bF\.?\s*R\.?\s*D\.?\b", re.IGNORECASE)


def tier_combined(citation_string: str, court_hint: str) -> str:
    """Use court_hint if it resolves; else fall back to reporter.

    The court_hint signal is methodologically clean — it's the
    parenthetical the citing court itself wrote per Bluebook 10.4,
    extracted by the LLM. No CL data involved, so no measurement
    bias.
    """
    by_hint = tier_from_court_hint(court_hint)
    if by_hint:
        return by_hint

    # NY / IL forms missed by the pilot tier_from_cite regexes
    c = citation_string or ""
    if _NY_NOPERIODS_IAC_RE.search(c):
        return "State_IAC"
    if _NY_NOPERIODS_COLR_RE.search(c):
        return "State_COLR"
    if _IL_PUBLIC_DOMAIN_RE.search(c):
        return "State_IAC"  # IL App = appellate; IL alone (no "App") = Sup
    if _FED_DIST_REPORTER_RE.search(c):
        return "Federal_District"

    return tier_from_cite(citation_string)

SAMPLE_PER_TIER = 50
K_PER_OPINION_PER_TIER = 5
SEED = 20260515  # date of this real run

# Federal_District added 2026-05-15 (user observation: federal trial
# courts are heavily cited — 152 in pool — and were missing from the
# design's 4 target tiers). Listed LAST so adding it doesn't perturb
# the prior 4 tiers' deterministic shuffle order.
TARGET_TIERS = ("SCOTUS", "Circuit", "State_COLR", "State_IAC", "Federal_District")


# ---- pre-filters -----------------------------------------------------------

# Short-form / non-resolvable citation patterns (settled decision #4).
# These match if the ENTIRE citation_string is one of these forms — bare pin
# cites with no reporter context. Don't blanket-match "at N" anywhere because
# many citations end with ", at 45" as a pin cite within a full reporter cite.
_SHORTFORM_RES = [
    # Id., at N / Id at p. N / Id. ¶ N / Id. at p. 450 (Cal style)
    re.compile(r"^\s*[Ii]d\.?,?\s*(?:at\s+(?:p\.?\s*)?|\xb6\s*|¶\s*)\d", re.IGNORECASE),
    re.compile(r"^\s*[Ii]d\.?$", re.IGNORECASE),
    re.compile(r"^\s*at\s+\d+(?:[-–]\d+)?\s*$", re.IGNORECASE),
    re.compile(r"\bsupra\b", re.IGNORECASE),
    re.compile(r"^\s*[Ii]bid\.?\s*$", re.IGNORECASE),
]

# Foreign / non-US reporter patterns (settled decision #5)
_FOREIGN_RES = [
    re.compile(r"\bEng\.?\s*Rep\.?\b", re.IGNORECASE),
    re.compile(r"\bMees\.?\s*&\s*W\.?\b", re.IGNORECASE),
    re.compile(r"\bK\.?B\.?\b"),    # King's Bench
    re.compile(r"\bQ\.?B\.?\b"),    # Queen's Bench
    re.compile(r"\bCh\.?\s*D\.?\b"),  # Chancery Division
    re.compile(r"\bW\.?L\.?R\.?\b"),  # Weekly Law Reports
    re.compile(r"\bA\.?C\.?\s+\d"),   # Appeal Cases (UK)
]


def is_shortform(cite: str) -> bool:
    c = (cite or "").strip()
    for rx in _SHORTFORM_RES:
        if rx.search(c):
            return True
    return False


def is_foreign(cite: str) -> bool:
    c = (cite or "").strip()
    for rx in _FOREIGN_RES:
        if rx.search(c):
            return True
    return False


# ---- pipeline --------------------------------------------------------------

def load_extractions() -> list[dict[str, Any]]:
    """Return one row per (extraction, valid citation) pair."""
    flat: list[dict[str, Any]] = []
    for f in sorted(EXTRACT_DIR.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        for c in data.get("citations_valid") or []:
            if not isinstance(c, dict):
                continue
            cite_str = (c.get("citation_string") or "").strip()
            if not cite_str:
                continue
            flat.append({
                "citing_cluster": data.get("cluster_id"),
                "citing_court_id": data.get("court_id", ""),
                "citing_level": data.get("level", ""),
                "citing_case_name": data.get("case_name", ""),
                "citing_char_count": data.get("char_count", 0),
                "citation_string": cite_str,
                "cited_case_name": (c.get("cited_case_name") or "").strip(),
                "year": c.get("year"),
                # month/day/docket_number added 2026-05-15 (task #7) — needed
                # for district court WL/LEXIS fallback where the cited case
                # name may change between brief and CL caption (Rule 25(d),
                # SSA anonymization, John Doe reveal). Older real_extractions
                # JSON pre-dates these fields; .get() returns None.
                "month": c.get("month"),
                "day": c.get("day"),
                "docket_number": (c.get("docket_number") or "") if c.get("docket_number") else "",
                "court_hint": c.get("court_hint"),
                "parenthetical": c.get("parenthetical") or "",
            })
    return flat


def main() -> int:
    rng = random.Random(SEED)

    # 1. Load
    flat = load_extractions()
    n0 = len(flat)
    citing_n = len(set(r["citing_cluster"] for r in flat))
    print(f"Loaded {n0} valid citations from {citing_n} citing opinions")

    # 2. Pre-filter short-form
    shortform_dropped = 0
    flat2 = []
    for r in flat:
        if is_shortform(r["citation_string"]):
            shortform_dropped += 1
            continue
        flat2.append(r)
    print(f"After short-form filter: {len(flat2)} (dropped {shortform_dropped})")

    # 3. Pre-filter foreign
    foreign_dropped = 0
    flat3 = []
    for r in flat2:
        if is_foreign(r["citation_string"]):
            foreign_dropped += 1
            continue
        flat3.append(r)
    print(f"After foreign filter:    {len(flat3)} (dropped {foreign_dropped})")

    # 4. Dedup (citing_cluster, citation_string, parenthetical)
    seen: set[tuple[Any, str, str]] = set()
    dedup: list[dict[str, Any]] = []
    for r in flat3:
        key = (r["citing_cluster"], r["citation_string"], r["parenthetical"])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(r)
    print(f"After dedup:             {len(dedup)} (removed {len(flat3)-len(dedup)})")

    # 5. Tier-classify — court_hint (from Bluebook date parenthetical)
    # takes precedence over reporter pattern when available. Falls back
    # to reporter for short-form cites without court_hint.
    for r in dedup:
        r["cited_tier"] = tier_combined(r["citation_string"], r.get("court_hint") or "")

    # 5b. Within-cluster Other dedup: if the same (citing_cluster,
    # citation_string) appears as both a properly-classified tier AND
    # as Other (no court_hint), drop the Other copy as a short-form
    # follow-up to an earlier full cite. Per user 2026-05-15: these
    # second-mention WL pin cites carry no new tier info.
    by_cs: dict[tuple[Any, str], list[dict[str, Any]]] = defaultdict(list)
    for r in dedup:
        by_cs[(r["citing_cluster"], r["citation_string"])].append(r)
    deduped2: list[dict[str, Any]] = []
    other_dropped_dup = 0
    for key, rows in by_cs.items():
        tiers = {r["cited_tier"] for r in rows}
        if "Other" in tiers and len(tiers) > 1:
            # Keep only non-Other rows
            for r in rows:
                if r["cited_tier"] != "Other":
                    deduped2.append(r)
                else:
                    other_dropped_dup += 1
        else:
            deduped2.extend(rows)
    dedup = deduped2
    print(f"After within-cluster Other dedup: {len(dedup)} (dropped {other_dropped_dup} Other-tier dupes)")

    # 6. K=5 cap per (citing_cluster, cited_tier)
    by_op_tier: dict[tuple[Any, str], list[dict[str, Any]]] = defaultdict(list)
    for r in dedup:
        by_op_tier[(r["citing_cluster"], r["cited_tier"])].append(r)
    capped: list[dict[str, Any]] = []
    capped_dropped = 0
    for key, rows in by_op_tier.items():
        if len(rows) <= K_PER_OPINION_PER_TIER:
            capped.extend(rows)
        else:
            rng.shuffle(rows)
            capped.extend(rows[:K_PER_OPINION_PER_TIER])
            capped_dropped += len(rows) - K_PER_OPINION_PER_TIER
    print(f"After K={K_PER_OPINION_PER_TIER} cap:           {len(capped)} (dropped {capped_dropped} over-cap)")

    # Persist the full pool (all tiers including non-target)
    if capped:
        fields = list(capped[0].keys())
        with FINAL_POOL_CSV.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(capped)
        print(f"wrote {FINAL_POOL_CSV.name} ({len(capped)} rows)")

    # 7. Distribution by cited_tier
    by_tier = Counter(r["cited_tier"] for r in capped)
    print("\nNatural distribution after dedup + cap:")
    for t in ("SCOTUS", "Circuit", "State_COLR", "State_IAC",
              "Federal_District", "Other"):
        n = by_tier.get(t, 0)
        target = SAMPLE_PER_TIER if t in TARGET_TIERS else "-"
        status = ""
        if t in TARGET_TIERS:
            status = " (>= target)" if n >= SAMPLE_PER_TIER else f" (need {SAMPLE_PER_TIER-n} more)"
        print(f"  {t:<18} {n:>4}   target={target}{status}")

    # 8. Stratified sample
    by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in capped:
        if r["cited_tier"] in TARGET_TIERS:
            by_target[r["cited_tier"]].append(r)
    sample: list[dict[str, Any]] = []
    sample_counts: dict[str, int] = {}
    for tier in TARGET_TIERS:
        rows = by_target.get(tier, [])
        if len(rows) > SAMPLE_PER_TIER:
            rng.shuffle(rows)
            picked = rows[:SAMPLE_PER_TIER]
        else:
            picked = list(rows)
        sample.extend(picked)
        sample_counts[tier] = len(picked)
    print(f"\nStratified sample: {len(sample)} rows (target {SAMPLE_PER_TIER*len(TARGET_TIERS)})")
    for t in TARGET_TIERS:
        print(f"  {t:<14} {sample_counts.get(t,0):>3}/{SAMPLE_PER_TIER}")

    if sample:
        fields = list(sample[0].keys())
        with FINAL_200_CSV.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(sample)
        print(f"wrote {FINAL_200_CSV.name} ({len(sample)} rows)")

    # Narrative summary
    lines = [
        "# Step 3 — stratification summary",
        "",
        f"- Valid citations loaded: {n0}",
        f"- After short-form filter: {len(flat2)} (dropped {shortform_dropped})",
        f"- After foreign filter: {len(flat3)} (dropped {foreign_dropped})",
        f"- After dedup: {len(dedup)} (removed {len(flat3)-len(dedup)})",
        f"- After K={K_PER_OPINION_PER_TIER} cap per (citing_cluster, cited_tier): {len(capped)} (dropped {capped_dropped})",
        f"- Stratified sample: {len(sample)} rows (target {SAMPLE_PER_TIER*len(TARGET_TIERS)})",
        "",
        "## Pool distribution by cited tier",
        "",
        "| tier | n in pool | target | sample |",
        "|---|---|---|---|",
    ]
    for t in ("SCOTUS", "Circuit", "State_COLR", "State_IAC",
              "Federal_District", "Other"):
        n = by_tier.get(t, 0)
        if t in TARGET_TIERS:
            tgt = SAMPLE_PER_TIER
            samp = sample_counts.get(t, 0)
        else:
            tgt = "—"
            samp = "(not sampled)"
        lines.append(f"| {t} | {n} | {tgt} | {samp} |")
    lines.append("")
    lines.append("## Per-target-tier yield from this cohort")
    lines.append("")
    n_citing = len(set(r["citing_cluster"] for r in capped))
    lines.append(f"Pool drawn from {n_citing} citing opinions (after cap).")
    for t in TARGET_TIERS:
        n = by_tier.get(t, 0)
        per_op = (n / n_citing) if n_citing else 0
        lines.append(f"- {t}: {n}/{n_citing} = {per_op:.2f} per opinion")
    lines.append("")
    lines.append("## Caveats")
    lines.append("- Regional reporters (A.3d, P.3d, N.E.3d, etc.) default to State_COLR; actual COLR/IAC split waits on step 4 CL lookup.")
    lines.append("- Hallucinated citations (~175, ~13.5% of LLM output) are excluded by using citations_valid only.")
    lines.append("- Citing-court mix: 60 federal + 18 state opinions, with deliberate gaps on cal/nysd/texapp (see size_probe_2026-05-14.md and the mining commit).")
    lines.append("")
    SUMMARY_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {SUMMARY_MD.name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
