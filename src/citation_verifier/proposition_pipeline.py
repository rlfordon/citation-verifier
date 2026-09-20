"""Proposition verification pipeline — idempotent verbs over a workdir.

Evolved from the old brief_pipeline.py (pipeline-redesign design §2). The
deprecated `brief_pipeline` sys.modules alias was removed in 0.6.0 (one
minor version after 0.5); import this module directly. Verbs land
incrementally: verify/merge (§10 step 2) are here;
check-quotes/crosscheck/triage/assess/apply-assessments/report follow.
"""

from __future__ import annotations

import csv
import difflib
import json as json_mod
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cache import VerificationCache, open_citation_cache, resolve_cache_dir
from .client import AsyncCourtListenerClient
from .models import Status, VerificationResult
from .quote_matcher import (
    _best_match_with_passage,
    _extract_passage,
    _normalize_quote_text,
    verify_quote,
)
from .report_template import generate_report_html
from .verifier import CitationVerifier


# Version of the claims.csv column contract documented in
# docs/claims-csv-schema.md. Stamped into every workdir's run.json by
# _update_run_json so external consumers (the us-legal-research memo-import
# skill) can detect a breaking change. Bump when a column is renamed,
# removed, or its meaning/allowed values change.
CLAIMS_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class MergeStats:
    """Statistics from merging verification results into claims.csv."""
    matched: int = 0
    unmatched: int = 0
    unmatched_claims: list[str] = field(default_factory=list)
    statuses: dict[str, int] = field(default_factory=dict)
    opinion_count: int = 0


@dataclass
class Wave1Result:
    """Output of wave1_verify_and_download."""
    results: list[VerificationResult]
    miss_indices: list[int]
    download_stats: dict[str, int] = field(default_factory=dict)


@dataclass
class Wave2Result:
    """Output of wave2_fallback_and_download."""
    results: list[VerificationResult]
    download_stats: dict[str, int] = field(default_factory=dict)


@dataclass
class PipelineResult:
    """Output of full_pipeline."""
    wave1: Wave1Result
    wave2: Wave2Result
    merge: MergeStats


# ---------------------------------------------------------------------------
# Helpers — pinpoint stripping, filenames, CSV I/O
# ---------------------------------------------------------------------------

# Matches ", 527" or ", at 527" or ", 527-30" at end of volume/page cite
_PINPOINT_RE = re.compile(
    r",\s+(?:at\s+)?\d+(?:\s*[-\u2013]\s*\d+)?\s*(?=\(|$)"
)


def _strip_pinpoint(cite: str) -> str:
    """Remove pinpoint page references from a citation string."""
    return _PINPOINT_RE.sub(" ", cite).strip()


def _normalize_for_match(cite: str) -> str:
    """Normalize a citation for matching: strip pinpoint, lowercase, collapse whitespace."""
    s = _strip_pinpoint(cite)
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    return s


# Double-quoted spans (straight or smart). Single-quoted spans are skipped
# (apostrophe ambiguity); >= 2 words per design §6.4 -- the 2-word quoted
# term "judicial admissions" is exactly what the Am. Auto misses hinged on.
_QUOTE_SPAN = re.compile(r'[“"]([^"“”]{3,}?)[”"]')


def extract_quoted_spans(text: str | None, min_words: int = 2) -> list[str]:
    """Extract double-quoted spans of >= min_words words from text."""
    out = []
    for m in _QUOTE_SPAN.finditer(text or ""):
        span = m.group(1).strip()
        if len(span.split()) >= min_words:
            out.append(span)
    return out


def _sanitize_filename(case_name: str) -> str:
    """Convert a case name to a safe filename (no extension)."""
    # Replace common separators with underscore
    s = re.sub(r"\s+v\.?\s+", "_v_", case_name)
    # Keep only alphanumeric, underscores, hyphens
    s = re.sub(r"[^A-Za-z0-9_\-]", "_", s)
    # Collapse multiple underscores
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:120]  # cap length


def _slug_tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", s.lower()) if len(t) > 2}


_LINK_THRESHOLD = 0.25  # Jaccard; from the 2026-06-11 measurement workaround


def _link_opinion_file(workdir: Path, matched_name: str, cited_case: str,
                       cl_url: str) -> str:
    """Slug-token opinion linkage (replaces name-containment, §10 step 2).

    Scores every opinions/ file stem by Jaccard token overlap against three
    sources in priority order -- the cl_url slug, the CL matched name, the
    cited case-name part -- and returns the best file at or above threshold
    from the first source that produces one. Name-containment failed when
    CL's caption is longer than the cited name (e.g. 'Midwest Employers
    Cas. Co. v. Williams' vs the downloaded 'MIDWEST_EMPLOYERS_CASUALTY_CO
    _Plaintiff-Appellant-Appellee_v_Jo_Ann_WILLIAMS...').
    """
    opinions_dir = workdir / "opinions"
    if not opinions_dir.exists():
        return ""
    stems = {f.name: _slug_tokens(f.stem)
             for f in opinions_dir.iterdir() if f.is_file()}
    if not stems:
        return ""

    slug = cl_url.rstrip("/").rsplit("/", 1)[-1] if cl_url else ""
    name_part = cited_case.split(",")[0] if cited_case else ""
    for source in (slug, matched_name, name_part):
        st = _slug_tokens(source)
        if not st:
            continue
        best, best_score = "", 0.0
        for fname, ft in stems.items():
            if not ft:
                continue
            score = len(st & ft) / len(st | ft)
            if score > best_score:
                best, best_score = fname, score
        if best and best_score >= _LINK_THRESHOLD:
            return f"opinions/{best}"
    return ""


_VR_FIELDS = [
    "citation", "status", "confidence", "cl_url",
    "matched_name", "matched_court", "matched_court_id",
    "diagnostics_cat", "diagnostics_msg",
    "syllabus",
]


def _write_verification_csv(
    workdir: Path,
    citations: list[str],
    results: list[VerificationResult],
    append: bool = False,
) -> None:
    """Write or append verification results to verification_results.csv."""
    path = workdir / "verification_results.csv"
    mode = "a" if append else "w"
    write_header = not append or not path.exists()

    with open(path, mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_VR_FIELDS)
        if write_header:
            writer.writeheader()
        for cite, result in zip(citations, results):
            # v0.3 shape: structured Warnings replace Diagnostics; the legacy
            # diagnostic-bridge text lives in resolution_path[-1].notes
            # (Task 2 packs old Diagnostics there).
            warn_cats = [w.category.value for w in result.warnings]
            warn_msgs = [w.message for w in result.warnings]
            stage_notes = ""
            # SS11 bug 1 fix: the caption lives under stage-specific summary
            # keys; the accessor is the only sanctioned read surface.
            matched_name = result.matched_case_name
            if result.resolution_path:
                last = result.resolution_path[-1]
                if last.notes:
                    stage_notes = last.notes
            confidence = result.headline_confidence or 0.0
            # Combine warning messages and stage notes for the diagnostic
            # message column, preserving the old "; "-joined freeform shape.
            diag_msg_parts = list(warn_msgs)
            if stage_notes:
                diag_msg_parts.append(stage_notes)
            writer.writerow({
                "citation": cite,
                "status": result.status.value,
                "confidence": f"{confidence:.2f}",
                "cl_url": result.final_ids.absolute_url or "",
                "matched_name": matched_name,
                "matched_court": result.matched_court,
                "matched_court_id": result.matched_court_id,
                "diagnostics_cat": "; ".join(warn_cats),
                "diagnostics_msg": "; ".join(diag_msg_parts),
                # Surfaced via VerificationResult.syllabus accessor, which
                # walks resolution_path to the citation_lookup entry's
                # raw_response_summary (joined syllabus + nature_of_suit).
                # Restored from Phase 1 retro Q5 (Path A).
                "syllabus": result.syllabus or "",
            })


# ---------------------------------------------------------------------------
# Opinion downloading
# ---------------------------------------------------------------------------

# Phase 3 produces all four VERIFIED_* statuses; Phase 4 confirms each
# has a populated absolute_url and is download-eligible. WRONG_CASE,
# NOT_FOUND, and VERIFICATION_INCOMPLETE stay excluded -- downloading
# their (missing) opinion text doesn't make sense.
# Check Cite (2026-06-11): CITE_UNCONFIRMED is included -- it carries the
# winning stage's IDs, and downloading the matched case's text is exactly
# what lets the assessment agent show the brief's proposition isn't in it.
_DOWNLOADABLE_STATUSES = {
    Status.VERIFIED,
    Status.VERIFIED_PARTIAL,
    Status.VERIFIED_VIA_RECAP,
    Status.VERIFIED_DOCKET_ONLY,
    Status.CITE_UNCONFIRMED,
}


# Phase 4 Task 8: deterministic status -> badge-label fallback for the
# report-finding badge when the agent-authored badge_label is absent
# (legacy claims.csv or pre-agent runs). The agent-authored path is
# unchanged; this only governs the fallback render. Mapped by status
# value string (matches the cl_status column in claims.csv).
_STATUS_BADGE_FALLBACK: dict[str, str] = {
    "WRONG_CASE": "Case mismatch -- cite resolves to a different case",
    "CITE_UNCONFIRMED": "Check cite -- case found by name, cited location unconfirmed",
    "VERIFIED_PARTIAL": "Verified -- parallel cite only",
    "VERIFIED_VIA_RECAP": "Verified via RECAP",
    "VERIFIED_DOCKET_ONLY": "Docket only -- no opinion text",
    "VERIFICATION_INCOMPLETE": "Verification incomplete -- infrastructure error",
    "INSUFFICIENT_DATA": "Insufficient data -- citation lacks court and year",
}

# Gray-lane card explanations per unlocatable status (SS6.9 Gray lane).
# status -> (card explanation, methodology "reason")
_UNLOCATABLE_EXPLANATIONS: dict[str, tuple[str, str]] = {
    "NOT_FOUND": (
        "Case not found on CourtListener. Cannot verify against "
        "opinion text.",
        "Not in CourtListener database"),
    "INSUFFICIENT_DATA": (
        "The citation lacks the court and year data needed to verify "
        "it against CourtListener.",
        "Citation lacks court and year"),
    "VERIFICATION_INCOMPLETE": (
        "Verification could not complete (infrastructure error during "
        "lookup). Rerun the verify verb.",
        "Verification incomplete -- infrastructure error"),
}

# Threshold (chars of visible text) below which we suspect a downloaded
# "opinion" is actually a short order (vacatur, amendment notice, mandate,
# rehearing denial). When we hit one of these, we look for a sibling
# cluster on the same docket with substantive content.
_SHORT_OPINION_THRESHOLD = 3000


def _visible_text_len(data: dict[str, Any]) -> int:
    """Return the length of human-readable text in an opinion data dict."""
    text = data.get("text") or ""
    if not text:
        return 0
    if data.get("format") == "html":
        stripped = re.sub(r"<[^>]+>", " ", text)
        stripped = re.sub(r"\s+", " ", stripped).strip()
        return len(stripped)
    return len(text.strip())


async def _find_substantive_sibling(
    client: AsyncCourtListenerClient,
    matched_url: str,
    current_len: int,
) -> dict[str, Any] | None:
    """Look for a sibling cluster on the same docket with substantive content.

    Triggered when the primary cluster's opinion is suspiciously short (e.g.
    a vacatur order sitting in its own CL cluster while the real merits
    opinion lives in a separate cluster on the same docket — the Hertz 3d Cir.
    case was the motivating example).

    Returns a new opinion data dict (with source_url set to the sibling's URL)
    if a sibling has more than 2x the current text AND clears the short-opinion
    threshold; otherwise returns None.
    """
    cluster_match = re.search(r"/opinion/(\d+)/", matched_url)
    if not cluster_match:
        return None
    current_cluster_id = cluster_match.group(1)

    try:
        current_cluster = await client._request_with_retry(
            "GET", f"{client.BASE_URL}/clusters/{current_cluster_id}/",
        )
        if not isinstance(current_cluster, dict):
            return None
        docket_url = current_cluster.get("docket") or ""
        if not docket_url or not isinstance(docket_url, str):
            return None
        docket_id = docket_url.rstrip("/").split("/")[-1]

        resp = await client._request_with_retry(
            "GET", f"{client.BASE_URL}/clusters/",
            params={"docket": docket_id},
        )
        siblings = resp.get("results", []) if isinstance(resp, dict) else []
    except Exception:
        return None

    best: dict[str, Any] | None = None
    best_len = current_len
    for sib in siblings:
        if not isinstance(sib, dict):
            continue
        sib_id = str(sib.get("id") or "")
        if not sib_id or sib_id == current_cluster_id:
            continue
        sib_abs = sib.get("absolute_url", "")
        if sib_abs and not sib_abs.startswith("http"):
            sib_abs = f"https://www.courtlistener.com{sib_abs}"
        if not sib_abs:
            continue
        try:
            sib_data = await client.get_opinion_text_with_metadata(
                sib_abs, prefer_html=True,
            )
        except Exception:
            continue
        if not isinstance(sib_data, dict):
            continue
        sib_len = _visible_text_len(sib_data)
        if sib_len > best_len * 2 and sib_len >= _SHORT_OPINION_THRESHOLD:
            sib_data["source_url"] = sib_abs
            best = sib_data
            best_len = sib_len
    return best


def _ocr_manifest_path(workdir: Path) -> Path:
    return Path(workdir) / "opinions" / "ocr_status.json"


def _read_ocr_manifest(workdir: Path) -> dict[str, object]:
    """Read opinions/ocr_status.json -> {filename: extracted_by_ocr}. {} if absent."""
    path = _ocr_manifest_path(workdir)
    if not path.exists():
        return {}
    try:
        data = json_mod.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json_mod.JSONDecodeError, OSError):
        return {}


def _write_ocr_manifest(workdir: Path, mapping: dict[str, object]) -> None:
    """Merge `mapping` into opinions/ocr_status.json (no-op if mapping empty)."""
    if not mapping:
        return
    merged = _read_ocr_manifest(workdir)
    merged.update(mapping)
    path = _ocr_manifest_path(workdir)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json_mod.dumps(merged, indent=2), encoding="utf-8")


# opinions/manifest.json (memo-import): per-opinion metadata for downstream
# tools, keyed by opinion file stem. Written at download time -- date_filed
# rides along on the metadata fetch (client.get_opinion_text_with_metadata
# already walks cluster->docket for it), so no extra cluster-endpoint call.
def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _opinion_manifest_path(workdir: Path) -> Path:
    return Path(workdir) / "opinions" / "manifest.json"


def _read_opinion_manifest(workdir: Path) -> dict[str, dict]:
    """Read opinions/manifest.json -> {stem: {...}}. {} if absent/corrupt."""
    path = _opinion_manifest_path(workdir)
    if not path.exists():
        return {}
    try:
        data = json_mod.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json_mod.JSONDecodeError, OSError):
        return {}


def _write_opinion_manifest(workdir: Path, mapping: dict[str, dict]) -> None:
    """Merge `mapping` into opinions/manifest.json (no-op if empty)."""
    if not mapping:
        return
    merged = _read_opinion_manifest(workdir)
    merged.update(mapping)
    path = _opinion_manifest_path(workdir)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json_mod.dumps(merged, indent=2), encoding="utf-8")


async def _download_opinion(
    client: AsyncCourtListenerClient,
    workdir: Path,
    result: VerificationResult,
    citation: str,
    ocr_manifest: dict[str, object] | None = None,
    manifest: dict[str, dict] | None = None,
) -> str | None:
    """Download opinion text for a verified result. Returns saved filename or None.

    When `manifest` is provided, records a per-opinion metadata entry
    (keyed by file stem) for opinions/manifest.json -- capturing the
    post-swap cluster_id/url and the fetch-time date_filed."""
    matched_url = result.final_ids.absolute_url
    if not matched_url:
        return None

    opinions_dir = workdir / "opinions"
    opinions_dir.mkdir(exist_ok=True)

    try:
        data = await client.get_opinion_text_with_metadata(
            matched_url, prefer_html=True,
        )
        if not data:
            return None

        # If the matched cluster is a short order (vacatur, amendment notice,
        # rehearing denial), look for a sibling cluster on the same docket
        # carrying the substantive merits opinion and swap to that.
        if _visible_text_len(data) < _SHORT_OPINION_THRESHOLD:
            better = await _find_substantive_sibling(
                client, matched_url, _visible_text_len(data),
            )
            if better is not None:
                new_url = better["source_url"]
                m = re.search(r"/opinion/(\d+)/", new_url)
                swap_note = (
                    f"Matched cluster looked like a short order "
                    f"({_visible_text_len(data)} chars); swapped to sibling "
                    f"cluster {m.group(1) if m else '?'} on same docket with "
                    f"substantive content ({_visible_text_len(better)} chars)."
                )
                # TODO(phase-3): consider adding a `sibling_swap` WarningCategory
                # for this operational note. Phase 1's closed-set WarningCategory
                # has no slot for it (design §2.6); adding one requires a schema
                # CHANGELOG entry which is out of scope for Task 4. Until then,
                # we append to the resolving stage's notes (the legacy
                # diagnostic bridge).
                if result.resolution_path:
                    last = result.resolution_path[-1]
                    last.notes = (
                        f"{last.notes}; {swap_note}" if last.notes else swap_note
                    )
                data = better
                # Update FinalIds to reflect the swap so verification_results.csv
                # persists the new URL (CLAUDE.md: CSV is written after downloads).
                result.final_ids.absolute_url = new_url
                if m:
                    try:
                        result.final_ids.cluster_id = int(m.group(1))
                    except ValueError:
                        pass
                if better.get("case_name") and result.resolution_path:
                    result.resolution_path[-1].raw_response_summary["case_name"] = (
                        better["case_name"]
                    )

        # Stash the matched court (design SS6.5 court check). The metadata
        # fetch already walked cluster->docket->court; persisting it here
        # is the only no-extra-API-call source (citation-lookup clusters
        # carry no court field). Same stash pattern as the sibling-swap
        # case_name above; surfaced via VerificationResult.matched_court.
        if result.resolution_path:
            _summ = result.resolution_path[-1].raw_response_summary
            if data.get("court"):
                _summ["matched_court"] = data["court"]
            if data.get("court_id"):
                _summ["matched_court_id"] = data["court_id"]

        fmt = data.get("format", "text")
        matched_case_name = ""
        if result.resolution_path:
            matched_case_name = (
                result.resolution_path[-1].raw_response_summary.get("case_name", "")
                or ""
            )
        case_name = matched_case_name or data.get("case_name", "")
        if not case_name:
            # Fall back to citation name
            case_name = citation.split(",")[0].strip()

        base = _sanitize_filename(case_name)
        if not base:
            base = "unknown"

        # Per-opinion manifest entry (keyed by file stem == base). Built once;
        # recorded only on a successful write. cluster_id/absolute_url reflect
        # any sibling-swap done above; date_filed rode along on the fetch.
        def _record_manifest() -> None:
            if manifest is None:
                return
            manifest[base] = {
                "cluster_id": result.final_ids.cluster_id,
                "case_name": case_name,
                "court": data.get("court", "") or "",
                "date_filed": data.get("date_filed", "") or "",
                "citation": citation,
                "absolute_url": result.final_ids.absolute_url or "",
                "retrieved": _utcnow_iso(),
            }

        if fmt == "pdf":
            pdf_bytes = data.get("pdf_bytes")
            if not pdf_bytes:
                return None
            filename = f"{base}.pdf"
            (opinions_dir / filename).write_bytes(pdf_bytes)
            _record_manifest()
            return filename

        text = data.get("text", "")
        if not text or not text.strip():
            return None

        ext = ".html" if fmt == "html" else ".txt"
        filename = f"{base}{ext}"
        (opinions_dir / filename).write_text(text, encoding="utf-8")
        if ocr_manifest is not None:
            ocr_manifest[filename] = data.get("extracted_by_ocr")
        _record_manifest()
        return filename

    except Exception:
        return None


# ---------------------------------------------------------------------------
# Optional persistent CL lookup cache (memo-import: CITATION_VERIFIER_CACHE_DIR
# / --cache-dir). Off by default -- cache=None reproduces the old behavior
# exactly. When enabled, previously-resolved citations skip the CL API on
# reruns, which is the big win when many memos cite the same landmark cases.
# ---------------------------------------------------------------------------

async def _verify_batch_cached(
    verifier: CitationVerifier,
    citations: list[str],
    cache: VerificationCache | None,
    *,
    quick_only: bool,
    progress_callback: Any = None,
) -> list[VerificationResult]:
    """verify_batch with an optional persistent cache in front.

    Only resolved (downloadable) results are cached: a cached hit is always
    safe to reuse (the citation resolved), while misses are never stored so
    the fuzzy search/RECAP fallback still runs on every attempt and a
    quick-only miss can't poison the full path. ``cache=None`` delegates
    straight to ``verify_batch`` (unchanged default)."""
    if cache is None:
        return await verifier.verify_batch(
            citations, quick_only=quick_only,
            progress_callback=progress_callback,
        )
    results: list[VerificationResult | None] = [None] * len(citations)
    miss_idx: list[int] = []
    for i, cite in enumerate(citations):
        hit = cache.get(cite)
        if hit is not None and hit.status in _DOWNLOADABLE_STATUSES:
            results[i] = hit
        else:
            miss_idx.append(i)
    if miss_idx:
        sub = await verifier.verify_batch(
            [citations[i] for i in miss_idx], quick_only=quick_only,
            progress_callback=progress_callback,
        )
        for j, i in enumerate(miss_idx):
            results[i] = sub[j]
            if sub[j].status in _DOWNLOADABLE_STATUSES:
                cache.put(citations[i], sub[j])
    return [r for r in results if r is not None]


# ---------------------------------------------------------------------------
# Wave 1: batch citation lookup + download
# ---------------------------------------------------------------------------

async def wave1_verify_and_download(
    workdir: Path,
    citations: list[str],
    progress_callback: Any = None,
    cache: VerificationCache | None = None,
) -> Wave1Result:
    """Wave 1: quick batch lookup + download opinions for hits.

    Uses verify_batch(quick_only=True) for a single API call,
    then downloads opinions for all resolved citations.
    """
    workdir = Path(workdir)
    verifier = CitationVerifier()

    results = await _verify_batch_cached(
        verifier, citations, cache, quick_only=True,
        progress_callback=progress_callback,
    )

    # Identify misses for wave2
    miss_indices = [
        i for i, r in enumerate(results)
        if r.status not in _DOWNLOADABLE_STATUSES
    ]

    # Download opinions for hits (may mutate result.final_ids.absolute_url
    # if the pipeline swaps to a substantive sibling cluster — see
    # _download_opinion)
    download_stats = {"downloaded": 0, "failed": 0, "skipped": 0}
    ocr_manifest: dict[str, object] = {}
    manifest: dict[str, dict] = {}

    async with AsyncCourtListenerClient() as client:
        for i, (cite, result) in enumerate(zip(citations, results)):
            if result.status not in _DOWNLOADABLE_STATUSES:
                download_stats["skipped"] += 1
                continue

            filename = await _download_opinion(
                client, workdir, result, cite, ocr_manifest, manifest,
            )
            if filename:
                download_stats["downloaded"] += 1
            else:
                download_stats["failed"] += 1

    _write_ocr_manifest(workdir, ocr_manifest)
    _write_opinion_manifest(workdir, manifest)

    # Write verification_results.csv after downloads so any URL swaps persist
    _write_verification_csv(workdir, citations, results)

    return Wave1Result(
        results=results,
        miss_indices=miss_indices,
        download_stats=download_stats,
    )


# ---------------------------------------------------------------------------
# Wave 2: fallback search for misses + download
# ---------------------------------------------------------------------------

async def wave2_fallback_and_download(
    workdir: Path,
    citations: list[str],
    miss_indices: list[int],
    progress_callback: Any = None,
    cache: VerificationCache | None = None,
) -> Wave2Result:
    """Wave 2: full pipeline verification for citations missed in wave1.

    Uses verify_batch() without quick_only — opinion search + RECAP fallback.
    Appends results to verification_results.csv and downloads any resolved opinions.
    """
    workdir = Path(workdir)

    if not miss_indices:
        return Wave2Result(results=[], download_stats={"downloaded": 0, "failed": 0, "skipped": 0})

    miss_citations = [citations[i] for i in miss_indices]
    verifier = CitationVerifier()

    results = await _verify_batch_cached(
        verifier, miss_citations, cache, quick_only=False,
        progress_callback=progress_callback,
    )

    # Download opinions for newly resolved citations (may mutate
    # result.final_ids.absolute_url when swapping to a substantive sibling
    # cluster)
    download_stats = {"downloaded": 0, "failed": 0, "skipped": 0}
    ocr_manifest: dict[str, object] = {}
    manifest: dict[str, dict] = {}

    async with AsyncCourtListenerClient() as client:
        for cite, result in zip(miss_citations, results):
            if result.status not in _DOWNLOADABLE_STATUSES:
                download_stats["skipped"] += 1
                continue

            filename = await _download_opinion(
                client, workdir, result, cite, ocr_manifest, manifest,
            )
            if filename:
                download_stats["downloaded"] += 1
            else:
                download_stats["failed"] += 1

    _write_ocr_manifest(workdir, ocr_manifest)
    _write_opinion_manifest(workdir, manifest)

    # Append to verification_results.csv after downloads so URL swaps persist
    _write_verification_csv(workdir, miss_citations, results, append=True)

    return Wave2Result(
        results=results,
        download_stats=download_stats,
    )


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

async def full_pipeline(
    workdir: Path,
    citations: list[str],
    progress_callback: Any = None,
) -> PipelineResult:
    """Run wave1 + wave2 + merge in sequence."""
    w1 = await wave1_verify_and_download(workdir, citations, progress_callback)
    w2 = await wave2_fallback_and_download(workdir, citations, w1.miss_indices, progress_callback)
    m = merge_claims(workdir)
    return PipelineResult(wave1=w1, wave2=w2, merge=m)


# ---------------------------------------------------------------------------
# Versioned prompt templates (design §3 reproducibility / §6.6).
# Templates live in src/citation_verifier/prompts/; editing one means a NEW
# version (copy + bump header) because RecordedExecutor cassettes are keyed
# by prompt_version.
# ---------------------------------------------------------------------------

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_VERSION_RE = re.compile(r"<!--\s*prompt_version:\s*(\S+)\s*-->")
_LEADING_COMMENT_RE = re.compile(r"\A(?:\s*<!--.*?-->)*\s*", re.DOTALL)

DEFAULT_PROMPT_VERSION = "assess-v1"
ASSESS_V2_PROMPT_VERSION = "assess-v2"
EXTRACT_PROMPT_VERSION = "extract-v1"


def load_prompt_template(version: str) -> str:
    """Load a versioned prompt template body (header comments stripped).

    The file must declare the version it was asked for -- a renamed file
    can't silently serve a different prompt under a cassette's key."""
    path = _PROMPTS_DIR / f"{version.replace('-', '_')}.md"
    text = path.read_text(encoding="utf-8")
    m = _VERSION_RE.search(text)
    if not m or m.group(1) != version:
        raise ValueError(f"{path} does not declare prompt_version={version}")
    return _LEADING_COMMENT_RE.sub("", text).rstrip("\n")


def render_assess_prompt(version: str, opinion_path: str, cited_case: str,
                         proposition: str, quote_check_worst: str) -> str:
    """Render the assess prompt. Placeholder substitution is replace-based
    (not str.format) because the template body contains literal JSON
    braces."""
    body = load_prompt_template(version)
    for key, value in (("{opinion_path}", opinion_path),
                       ("{cited_case}", cited_case),
                       ("{proposition}", proposition),
                       ("{quote_check_worst}", quote_check_worst)):
        body = body.replace(key, value)
    return body


# Same floor the report uses for the deterministic passage (verify-brief
# Phase 2c: above ~0.65 the matched passage is usually the one to quote;
# below it's junk that would mislead the agent).
_V2_PASSAGE_HINT_MIN_SIM = 0.65


def render_assess_v2_claim_block(claim: dict) -> str:
    """One claim's entry in the v2 multi-claim prompt (design SS6.3
    cited_for, SS6.7 prescreen hint). Optional lines are omitted when
    empty so short claims stay short."""
    lines = [f"### Claim {claim['claim_id']}",
             f"Cited case: {claim.get('cited_case', '')}",
             f"Proposition: {claim.get('proposition', '')}"]
    if (claim.get("cited_for") or "").strip():
        lines.append("Cited for (judge this narrower assertion): "
                     + claim["cited_for"].strip())
    if (claim.get("brief_sentence") or "").strip():
        lines.append("Brief sentence: " + claim["brief_sentence"].strip())
    quoted = (claim.get("quoted_text") or "").strip()
    if quoted and quoted != "[]":
        lines.append("Quoted strings: " + quoted)
    lines.append("Quote check result: "
                 + (claim.get("quote_check_worst") or "NO_QUOTES"))
    try:
        checks = json_mod.loads(claim.get("quote_check") or "[]")
    except (json_mod.JSONDecodeError, ValueError):
        checks = []
    for qc in checks:
        sim = qc.get("similarity", 0) if isinstance(qc, dict) else 0
        if (isinstance(qc, dict) and qc.get("matched_passage")
                and sim >= _V2_PASSAGE_HINT_MIN_SIM):
            lines.append(f"Matched passage hint (deterministic, "
                         f"sim={sim:.2f}): {qc['matched_passage']}")
    if (claim.get("prescreen_hint") or "").strip():
        lines.append("Preliminary review hint: "
                     + claim["prescreen_hint"].strip())
    return "\n".join(lines)


def render_assess_v2_prompt(version: str, opinion_path: str,
                            claims: list[dict]) -> str:
    """Render the packed v2 prompt: one opinion, many claims."""
    blocks = "\n\n".join(render_assess_v2_claim_block(c) for c in claims)
    return (load_prompt_template(version)
            .replace("{opinion_path}", opinion_path)
            .replace("{claims_block}", blocks))


def render_extract_prompt(version: str, document_path: str) -> str:
    """Render the extract prompt (design SS3 verb 0). Replace-based like
    render_assess_prompt."""
    return load_prompt_template(version).replace(
        "{document_path}", document_path)


# ---------------------------------------------------------------------------
# Pipeline verbs (design §3): idempotent, importable, CLI-exposed.
# resume = rerun the verb; each checks its prerequisites.
# ---------------------------------------------------------------------------

def _update_run_json(workdir: Path, verb: str, **info: Any) -> None:
    """Reproducibility record (design §3): git hash + per-verb stamps."""
    path = workdir / "run.json"
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json_mod.loads(path.read_text(encoding="utf-8"))
        except json_mod.JSONDecodeError:
            data = {}
    # claims.csv column schema version (docs/claims-csv-schema.md). Pinned
    # so external consumers -- the us-legal-research memo-import skill --
    # can detect a breaking change; bump it when a column's meaning changes.
    data["schema_version"] = CLAIMS_SCHEMA_VERSION
    if not data.get("git_hash"):
        try:
            data["git_hash"] = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=10,
                cwd=str(Path(__file__).parent),
            ).stdout.strip() or "unknown"
        except Exception:
            data["git_hash"] = "unknown"
    verbs = data.setdefault("verbs", {})
    verbs[verb] = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **info,
    }
    path.write_text(json_mod.dumps(data, indent=2), encoding="utf-8")


def citations_from_workdir(workdir: Path) -> list[str]:
    """Unique citations to verify: claims.csv cited_case (order-preserving
    dedup), unioned with citations_toa.txt / citations_body.txt when the
    extract verb has produced them (one citation per line)."""
    workdir = Path(workdir)
    seen: dict[str, None] = {}
    with open(workdir / "claims.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cite = (row.get("cited_case") or "").strip()
            if cite:
                seen.setdefault(cite)
    for name in ("citations_toa.txt", "citations_body.txt"):
        p = workdir / name
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    seen.setdefault(line)
    return list(seen)


# extract-v1 verdict schema (documentation-shaped; run_extract validates).
_EXTRACT_V1_SCHEMA = {
    "claims": [{"page": "str", "proposition": "str", "cited_for": "str",
                "cited_case": "str", "quoted_text": ["str"],
                "brief_sentence": "str"}],
    "citations_toa": ["str"],
    "citations_body": ["str"],
}

# The single extract job's synthetic resume row id (the verdicts JSONL
# resume key is claim_id + prompt_version; extract has one job per workdir).
_EXTRACT_ROW_ID = "extract"

_CLAIMS_COLUMNS = ["claim_id", "page", "proposition", "cited_for",
                   "cited_case", "quoted_text", "brief_sentence"]


@dataclass
class ExtractStats:
    """Statistics from run_extract."""
    claims: int = 0
    toa: int = 0
    body: int = 0
    pending: bool = False


def run_extract(workdir: Path, document: str | Path, executor: Any = None,
                prompt_version: str = EXTRACT_PROMPT_VERSION,
                force: bool = False) -> ExtractStats | None:
    """Verb 0 (design SS3, LLM, optional front end): document ->
    claims.csv + citations_toa.txt + citations_body.txt.

    One job per workdir through the executor protocol (resume key =
    "extract" + prompt_version in jobs/extract_results.jsonl). Default
    executor is AgentToolExecutor (jobs mode): writes jobs/extract.json
    and pends; dispatch an agent, append the verdict, rerun to ingest.
    Idempotent: no-ops (returns None) when claims.csv already exists --
    prepared-pairs workdirs never re-extract (force=True to redo).
    """
    from .executor import AgentToolExecutor, Job, append_verdict_jsonl, \
        load_verdicts_jsonl

    workdir = Path(workdir)
    document = Path(document)
    if (workdir / "claims.csv").exists() and not force:
        return None

    results_path = workdir / "jobs" / "extract_results.jsonl"
    verdict = None
    if results_path.exists():
        for v in load_verdicts_jsonl(results_path):  # last write wins
            if (v.claim_id == _EXTRACT_ROW_ID
                    and v.prompt_version == prompt_version):
                verdict = v

    if verdict is None:
        job = Job(
            job_id=_EXTRACT_ROW_ID,
            claim_ids=[_EXTRACT_ROW_ID],
            prompt=render_extract_prompt(prompt_version, str(document)),
            prompt_version=prompt_version,
            files=[str(document)],
            schema=_EXTRACT_V1_SCHEMA,
        )
        if executor is None:
            executor = AgentToolExecutor(workdir / "jobs" / "extract.json")
        for v in executor.run([job]):
            append_verdict_jsonl(results_path, v)
            verdict = v

    if verdict is None:
        stats = ExtractStats(pending=True)
        _update_run_json(workdir, "extract", prompt_version=prompt_version,
                         pending=True)
        return stats

    stats = _write_extract_outputs(workdir, verdict.fields)
    _update_run_json(workdir, "extract", prompt_version=prompt_version,
                     claims=stats.claims, toa=stats.toa, body=stats.body)
    return stats


def _write_extract_outputs(workdir: Path,
                           fields: dict[str, Any]) -> ExtractStats:
    """Validate the extract verdict and write claims.csv (pipeline-assigned
    claim_id = <workdir.name>-NN) + the TOA/body citation lists."""
    claims = fields.get("claims")
    if not isinstance(claims, list) or not all(
            isinstance(c, dict) for c in claims):
        raise ValueError(
            "extract verdict: 'claims' must be a list of objects")
    rows = []
    for i, c in enumerate(claims, start=1):
        for required in ("cited_case", "proposition"):
            if not str(c.get(required) or "").strip():
                raise ValueError(
                    f"extract verdict: claim {i} missing {required}")
        quoted = c.get("quoted_text", [])
        rows.append({
            "claim_id": f"{workdir.name}-{i:02d}",
            "page": str(c.get("page") or ""),
            "proposition": str(c.get("proposition") or "").strip(),
            "cited_for": str(c.get("cited_for") or "").strip(),
            "cited_case": str(c.get("cited_case") or "").strip(),
            "quoted_text": (quoted if isinstance(quoted, str)
                            else json_mod.dumps(quoted, ensure_ascii=False)),
            "brief_sentence": str(c.get("brief_sentence") or "").strip(),
        })
    toa = [str(s).strip() for s in fields.get("citations_toa") or []
           if str(s).strip()]
    body = [str(s).strip() for s in fields.get("citations_body") or []
            if str(s).strip()]

    with open(workdir / "claims.csv", "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_CLAIMS_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    (workdir / "citations_toa.txt").write_text(
        "\n".join(toa) + ("\n" if toa else ""), encoding="utf-8")
    (workdir / "citations_body.txt").write_text(
        "\n".join(body) + ("\n" if body else ""), encoding="utf-8")
    return ExtractStats(claims=len(rows), toa=len(toa), body=len(body))


async def run_verify(workdir: Path, citations: list[str] | None = None,
                     force: bool = False, progress_callback: Any = None,
                     cache_dir: str | Path | None = None,
                     ) -> PipelineResult | None:
    """Verb 1 (design §3): wave1 + wave2 + opinion downloads. Idempotent --
    no-ops when verification_results.csv already exists (rerun with
    force=True to redo). Returns None on the no-op path.

    ``cache_dir`` (or the CITATION_VERIFIER_CACHE_DIR env var) enables a
    persistent CL lookup cache rooted there; left unset, no pipeline cache
    is used (default behavior unchanged)."""
    workdir = Path(workdir)
    if (workdir / "verification_results.csv").exists() and not force:
        return None
    if citations is None:
        citations = citations_from_workdir(workdir)
    resolved = resolve_cache_dir(str(cache_dir) if cache_dir else None)
    cache = open_citation_cache(resolved) if resolved is not None else None
    w1 = await wave1_verify_and_download(workdir, citations,
                                         progress_callback, cache=cache)
    w2 = await wave2_fallback_and_download(workdir, citations,
                                           w1.miss_indices,
                                           progress_callback, cache=cache)
    _update_run_json(workdir, "verify", citations=len(citations),
                     wave1_misses=len(w1.miss_indices))
    return PipelineResult(wave1=w1, wave2=w2,
                          merge=MergeStats())  # merge is its own verb


# v1 verdict schema, attached to jobs for transports that can enforce
# structured output (documentation-shaped; apply-assessments validates).
_ASSESS_V1_SCHEMA = {
    "assessment": "Green|Yellow|Red",
    "rationale": "one sentence",
}

# v2 packed-job contract: per-claim verdicts array (documentation-shaped;
# run_apply_assessments validates).
_ASSESS_V2_SCHEMA = {
    "verdicts": [{"claim_id": "str",
                  "support": "supported|partial|unsupported|unverifiable",
                  "badge_label": "str", "brief_block": "str",
                  "opinion_block": "str", "finding_analysis": "str"}],
}


@dataclass
class AssessStats:
    """Statistics from run_assess."""
    eligible: int = 0
    done: int = 0
    pending: int = 0
    skipped_deterministic: int = 0


def _assessable(claim: dict) -> bool:
    """Agent lane: opinion text linked and not resolved-to-wrong-case.
    Everything else gets a deterministic lane (scoring/apply)."""
    return bool(claim.get("opinion_file")) and (
        claim.get("cl_status") != "WRONG_CASE")


def run_assess(workdir: Path, executor: Any = None,
               prompt_version: str = DEFAULT_PROMPT_VERSION) -> AssessStats:
    """Verb 6 (design §3, LLM): grouped assessment jobs via the executor.

    Selects assessable claims lacking a verdict for prompt_version
    (resume key = claim_id + prompt_version), renders one job per claim
    from the versioned template (jobs are ordered by opinion file; §6.8
    multi-opinion packing arrives with assess-v2 — every recorded
    cassette is single-claim v1), writes jobs/assess.json through the
    executor. Default executor is AgentToolExecutor (jobs mode): no
    verdicts are produced in-process; dispatch Agent-tool subagents that
    append to jobs/assess_results.jsonl, then rerun this verb to ingest.
    """
    from .executor import AgentToolExecutor, Job, append_verdict_jsonl, \
        load_verdicts_jsonl

    workdir = Path(workdir)
    results_path = workdir / "jobs" / "assess_results.jsonl"
    have: set[str] = set()
    if results_path.exists():
        have = {v.claim_id for v in load_verdicts_jsonl(results_path)
                if v.prompt_version == prompt_version}

    with open(workdir / "claims.csv", newline="", encoding="utf-8") as f:
        claims = list(csv.DictReader(f))

    stats = AssessStats()
    todo: list[dict] = []
    for c in claims:
        if not _assessable(c):
            stats.skipped_deterministic += 1
            continue
        stats.eligible += 1
        if c["claim_id"] in have:
            stats.done += 1
        else:
            todo.append(c)
    todo.sort(key=lambda c: c.get("opinion_file", ""))

    if prompt_version == DEFAULT_PROMPT_VERSION:
        # v1: one job per claim, byte-pinned prompt (cassette compat).
        jobs = [Job(
            job_id=f"assess-{c['claim_id']}",
            claim_ids=[c["claim_id"]],
            prompt=render_assess_prompt(
                prompt_version, str(workdir / c["opinion_file"]),
                c["cited_case"], c["proposition"],
                c.get("quote_check_worst", "NO_QUOTES")),
            prompt_version=prompt_version,
            files=[c["opinion_file"]],
            schema=_ASSESS_V1_SCHEMA,
        ) for c in todo]
    else:
        # v2+: one packed job per opinion (Step 8 decision log:
        # per-opinion only -- documented deviation from SS6.8's
        # multi-opinion caps, which economized interactive subagent
        # dispatch; SDK jobs are cheap to spawn and per-opinion keeps
        # the shared-read win with a smaller failure blast radius).
        by_opinion: dict[str, list[dict]] = {}
        for c in todo:
            by_opinion.setdefault(c["opinion_file"], []).append(c)
        jobs = [Job(
            job_id="assess-" + Path(opinion).stem[:60],
            claim_ids=[c["claim_id"] for c in group],
            prompt=render_assess_v2_prompt(
                prompt_version, str(workdir / opinion), group),
            prompt_version=prompt_version,
            files=[opinion],
            schema=_ASSESS_V2_SCHEMA,
        ) for opinion, group in by_opinion.items()]

    if jobs:
        if executor is None:
            executor = AgentToolExecutor(workdir / "jobs" / "assess.json")
        for v in executor.run(jobs):
            append_verdict_jsonl(results_path, v)
            stats.done += 1
    stats.pending = stats.eligible - stats.done
    _update_run_json(workdir, "assess", prompt_version=prompt_version,
                     done=stats.done, pending=stats.pending)
    return stats


@dataclass
class ApplyStats:
    """Statistics from run_apply_assessments."""
    applied: int = 0
    invalid: int = 0
    missing: int = 0  # assessable claims with no verdict yet
    invalid_claims: list[str] = field(default_factory=list)


_VALID_COLORS = ("Green", "Yellow", "Red")


def run_apply_assessments(workdir: Path,
                          prompt_version: str = DEFAULT_PROMPT_VERSION,
                          ) -> ApplyStats:
    """Verb 7 (design §3 / §6.6): verdicts JSONL -> claims.csv.

    The pipeline owns the CSV; subagents only append JSON lines. Each
    verdict is validated against the version's schema and the §6.4
    quote_floor is enforced (the agent can lower a color, never raise it
    past the floor). Writes: assessment (floor-enforced color), support
    (empty under the single-color v1 schema; v2 fills it), assessed_by
    (model/prompt_version), and finding_analysis (rationale) when empty.
    """
    from .executor import load_verdicts_jsonl
    from .scoring import _SEVERITY_RANK

    workdir = Path(workdir)
    results_path = workdir / "jobs" / "assess_results.jsonl"
    if not results_path.exists():
        raise FileNotFoundError(
            f"{results_path} missing -- run the assess verb first")
    verdicts = {v.claim_id: v for v in load_verdicts_jsonl(results_path)
                if v.prompt_version == prompt_version}  # last write wins

    with open(workdir / "claims.csv", newline="", encoding="utf-8") as f:
        claims = list(csv.DictReader(f))

    stats = ApplyStats()
    for c in claims:
        if not _assessable(c):
            continue
        v = verdicts.get(c["claim_id"])
        if v is None:
            stats.missing += 1
            continue
        if "support" in v.fields:
            # v2+ verdict: color derived from the SS6.9 axes (existence
            # from cl_status, support from the agent, quote from the
            # deterministic check); the agent never outputs a color.
            from .scoring import derive_color
            support = v.fields.get("support")
            if support not in ("supported", "partial", "unsupported",
                               "unverifiable"):
                stats.invalid += 1
                stats.invalid_claims.append(c["claim_id"])
                continue
            # Quote axis = the FLOOR-EFFECTIVE verdict (SS6.9's CLOSE row
            # cites "floor, SS6.4", and the SS6.4 banded calibration says
            # CLOSE in [0.75, 0.85) is transcription noise, quote_floor
            # unset). Passing raw quote_check_worst double-floors
            # noise-band greens (withers-21, Step 8 acceptance finding).
            quote_axis = (c.get("quote_check_worst", "")
                          if (c.get("quote_floor") or "").strip() else "")
            color = derive_color(c.get("cl_status", ""), support,
                                 quote_axis)
            c["support"] = support
            c["badge_label"] = v.fields.get("badge_label", "")
            # F3 (cost-audit): the brief_block is the brief's own language,
            # which claims.csv already holds verbatim in brief_sentence.
            # Default from it when the verdict omits/empties brief_block
            # (a deterministic copy is more reliable than asking the agent
            # to transcribe -- and lets a future assess-v3 drop the field).
            c["brief_block"] = (v.fields.get("brief_block", "").strip()
                                or c.get("brief_sentence", ""))
            c["opinion_block"] = v.fields.get("opinion_block", "")
            # v2 owns the analysis (richer than v1's rationale)
            c["finding_analysis"] = v.fields.get("finding_analysis", "")
        else:
            # v1 verdict: single agent color, validated.
            color = v.fields.get("assessment")
            if color not in _VALID_COLORS:
                stats.invalid += 1
                stats.invalid_claims.append(c["claim_id"])
                continue
            c["support"] = v.fields.get("support", "")
            if not c.get("finding_analysis"):
                c["finding_analysis"] = v.fields.get("rationale", "")
        floor = c.get("quote_floor", "")
        if (floor in _SEVERITY_RANK and color in _SEVERITY_RANK
                and _SEVERITY_RANK[color] < _SEVERITY_RANK[floor]):
            color = floor
        c["assessment"] = color
        c["assessed_by"] = f"{v.model}/{v.prompt_version}"
        stats.applied += 1

    fields = list(claims[0].keys())
    for col in ("assessment", "support", "assessed_by", "finding_analysis",
                "badge_label", "brief_block", "opinion_block"):
        if col not in fields:
            fields.append(col)
    for c in claims:
        for col in fields:
            c.setdefault(col, "")
    with open(workdir / "claims.csv", "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(claims)

    _update_run_json(workdir, "apply-assessments",
                     prompt_version=prompt_version, applied=stats.applied,
                     invalid=stats.invalid, missing=stats.missing)
    return stats


@dataclass
class ReportStats:
    """Statistics from run_report (lane counts are per claims.csv ROW;
    the gray lane groups rows into per-case cards in the HTML)."""
    path: Path | None = None
    findings: int = 0  # Red + Yellow cards
    check_cite: int = 0
    verified: int = 0
    unable: int = 0


def run_report(workdir: Path) -> ReportStats:
    """Verb 8 (design SS3 row 8): claims.csv -> report.html with the
    SS6.9 lanes. Reads brief_metadata.json for the header when present
    (same convention as the legacy verify-brief --report). Idempotent --
    regenerates the HTML on every run."""
    from .scoring import CHECK_CITE, GRAY, GREEN, report_lane

    workdir = Path(workdir)
    meta: dict[str, Any] = {}
    meta_path = workdir / "brief_metadata.json"
    if meta_path.exists():
        try:
            meta = json_mod.loads(meta_path.read_text(encoding="utf-8"))
        except json_mod.JSONDecodeError:
            meta = {}
    path = generate_report(
        workdir,
        title=meta.get("title", ""),
        case_name=meta.get("case_name", ""),
        case_number=meta.get("case_number", ""),
        filed_date=meta.get("filed_date", ""),
        report_date=meta.get("report_date", ""),
    )
    stats = ReportStats(path=path)
    with open(workdir / "claims.csv", newline="", encoding="utf-8") as f:
        for c in csv.DictReader(f):
            lane = report_lane(c.get("cl_status", ""),
                               c.get("assessment", ""),
                               c.get("opinion_file", ""))
            if lane == GREEN:
                stats.verified += 1
            elif lane == GRAY:
                stats.unable += 1
            elif lane == CHECK_CITE:
                stats.check_cite += 1
            else:
                stats.findings += 1
    # findings.json: the report's per-claim data model for downstream tools
    # (memo-import). Emitted alongside report.html; report.html unchanged.
    (workdir / "findings.json").write_text(
        json_mod.dumps({"claims": _build_findings_model(workdir)}, indent=2),
        encoding="utf-8")
    _update_run_json(workdir, "report", findings=stats.findings,
                     check_cite=stats.check_cite,
                     verified=stats.verified, unable=stats.unable)
    return stats


def run_merge(workdir: Path) -> MergeStats:
    """Verb 2 (design §3): join claims <-> results + slug opinion linkage.
    Requires verification_results.csv (run the verify verb first)."""
    workdir = Path(workdir)
    vr = workdir / "verification_results.csv"
    if not vr.exists():
        raise FileNotFoundError(f"{vr} missing -- run the verify verb first")
    stats = merge_claims(workdir)
    _update_run_json(workdir, "merge", matched=stats.matched,
                     linked=stats.opinion_count)
    return stats


def run_check_quotes(workdir: Path) -> "QuoteCheckStats":
    """Verb 3 (design SS3): deterministic quote verdicts + SS6.4 floors.
    Thin wrapper over check_quotes adding the run.json stamp."""
    workdir = Path(workdir)
    stats = check_quotes(workdir)
    _update_run_json(workdir, "check-quotes",
                     total=stats.total_claims)
    return stats


# ---------------------------------------------------------------------------
# Verb 4: crosscheck (design SS6.5) -- deterministic flags, never verdicts.
# ---------------------------------------------------------------------------

_BASE_CITE_RE = re.compile(
    r"^(?P<name>.+?),\s*(?P<vol>\d+)\s+"
    r"(?P<rep>[A-Z][A-Za-z0-9. ']*?)\s+(?P<page>\d+)")
_CITE_YEAR_RE = re.compile(r"\((?:[^()]*?\s)?(\d{4})\)")
_PIN_AFTER_BASE_RE = re.compile(r"^\s*,\s*(\d+)")
_FOOTNOTE_PIN_RE = re.compile(r"\bn\.\s*(\d+)", re.IGNORECASE)
_STAR_PAGE_RE = re.compile(r"\*\s?(\d{1,5})\b")


def _parse_base_cite(text: str) -> dict[str, str] | None:
    """Volume/reporter/page/year + normalized case name from a citation
    string. Regex-level parse (the SS6.5 diff needs components, not a
    full ParsedCitation)."""
    m = _BASE_CITE_RE.match(text.strip())
    if not m:
        return None
    year = _CITE_YEAR_RE.search(text)
    return {
        "name_norm": re.sub(r"[^a-z0-9]", "", m.group("name").lower()),
        "volume": m.group("vol"),
        "reporter": re.sub(r"[\s.]", "", m.group("rep")).lower(),
        "page": m.group("page"),
        "year": year.group(1) if year else "",
        "_end": str(m.end()),
    }


def _toa_body_variants(workdir: Path) -> dict[str, list[str]]:
    """name_norm -> distinct citation variants across the TOA/body lists
    (only names with >1 variant -- the Bryant 597-vs-97 class)."""
    seen: dict[str, dict[tuple, str]] = {}
    for fname in ("citations_toa.txt", "citations_body.txt"):
        p = workdir / fname
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            c = _parse_base_cite(line)
            if not c:
                continue
            key = (c["volume"], c["reporter"], c["page"])
            seen.setdefault(c["name_norm"], {}).setdefault(key, line)
    return {name: list(variants.values())
            for name, variants in seen.items() if len(variants) > 1}


def _read_clean_opinion(workdir: Path, opinion_file: str) -> str:
    """Opinion text with HTML stripped (same strip as check_quotes).

    CL harvard-XML footnote markers (<footnotemark>N</footnotemark>) are
    rewritten to " n.N " BEFORE the tag strip so the SS6.5 footnote-
    existence check can see them -- a plain strip leaves a bare number
    and every n.N pincite false-flags as footnote_missing (the Step 8
    9.6 Withers finding, withers-36 / Missouri v. Jenkins n.10)."""
    try:
        raw = (workdir / opinion_file).read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError, OSError):
        return ""
    raw = re.sub(r"<footnotemark>\s*(\d+)\s*</footnotemark>", r" n.\1 ",
                 raw)
    clean = re.sub(r"<[^>]+>", " ", raw)
    clean = re.sub(r"&\w+;", " ", clean)
    return re.sub(r"\s+", " ", clean).strip()


def _pincite_flag(cited_case: str, opinion_text: str) -> dict | None:
    """Best-effort SS6.5 pincite check: star-pagination range + footnote
    existence. Returns a details dict or None. Flags only -- never
    feeds the color function."""
    base = _parse_base_cite(cited_case)
    if not base or not opinion_text:
        return None
    flag: dict[str, Any] = {}
    rest = cited_case.strip()[int(base["_end"]):]
    pin_m = _PIN_AFTER_BASE_RE.match(rest)
    if pin_m:
        pin = int(pin_m.group(1))
        stars = [int(s) for s in _STAR_PAGE_RE.findall(opinion_text)]
        # >=3 markers = real star pagination, not stray asterisks
        if len(stars) >= 3:
            lo, hi = min(stars), max(stars)
            if not (lo <= pin <= hi):
                flag["pinpoint"] = str(pin)
                flag["star_range"] = [lo, hi]
    fn_m = _FOOTNOTE_PIN_RE.search(cited_case)
    if fn_m:
        fn = fn_m.group(1)
        if not re.search(
                rf"(?:n\.\s*{fn}\b|footnote\s+{fn}\b|\[fn?{fn}\])",
                opinion_text, re.IGNORECASE):
            flag["footnote_missing"] = fn
    return flag or None


@dataclass
class CrosscheckStats:
    """Statistics from run_crosscheck."""
    total: int = 0
    toa_mismatches: int = 0
    court_mismatches: int = 0
    pincite_flags: int = 0


def run_crosscheck(workdir: Path) -> CrosscheckStats:
    """Verb 4 (design SS3 / SS6.5): deterministic TOA-vs-body diff,
    court check, and best-effort pincite check. Writes the
    crosscheck_flags JSON column ('' when clean). Flags only: never
    touches assessment colors. Idempotent -- recomputes on rerun."""
    from .court_map import lookup_court_id
    from .parser import parse_citation

    workdir = Path(workdir)
    with open(workdir / "claims.csv", newline="", encoding="utf-8") as f:
        claims = list(csv.DictReader(f))

    variants = _toa_body_variants(workdir)

    # citation -> vr row (for matched_court_id), same join key as merge
    vr_lookup: dict[str, dict] = {}
    vr_path = workdir / "verification_results.csv"
    if vr_path.exists():
        with open(vr_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                key = _normalize_for_match(row.get("citation", ""))
                if key:
                    vr_lookup[key] = row

    stats = CrosscheckStats()
    opinion_cache: dict[str, str] = {}
    for claim in claims:
        stats.total += 1
        cited = claim.get("cited_case", "") or ""
        flags: dict[str, Any] = {}

        # 1. TOA vs body (SS6.5 bullet 1)
        base = _parse_base_cite(cited)
        if base and base["name_norm"] in variants:
            flags["toa_mismatch"] = {
                "variants": variants[base["name_norm"]]}

        # 2. Court check (SS6.5 bullet 2): cited vs matched CL court id.
        # Skips when either side is unknown (state courts, legacy vr
        # CSVs without matched_court_id, unparseable cites) -- best
        # effort by design.
        vr_row = vr_lookup.get(_normalize_for_match(cited), {})
        matched_id = (vr_row.get("matched_court_id") or "").strip()
        if matched_id:
            try:
                parsed = parse_citation(cited)
            except Exception:
                parsed = None
            cited_court = getattr(parsed, "court", None)
            cited_id = lookup_court_id(cited_court) if cited_court else None
            if cited_id and cited_id != matched_id:
                stats.court_mismatches += 1
                flags["court_mismatch"] = {
                    "cited": cited_court,
                    "cited_id": cited_id,
                    "matched_id": matched_id,
                    "matched": vr_row.get("matched_court", ""),
                }

        # 3. Pincite check (SS6.5 bullet 3, best-effort flags)
        opinion_file = claim.get("opinion_file", "") or ""
        if opinion_file:
            if opinion_file not in opinion_cache:
                opinion_cache[opinion_file] = _read_clean_opinion(
                    workdir, opinion_file)
            pin = _pincite_flag(cited, opinion_cache[opinion_file])
            if pin:
                stats.pincite_flags += 1
                flags["pincite_flag"] = pin

        if "toa_mismatch" in flags:
            stats.toa_mismatches += 1
        claim["crosscheck_flags"] = (
            json_mod.dumps(flags, ensure_ascii=False) if flags else "")

    fields = list(claims[0].keys()) if claims else ["crosscheck_flags"]
    if "crosscheck_flags" not in fields:
        fields.append("crosscheck_flags")
    for c in claims:
        c.setdefault("crosscheck_flags", "")
    with open(workdir / "claims.csv", "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(claims)

    _update_run_json(workdir, "crosscheck", total=stats.total,
                     toa=stats.toa_mismatches,
                     court=stats.court_mismatches,
                     pincite=stats.pincite_flags)
    return stats


# ---------------------------------------------------------------------------
# Verb 5: triage (design SS6.7) -- assessment depth per claim.
# ---------------------------------------------------------------------------
#
# The Haiku summary-hint prescreen path was deleted 2026-07-02 (cost-audit
# F4). It defaulted OFF because the 2026-06-13 per-phase A/B (opus-v2 vs
# opus-v2-hints over the 3 corpora) measured it HARMFUL: no net A/B gain
# (55/61 both) and a Withers 16->14 yellows regression, both losses in the
# lenient direction -- the worst failure mode for a citation checker. The
# `prescreen_hint` CSV column is still tolerated as a legacy field
# (render_assess_v2_claim_block consumes it if present, merge carries it
# through), but nothing populates it anymore. Revisiting hints means a new
# prompt version anyway; see docs/plans/2026-07-01-pipeline-cost-audit.md F4.

_CLEAN_VERIFIED_STATUSES = {
    "VERIFIED", "VERIFIED_PARTIAL", "VERIFIED_VIA_RECAP",
    "VERIFIED_DOCKET_ONLY",
}


def _crosscheck_flag_lines(claim: dict) -> list[str]:
    """Human-readable card flags from the crosscheck_flags JSON column
    (SS6.5: a flag renders on the card even when support is otherwise
    fine; flags never move a claim between lanes). Missing / empty /
    malformed -> [] (legacy claims.csv tolerated)."""
    raw = (claim.get("crosscheck_flags") or "").strip()
    if not raw:
        return []
    try:
        flags = json_mod.loads(raw)
    except (json_mod.JSONDecodeError, ValueError):
        return []
    if not isinstance(flags, dict):
        return []
    lines: list[str] = []
    toa = flags.get("toa_mismatch") or {}
    if toa.get("variants"):
        lines.append("TOA/body citation mismatch: "
                     + " vs ".join(toa["variants"]))
    court = flags.get("court_mismatch") or {}
    if court:
        matched_name = court.get("matched", "")
        suffix = f" ({matched_name})" if matched_name else ""
        lines.append(
            f"Court mismatch: brief cites {court.get('cited_id', '?')}, "
            f"CL match is {court.get('matched_id', '?')}{suffix}")
    pin = flags.get("pincite_flag") or {}
    if pin.get("pinpoint"):
        lo, hi = (pin.get("star_range") or ["?", "?"])[:2]
        lines.append(f"Pincite {pin['pinpoint']} outside the opinion's "
                     f"star-pagination range {lo}-{hi}")
    if pin.get("footnote_missing"):
        lines.append(f"Footnote n.{pin['footnote_missing']} not found "
                     f"in the opinion text")
    return lines


def _triage_track_for(claim: dict) -> str:
    """Deterministic SS6.7 track. '' = deterministic lane (not agent-
    assessable). The SKILL's two LLM-judgment criteria (syllabus topic
    mismatch, lead authority) are out of deterministic scope -- the
    full-track net below is correspondingly conservative."""
    if not _assessable(claim):
        return ""
    if claim.get("quote_check_worst") in ("FABRICATED", "CLOSE"):
        return "full"
    if (claim.get("quote_floor") or "").strip():
        return "full"
    quoted = (claim.get("quoted_text") or "").strip()
    if quoted and quoted != "[]":
        return "full"
    if (claim.get("crosscheck_flags") or "").strip():
        return "full"
    if claim.get("cl_status") not in _CLEAN_VERIFIED_STATUSES:
        return "full"
    return "fast"


@dataclass
class TriageStats:
    """Statistics from run_triage."""
    full: int = 0
    fast: int = 0
    skipped: int = 0


def run_triage(workdir: Path) -> TriageStats:
    """Verb 5 (design SS3 / SS6.7): assessment depth per claim.

    Writes triage_track ('full' | 'fast' | '' for the deterministic
    lane) via the deterministic rules in _triage_track_for. Idempotent --
    tracks recompute on rerun. Any existing prescreen_hint column is
    carried through unchanged (tolerated legacy field; the prescreen path
    was deleted in cost-audit F4), but no new column is created.
    """
    workdir = Path(workdir)
    with open(workdir / "claims.csv", newline="", encoding="utf-8") as f:
        claims = list(csv.DictReader(f))

    stats = TriageStats()
    for c in claims:
        track = _triage_track_for(c)
        c["triage_track"] = track
        if track == "full":
            stats.full += 1
        elif track == "fast":
            stats.fast += 1
        else:
            stats.skipped += 1

    fields = list(claims[0].keys()) if claims else []
    if "triage_track" not in fields:
        fields.append("triage_track")
    for c in claims:
        for col in fields:
            c.setdefault(col, "")
    with open(workdir / "claims.csv", "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(claims)

    _update_run_json(workdir, "triage", full=stats.full, fast=stats.fast,
                     skipped=stats.skipped)
    return stats


# ---------------------------------------------------------------------------
# merge_claims (Task 3 — already implemented)
# ---------------------------------------------------------------------------

def merge_claims(workdir: Path) -> MergeStats:
    """Merge verification_results.csv into claims.csv.

    Reads both files, joins on base citation (pinpoint-stripped),
    writes updated claims.csv with verification columns added.
    """
    workdir = Path(workdir)
    claims_path = workdir / "claims.csv"
    vr_path = workdir / "verification_results.csv"

    stats = MergeStats()

    # Read verification results into a lookup by normalized citation
    vr_lookup: dict[str, dict[str, str]] = {}
    if vr_path.exists():
        with open(vr_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                key = _normalize_for_match(row.get("citation", ""))
                if key:
                    vr_lookup[key] = row

    # Read claims
    with open(claims_path, newline="", encoding="utf-8") as f:
        claims = list(csv.DictReader(f))

    # Merge columns. claim_id / cited_for (design §4 input contract) lead
    # the schema when the input claims carry them; legacy claims.csv
    # without them still merge unchanged. Any OTHER column already present
    # in claims.csv (downstream verbs' output -- quote_floor,
    # crosscheck_flags, triage_track, prescreen_hint, support,
    # assessed_by, brief_block, ...) is carried through so a standalone
    # merge rerun never drops it (resume = rerun the verb; review #2).
    output_fields = [
        "page", "proposition", "cited_case",
        "retrieved_case", "supporting_language", "assessment",
        "cl_url", "cl_status", "diagnostics", "opinion_file",
        "syllabus",
    ]
    if claims:
        if "cited_for" in claims[0]:
            output_fields.insert(output_fields.index("cited_case"),
                                 "cited_for")
        if "claim_id" in claims[0]:
            output_fields.insert(0, "claim_id")
        for col in claims[0].keys():
            if col not in output_fields:
                output_fields.append(col)

    merged_rows: list[dict[str, str]] = []
    for claim in claims:
        cited = claim.get("cited_case", "")
        key = _normalize_for_match(cited)

        vr = vr_lookup.get(key, {})

        status = vr.get("status", "")
        url = vr.get("cl_url", "")
        matched_name = vr.get("matched_name", "")
        diag_msg = vr.get("diagnostics_msg", "")

        if status:
            stats.matched += 1
            stats.statuses[status] = stats.statuses.get(status, 0) + 1
        else:
            stats.unmatched += 1
            if cited:
                stats.unmatched_claims.append(cited)

        # Slug-token opinion linkage (§10 step 2), gated on CL having
        # LOCATED the case (review #1): only a citation CL actually
        # matched (non-empty cl_url or matched_name) may link an
        # opinions/ file. A NOT_FOUND / unmatched citation has both
        # empty, so it can no longer borrow another located case's
        # opinion via the bare cited-name token source -- it stays Gray
        # "unable to verify" instead of being assessed against the wrong
        # opinion. (POSSIBLE_MATCH / LIKELY_REAL carry a match, so they
        # still link.)
        opinion_file = (_link_opinion_file(workdir, matched_name, cited, url)
                        if (url or matched_name) else "")

        if opinion_file:
            stats.opinion_count += 1

        # Start from the input row to preserve every existing column
        # (review #2), then overlay the merge-derived fields.
        row = dict(claim)
        row["retrieved_case"] = matched_name
        row["cl_url"] = url
        row["cl_status"] = status
        row["diagnostics"] = diag_msg
        row["opinion_file"] = opinion_file
        row["syllabus"] = vr.get("syllabus", "")
        row.setdefault("assessment", claim.get("assessment", ""))
        row.setdefault("supporting_language",
                       claim.get("supporting_language", ""))
        merged_rows.append(row)

    # Write updated claims.csv
    with open(claims_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=output_fields)
        writer.writeheader()
        writer.writerows(merged_rows)

    return stats


# ---------------------------------------------------------------------------
# Quote checking
# ---------------------------------------------------------------------------

@dataclass
class QuoteCheckStats:
    """Statistics from check_quotes."""
    total_claims: int = 0
    checked: int = 0
    no_quotes: int = 0
    no_opinion: int = 0
    verbatim: int = 0
    close: int = 0
    fabricated: int = 0
    derived_quotes: int = 0


# CLOSE quotes at or above this similarity are near-verbatim (the matcher's
# VERBATIM cut is >0.85): they keep their CLOSE verdict but do not trigger
# the Yellow floor. See _quote_floor for the calibration data.
_CLOSE_FLOOR_MAX_SIM = 0.75


def _quote_floor(results: list[dict]) -> str:
    """SS6.4 deterministic floor over per-quote verdicts.

    A FABRICATED quote, or a CLOSE quote below the near-verbatim band,
    caps the claim at Yellow (apply-assessments enforces it: the agent
    can lower a color, never raise it past the floor; offline scoring
    models the same rule). CLOSE in [0.75, 0.85) does NOT floor: that
    band is dominated by transcription noise and bracket alterations
    (Withers calibration 2026-06-11 -- flooring it over-flagged a true
    green whose quotes scored 0.79/0.80 while the real misquote catches
    sat at 0.64/0.73). The CLOSE verdict still shows in the report.
    """
    for r in results:
        if r["result"] == "FABRICATED":
            return "Yellow"
        if (r["result"] == "CLOSE"
                and r["similarity"] < _CLOSE_FLOOR_MAX_SIM):
            return "Yellow"
    return ""


def check_quotes(workdir: Path) -> QuoteCheckStats:
    """Check quoted text in claims against opinion files.

    Reads claims.csv, checks each quoted_text entry against the opinion,
    writes quote_check and quote_check_worst columns back to claims.csv.
    """
    workdir = Path(workdir)
    claims_path = workdir / "claims.csv"
    stats = QuoteCheckStats()

    with open(claims_path, newline="", encoding="utf-8") as f:
        claims = list(csv.DictReader(f))

    # Per-opinion OCR gate (CL extracted_by_ocr, carried via the download
    # manifest). Absent/unknown -> rules stay off (default behavior).
    ocr_manifest = _read_ocr_manifest(workdir)

    # Cache opinion text by file path (HTML-stripped for clean matching)
    opinion_cache: dict[str, str] = {}

    for claim in claims:
        stats.total_claims += 1
        quoted_raw = claim.get("quoted_text", "[]")
        opinion_file = claim.get("opinion_file", "")

        try:
            quotes = json_mod.loads(quoted_raw) if quoted_raw else []
        except (json_mod.JSONDecodeError, TypeError):
            quotes = []

        # SS6.4: when the input carries no quotes, derive >=2-word
        # double-quoted spans from the claim's own text (prepared-pairs
        # front ends often supply only the proposition). The derived list
        # is written back so downstream phases and the report see it.
        if not quotes:
            derived: dict[str, None] = {}
            for source in (claim.get("proposition", ""),
                           claim.get("brief_sentence", "")):
                for span in extract_quoted_spans(source):
                    derived.setdefault(span)
            if derived:
                quotes = list(derived)
                claim["quoted_text"] = json_mod.dumps(quotes)
                stats.derived_quotes += 1

        if not quotes:
            claim["quote_check"] = "[]"
            claim["quote_check_worst"] = "NO_QUOTES"
            claim["quote_floor"] = ""
            stats.no_quotes += 1
            continue

        if not opinion_file:
            claim["quote_check"] = "[]"
            claim["quote_check_worst"] = "NO_OPINION"
            claim["quote_floor"] = ""
            stats.no_opinion += 1
            continue

        # Load opinion text (strip HTML for clean matching + extraction)
        if opinion_file not in opinion_cache:
            opinion_path = workdir / opinion_file
            try:
                raw = opinion_path.read_text(encoding="utf-8")
            except (FileNotFoundError, UnicodeDecodeError):
                claim["quote_check"] = "[]"
                claim["quote_check_worst"] = "NO_OPINION"
                claim["quote_floor"] = ""
                stats.no_opinion += 1
                continue
            # Strip HTML/XML tags and collapse whitespace for clean text
            clean = re.sub(r"<[^>]+>", " ", raw)
            clean = re.sub(r"&\w+;", " ", clean)  # HTML entities
            clean = re.sub(r"\s+", " ", clean).strip()
            opinion_cache[opinion_file] = clean
        opinion_text = opinion_cache[opinion_file]

        # OCR gate per opinion: only collapse rn/O/l when CL flagged this
        # opinion as OCR-extracted. Unknown -> off (byte-identical to before).
        was_ocrd = bool(ocr_manifest.get(Path(opinion_file).name, False))

        # Check each quote
        results = []
        worst = "VERBATIM"
        _WORST_ORDER = {"VERBATIM": 0, "CLOSE": 1, "FABRICATED": 2}

        for quote in quotes:
            qv = verify_quote(quote, opinion_text, was_ocrd=was_ocrd)
            result = qv.result.value
            if result == "VERBATIM":
                stats.verbatim += 1
            elif result == "CLOSE":
                stats.close += 1
            else:
                stats.fabricated += 1

            entry: dict[str, object] = {
                "quote": quote,
                "result": result,
                "similarity": qv.similarity,
            }
            if qv.matched_passage:
                entry["matched_passage"] = qv.matched_passage
            results.append(entry)

            if _WORST_ORDER.get(result, 0) > _WORST_ORDER.get(worst, 0):
                worst = result

        claim["quote_check"] = json_mod.dumps(results)
        claim["quote_check_worst"] = worst
        claim["quote_floor"] = _quote_floor(results)
        stats.checked += 1

    # Write updated claims.csv
    if claims:
        all_fields = list(claims[0].keys())
        for col in ("quote_check", "quote_check_worst", "quote_floor"):
            if col not in all_fields:
                all_fields.append(col)

        with open(claims_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_fields)
            writer.writeheader()
            writer.writerows(claims)

    return stats


# ---------------------------------------------------------------------------
# Metadata sanity check
# ---------------------------------------------------------------------------

@dataclass
class MetadataCheckResult:
    """Results from metadata sanity check."""
    total_claims: int = 0
    name_mismatches: int = 0
    not_found: int = 0
    no_opinion: int = 0
    flagged_claims: list[dict] = field(default_factory=list)
    syllabus_items: list[dict] = field(default_factory=list)


def metadata_check(workdir: Path) -> MetadataCheckResult:
    """Check verification metadata for obvious problems before assessment.

    Flags:
    - Case name mismatches (CL returned a different case)
    - NOT_FOUND citations (no opinion available)
    - Claims with no opinion file (can't assess)

    Also surfaces syllabus data alongside propositions so the skill
    orchestrator (LLM) can flag obvious topic mismatches during triage.
    """
    workdir = Path(workdir)
    claims_path = workdir / "claims.csv"
    result = MetadataCheckResult()

    with open(claims_path, newline="", encoding="utf-8") as f:
        claims = list(csv.DictReader(f))

    for claim in claims:
        result.total_claims += 1
        cl_status = claim.get("cl_status", "")
        diagnostics = claim.get("diagnostics", "")
        opinion_file = claim.get("opinion_file", "")
        syllabus = claim.get("syllabus", "")

        flags = []

        # Name mismatch: diagnostics contain "name mismatch"
        if "name mismatch" in diagnostics.lower() or "Name mismatch" in diagnostics:
            result.name_mismatches += 1
            flags.append("name_mismatch")

        # NOT_FOUND
        if cl_status == "NOT_FOUND":
            result.not_found += 1
            flags.append("not_found")

        # No opinion available
        if not opinion_file and cl_status not in ("NOT_FOUND", ""):
            result.no_opinion += 1
            flags.append("no_opinion")

        if flags:
            result.flagged_claims.append({
                "cited_case": claim.get("cited_case", ""),
                "page": claim.get("page", ""),
                "proposition": claim.get("proposition", ""),
                "syllabus": syllabus,
                "flags": flags,
            })

        # Surface syllabus for LLM triage (even if no other flags)
        if syllabus:
            result.syllabus_items.append({
                "cited_case": claim.get("cited_case", ""),
                "page": claim.get("page", ""),
                "proposition": claim.get("proposition", ""),
                "syllabus": syllabus,
            })

    return result


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

# Lane -> severity token, shared by generate_report (finding cards) and the
# findings.json data model so the two never drift. Red/Yellow/CheckCite are
# the finding severities the HTML template already used; Green/Gray extend
# the same vocabulary to every claim.
_LANE_SEVERITY = {
    "Red": "red", "Yellow": "yellow", "CheckCite": "orange",
    "Green": "green", "Gray": "gray",
}


def _lane_severity(lane: str) -> str:
    return _LANE_SEVERITY.get(lane, "")


def _claim_badge(claim: dict, lane: str) -> str:
    """Effective badge label for a claim under its report lane. Mirrors the
    generate_report card logic exactly so findings.json matches the HTML.

      * Green  -> "Supported"
      * Gray   -> "" (gray cards render an explanation, not a badge)
      * CheckCite -> the forced Check Cite label (lane wins over any agent
        badge)
      * Red/Yellow -> agent badge, else status fallback, else severity default
    """
    from .scoring import CHECK_CITE, GRAY, GREEN

    if lane == GREEN:
        return "Supported"
    if lane == GRAY:
        return ""
    if lane == CHECK_CITE:
        return _STATUS_BADGE_FALLBACK["CITE_UNCONFIRMED"]
    severity = _lane_severity(lane)
    return (
        claim.get("badge_label", "").strip()
        or _STATUS_BADGE_FALLBACK.get(claim.get("cl_status", ""))
        or ("Not supported by cited case" if severity == "red"
            else "Overstated -- case partially supports")
    )


def _build_findings_model(workdir: Path) -> list[dict]:
    """The report's per-claim data model as plain dicts (one per claims.csv
    row, in order) for findings.json. Stable machine keys; lane/severity are
    enum-ish tokens. Downstream tools read this instead of scraping the HTML."""
    from .scoring import report_lane

    workdir = Path(workdir)
    with open(workdir / "claims.csv", newline="", encoding="utf-8") as f:
        claims = list(csv.DictReader(f))
    out: list[dict] = []
    for c in claims:
        lane = report_lane(c.get("cl_status", ""),
                           c.get("assessment", "").strip(),
                           c.get("opinion_file", ""))
        out.append({
            "claim_id": c.get("claim_id", ""),
            "lane": lane,
            "severity": _lane_severity(lane),
            "badge_label": _claim_badge(c, lane),
            "brief_block": c.get("brief_block", "").strip(),
            "opinion_block": c.get("opinion_block", "").strip(),
            "cl_url": c.get("cl_url", ""),
        })
    return out


def generate_report(
    workdir: Path,
    title: str = "",
    case_name: str = "",
    case_number: str = "",
    filed_date: str = "",
    report_date: str = "",
) -> Path:
    """Generate an HTML report from claims.csv assessment data.

    Reads claims.csv (must have assessment column populated),
    builds the report data structure, and writes report.html.

    Returns the path to the generated report.
    """
    from .scoring import GRAY, GREEN, report_lane

    workdir = Path(workdir)
    claims_path = workdir / "claims.csv"

    with open(claims_path, newline="", encoding="utf-8") as f:
        claims = list(csv.DictReader(f))

    findings = []
    verified = []
    unable_by_citation: dict[str, dict] = {}
    retrieved_set: dict[str, dict[str, str]] = {}
    unavailable_list = []
    finding_counter = 0
    # Minimum similarity for a deterministic matched passage to be shown.
    # Below this, the match is typically junk pulled from an unrelated part
    # of the opinion — worse than showing nothing, because it misleads the
    # reader into thinking the matcher found something relevant.
    _MATCH_PASSAGE_MIN_SIM = 0.65

    for claim in claims:
        assessment = claim.get("assessment", "").strip()
        cl_status = claim.get("cl_status", "")
        page = claim.get("page", "")
        proposition = claim.get("proposition", "")
        cited_case = claim.get("cited_case", "")
        cl_url = claim.get("cl_url", "")
        retrieved_case = claim.get("retrieved_case", "")
        supporting_lang = claim.get("supporting_language", "")
        opinion_file = claim.get("opinion_file", "")

        # Parse case name and citation from cited_case
        parts = cited_case.split(",", 1)
        case_name_parsed = parts[0].strip() if parts else cited_case
        citation_parsed = parts[1].strip() if len(parts) > 1 else ""

        # Track retrieved opinions
        if opinion_file and retrieved_case:
            cluster_id = ""
            if cl_url:
                m = re.search(r"/opinion/(\d+)/", cl_url)
                if m:
                    cluster_id = m.group(1)
            retrieved_set[retrieved_case] = {
                "case_name": retrieved_case,
                "citation": citation_parsed,
                "cluster_id": cluster_id,
            }

        # SS6.9 lane routing (Step 7): report_lane resolves the v1-schema
        # tension -- existence lanes (WRONG_CASE, CITE_UNCONFIRMED,
        # unlocatable) beat the assessment column; otherwise the
        # floor-enforced assessment is authoritative.
        lane = report_lane(cl_status, assessment, opinion_file)
        flag_lines = _crosscheck_flag_lines(claim)

        if lane == GREEN:
            verified.append({
                "page": page,
                "case_name": case_name_parsed,
                "citation": citation_parsed,
                "cl_url": cl_url,
                "proposition": proposition,
                "badge_label": "Supported",
                "supporting_language": supporting_lang,
                "crosscheck_flags": flag_lines,
            })
        elif lane == GRAY:
            explanation, reason = _UNLOCATABLE_EXPLANATIONS.get(
                cl_status, _UNLOCATABLE_EXPLANATIONS["NOT_FOUND"])
            # Group by cited_case so the same unavailable case cited for
            # multiple propositions collapses into one card.
            group_key = cited_case or f"{case_name_parsed}|{citation_parsed}"
            card = unable_by_citation.get(group_key)
            if not card:
                finding_counter += 1
                card = {
                    "id": f"finding-uv-{finding_counter}",
                    "page": page,
                    "case_name": case_name_parsed,
                    "citation": citation_parsed,
                    "propositions": [],
                    "explanation": (
                        supporting_lang if supporting_lang
                        else explanation
                    ),
                }
                unable_by_citation[group_key] = card
                unavailable_list.append({
                    "case_name": case_name_parsed,
                    "citation": citation_parsed,
                    "reason": reason,
                })
            card["propositions"].append({
                "page": page,
                "proposition": proposition,
                "quoted_text": claim.get("quoted_text", ""),
            })
        else:
            # Red / Yellow / CheckCite finding card
            finding_counter += 1
            severity = _lane_severity(lane)

            quoted_raw = claim.get("quoted_text", "").strip()
            quoted_strings: list[str] = []
            if quoted_raw and quoted_raw != "[]":
                try:
                    quoted_strings = json_mod.loads(quoted_raw)
                except (json_mod.JSONDecodeError, ValueError):
                    pass

            brief_sentence = claim.get("brief_sentence", "").strip()

            # Deterministic matched passages from quote_check. Only show
            # passages that cleared the similarity floor — below it, the
            # matcher is guessing and the passage is usually junk.
            matched_passages = []
            quote_check_raw = claim.get("quote_check", "[]")
            try:
                quote_checks = json_mod.loads(quote_check_raw) if quote_check_raw else []
            except (json_mod.JSONDecodeError, ValueError):
                quote_checks = []
            for qc in quote_checks:
                sim = qc.get("similarity", 0)
                if qc.get("matched_passage") and sim >= _MATCH_PASSAGE_MIN_SIM:
                    matched_passages.append({
                        "text": qc["matched_passage"],
                        "similarity": sim,
                        "result": qc.get("result", ""),
                    })

            # Agent-authored narrative. Prefer the new single-field schema;
            # fall back to composing from the legacy two-field schema so old
            # briefs can still regenerate their reports.
            finding_analysis = claim.get("finding_analysis", "").strip()
            if not finding_analysis:
                legacy_overview = claim.get("opinion_text", "").strip()
                legacy_explanation = claim.get("explanation", "").strip()
                parts_legacy = [p for p in (legacy_overview, legacy_explanation) if p]
                finding_analysis = "\n\n".join(parts_legacy)

            # Badge for the finding card. Shared with findings.json via
            # _claim_badge so the two never drift. For a Check Cite card the
            # lane label wins over any agent badge; the agent's
            # blocks/analysis still render below.
            badge_label = _claim_badge(claim, lane)

            # Agent-authored quote blocks (optional). When present they
            # replace the deterministic fallbacks in the template.
            brief_block = claim.get("brief_block", "").strip()
            opinion_block = claim.get("opinion_block", "").strip()

            findings.append({
                "id": f"finding-{finding_counter}",
                "page": page,
                "case_name": case_name_parsed,
                "citation": citation_parsed,
                "cl_url": cl_url,
                "severity": severity,
                "badge_label": badge_label,
                # Agent-authored blocks (preferred when present)
                "brief_block": brief_block,
                "opinion_block": opinion_block,
                # Deterministic inputs (fallback / reference)
                "brief_sentence": brief_sentence,
                "proposition": proposition,
                "quoted_strings": quoted_strings,
                "matched_passages": matched_passages,
                "finding_analysis": finding_analysis,
                "crosscheck_flags": flag_lines,
            })

    report_data = {
        "title": title,
        "case_name": case_name,
        "case_number": case_number,
        "filed_date": filed_date,
        "report_date": report_date,
        "findings": findings,
        "verified": verified,
        "unable_to_verify": list(unable_by_citation.values()),
        "retrieved_opinions": list(retrieved_set.values()),
        "unavailable_opinions": unavailable_list,
    }

    html = generate_report_html(report_data)
    report_path = workdir / "report.html"
    report_path.write_text(html, encoding="utf-8")
    return report_path
