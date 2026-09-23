"""Citation Verifier web application — FastAPI + SSE streaming."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import random
import re
import shutil
import subprocess
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import certifi
import ssl as _ssl
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from citation_verifier.cache import VerificationCache
from citation_verifier.client import AsyncCourtListenerClient
from citation_verifier.models import (
    ParsedCitation,
    Status,
    VerificationResult,
)
from citation_verifier.name_matcher import CaseNameMatcher
from citation_verifier.verifier import CitationVerifier

# Status -> display label / CSS class.  All v0.3 statuses are emitted in
# production.  Frontend JS in web/static/{get,index,qc}.html switches
# cover the full v0.3 enum (see tests/test_frontend_status_coverage.py).
# Legacy v0.2 names (LIKELY_REAL, POSSIBLE_MATCH) still appear in old
# on-disk JSON sidecars under tests/data/results/ and in the master CSV's
# pre-v0.3 rows; the QC page's badgeClass/statusLabel keep cases for them
# so historical data renders correctly.  The API only emits v0.3.
_STATUS_DISPLAY = {
    Status.VERIFIED: ("[OK] VERIFIED", "verified"),
    Status.VERIFIED_PARTIAL: ("[OK] VERIFIED (partial)", "verified-partial"),
    Status.VERIFIED_VIA_RECAP: ("[OK] VERIFIED (RECAP)", "verified-recap"),
    Status.VERIFIED_DOCKET_ONLY: ("[OK] VERIFIED (docket only)", "verified-docket"),
    Status.WRONG_CASE: ("[!] WRONG CASE", "wrong-case"),
    Status.NOT_FOUND: ("[X] NOT FOUND", "not-found"),
    Status.VERIFICATION_INCOMPLETE: ("[?] VERIFICATION INCOMPLETE", "verification-incomplete"),
}

logger = logging.getLogger(__name__)



def _get_api_token(request: Request) -> str | None:
    """Extract CourtListener API token from request header (BYOK)."""
    return request.headers.get("X-CL-API-Token") or None

# Paths
_project_root = Path(__file__).parent.parent
_results_dir = _project_root / "tests" / "data" / "results"
_default_csv = _project_root / "scratch" / "citations_for_review.csv"


# ---------------------------------------------------------------------------
# MasterCSV — lazy-loading CSV helper with QC read/write and dupe detection
# ---------------------------------------------------------------------------

class MasterCSV:
    """Lazy-loading wrapper around the master citations CSV."""

    def __init__(self, csv_path: Path | None = None):
        self._path = csv_path or _default_csv
        self._rows: list[dict] | None = None
        self._fieldnames: list[str] = []
        self._mtime: float = 0.0
        self._matcher = CaseNameMatcher()

    def _load(self) -> None:
        """Load (or reload) the CSV if the file has changed."""
        try:
            current_mtime = self._path.stat().st_mtime
        except OSError:
            self._rows = []
            self._fieldnames = []
            return
        if self._rows is not None and current_mtime == self._mtime:
            return
        with open(self._path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            self._fieldnames = list(reader.fieldnames or [])
            self._rows = list(reader)
        self._mtime = current_mtime

    @property
    def rows(self) -> list[dict]:
        self._load()
        return self._rows or []

    def get_row(self, citation_text: str) -> dict | None:
        """Find a row by exact citation_text match."""
        for row in self.rows:
            if row.get("citation_text", "").strip() == citation_text.strip():
                return row
        return None

    def update_qc(
        self, citation_text: str, qc_status: str, qc_notes: str
    ) -> bool:
        """Update qc_status and qc_notes for a citation row. Returns True on success."""
        self._load()
        if not self._rows:
            return False

        target = citation_text.strip()
        found = False
        for row in self._rows:
            if row.get("citation_text", "").strip() == target:
                row["qc_status"] = qc_status
                row["qc_notes"] = qc_notes
                found = True
                break

        if not found:
            return False

        # Backup then write
        bak = self._path.with_suffix(".csv.bak")
        shutil.copy2(self._path, bak)

        with open(self._path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self._fieldnames)
            writer.writeheader()
            for row in self._rows:
                writer.writerow(row)

        self._mtime = self._path.stat().st_mtime
        return True

    def find_duplicates(
        self,
        case_name: str | None,
        volume: str | None,
        reporter: str | None,
        page: str | None,
        exclude_citation: str | None = None,
    ) -> list[dict]:
        """Find potential duplicate rows using name similarity and volume/reporter/page proximity."""
        dupes: list[dict] = []
        exclude = (exclude_citation or "").strip()

        for row in self.rows:
            row_cite = row.get("citation_text", "").strip()
            if row_cite == exclude:
                continue
            # Only compare against rows that have been verified
            if not row.get("v_status"):
                continue

            score = 0.0
            reason = ""

            # Name similarity
            row_name = row.get("case_name", "")
            if case_name and row_name:
                name_sim = self._matcher.calculate_similarity(case_name, row_name)
                if name_sim >= 0.75:
                    score = max(score, name_sim)
                    reason = "name"

            # Volume+reporter+page proximity
            if (
                volume and reporter and page
                and row.get("volume") == volume
                and row.get("reporter") == reporter
            ):
                try:
                    page_diff = abs(int(page) - int(row.get("page", "0")))
                except (ValueError, TypeError):
                    page_diff = 9999
                if page_diff == 0:
                    score = max(score, 0.99)
                    reason = "exact_cite"
                elif page_diff <= 50:
                    score = max(score, 0.92)
                    reason = "pin_cite"

            if score >= 0.75:
                dupes.append({
                    "citation_text": row_cite,
                    "case_name": row_name,
                    "v_status": row.get("v_status", ""),
                    "qc_status": row.get("qc_status", ""),
                    "similarity": round(score, 3),
                    "reason": reason,
                    "tier": "high" if score >= 0.90 else "possible",
                })

        # Sort by similarity descending
        dupes.sort(key=lambda d: d["similarity"], reverse=True)
        return dupes[:10]


app = FastAPI(title="Citation Verifier", version="0.1.0")

# Public mode: when MODE=public, only serve the Get & Print page (hosted on Render).
_public_mode = os.environ.get("MODE", "").lower() == "public"
MAX_CITATIONS = 500 if _public_mode else 0  # 0 = no limit

if _public_mode:
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import Response as StarletteResponse

    class _BlockQCMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            path = request.url.path
            if path in ("/qc", "/debug", "/api/flag-for-flp") or path.startswith("/api/qc"):
                return StarletteResponse("Not Found", status_code=404)
            return await call_next(request)

    app.add_middleware(_BlockQCMiddleware)

# Mount static files
_static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# Shared instances
_cache = VerificationCache()
_verifier = CitationVerifier()
_master_csv = MasterCSV()


def _matched_case_name(result: VerificationResult) -> str | None:
    """Pull the matched case name from the final resolution_path entry.

    Mirrors __main__._matched_case_name — Phase 1 stashes the case name in
    ``resolution_path[-1].raw_response_summary["case_name"]``.
    """
    if not result.resolution_path:
        return None
    summary = result.resolution_path[-1].raw_response_summary or {}
    return summary.get("case_name") or None


def _stage_notes(result: VerificationResult) -> str:
    """Concatenated free-form notes for the resolving stage, if any."""
    if not result.resolution_path:
        return ""
    return result.resolution_path[-1].notes or ""


def _legacy_diagnostics(result: VerificationResult) -> list[dict[str, str]]:
    """Synthesize the legacy ``diagnostics`` list (category/message dicts) from
    v0.3 warnings + the resolving stage's notes. The frontend still reads
    ``diagnostics`` as a list of {category, message} objects, so emit it
    alongside the new ``warnings`` key for backward compatibility.
    """
    out: list[dict[str, str]] = []
    for w in result.warnings or []:
        out.append({"category": w.category.value, "message": w.message})
    notes = _stage_notes(result)
    if notes:
        for note in notes.split("; "):
            note = note.strip()
            if note:
                out.append({"category": "info", "message": note})
    return out


def _result_to_dict(result: VerificationResult) -> dict[str, Any]:
    """Serialize a VerificationResult for the API/frontend.

    Schema notes:
    - ``confidence`` is None when no resolved/partial stage exists. The
      frontend formats `(confidence * 100).toFixed(0) + '%'` and guards on
      `confidence != null`, so emitting null is fine.
    - ``matched_court``/``matched_date``/``matched_description`` are dropped
      from VerificationResult in v0.3. We fall back to the *cited* values
      from parsed_citation for shape stability; this is the cited court
      (e.g. "M.D. Ala.") rather than the CL court ID the old shape had.
    - ``warnings`` is the new structured channel; ``diagnostics`` is
      synthesized for backward compatibility with the existing frontend JS.
    """
    court = None
    date = None
    if result.parsed_citation:
        court = result.parsed_citation.court
        if result.parsed_citation.year is not None:
            date = str(result.parsed_citation.year)

    return {
        # Renamed in v0.3 (input_citation -> citation_as_written), but the
        # frontend reads `input_citation`, so emit both.
        "input_citation": result.citation_as_written,
        "citation_as_written": result.citation_as_written,
        "status": result.status.value,
        "confidence": result.headline_confidence,
        "matched_case_name": _matched_case_name(result),
        "matched_url": result.final_ids.absolute_url,
        # v0.3 doesn't carry CL court/date/description on the result; fall
        # back to the cited values from parsed_citation for shape stability.
        "matched_court": court,
        "matched_date": date,
        "matched_description": None,
        # Backward-compat: frontend JS reads `diagnostics` as a list of
        # {category, message} objects. Synthesize from warnings + stage notes.
        "diagnostics": _legacy_diagnostics(result),
        # New structured channels (Phase 2+ consumers).
        "warnings": [
            {
                "category": w.category.value,
                "message": w.message,
                "details": w.details,
            }
            for w in result.warnings or []
        ],
        "stage_notes": _stage_notes(result) or None,
        # Dropped in v0.3; emit for shape stability (matches __main__.py).
        "error": None,
    }


from fastapi.responses import RedirectResponse


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the Retrieve page as the homepage."""
    html_path = _static_dir / "get.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/get")
async def get_redirect():
    """Legacy /get URL redirects to /."""
    return RedirectResponse("/")


if not _public_mode:
    @app.get("/debug", response_class=HTMLResponse)
    async def debug_page():
        """Serve the Debug (detailed verification) page."""
        html_path = _static_dir / "index.html"
        return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/api/health")
async def health():
    """Health check — reports token presence and cache size."""
    has_token = bool(os.environ.get("COURTLISTENER_API_TOKEN", ""))
    return {
        "status": "ok",
        "has_api_token": has_token,
        "cache_size": len(_cache),
    }


@app.post("/api/verify")
async def verify(request: Request):
    """Verify citations via SSE stream.

    Accepts JSON body: {"citations": ["cite1", "cite2", ...]}
    Streams SSE events: start, result, progress, done, error.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": "Invalid JSON body"}, status_code=400
        )

    citations = body.get("citations", [])
    if not isinstance(citations, list):
        return JSONResponse(
            {"error": "citations must be a list"}, status_code=400
        )

    # Filter empty strings
    citations = [c.strip() for c in citations if c.strip()]

    if not citations:
        return JSONResponse(
            {"error": "No citations provided"}, status_code=400
        )

    if MAX_CITATIONS and len(citations) > MAX_CITATIONS:
        return JSONResponse(
            {"error": f"Maximum {MAX_CITATIONS} citations per request"},
            status_code=400,
        )

    token = _get_api_token(request)
    mode = body.get("mode", "full")
    quick_only = mode == "quick"

    async def event_generator():
        yield {
            "event": "start",
            "data": json.dumps({"total": len(citations)}),
        }

        # Single batched citation_lookup for hits; misses fan out to the
        # opinion-search + RECAP fallback inside verify_batch.  Results are
        # collected then streamed as a burst — UX trades the per-citation
        # trickle of "result" events (~15-25s for 10 cites) for one shared
        # round-trip (~3-5s for 10 cites) plus a single render pass.  See
        # plans/steady-wandering-eich.md for the trade-off rationale.
        async with AsyncCourtListenerClient(api_token=token) as client:
            try:
                results = await _verifier.verify_batch(
                    citations,
                    quick_only=quick_only,
                    client=client,
                )
            except Exception as exc:
                logger.exception("verify_batch failed for %d citations", len(citations))
                yield {
                    "event": "error",
                    "data": json.dumps({"error": str(exc)}),
                }
                return

        for i, (citation_text, result) in enumerate(zip(citations, results)):
            if not quick_only or result.status != Status.NOT_FOUND:
                _cache.put(citation_text, result)
            result_dict = _result_to_dict(result)
            result_dict["index"] = i
            result_dict["cached"] = False
            yield {
                "event": "result",
                "data": json.dumps(result_dict),
            }
            yield {
                "event": "progress",
                "data": json.dumps({
                    "completed": i + 1,
                    "total": len(citations),
                }),
            }

        yield {"event": "done", "data": json.dumps({"total": len(citations)})}

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# PDF download endpoints
# ---------------------------------------------------------------------------

def _sanitize_filename(name: str) -> str:
    """Turn a case name into a safe filename (ASCII, no special chars)."""
    # Replace common legal abbreviations
    name = name.replace("/", " v ")
    # Keep only alphanumeric, spaces, hyphens, periods
    name = re.sub(r"[^\w\s\-.]", "", name)
    # Collapse whitespace
    name = re.sub(r"\s+", " ", name).strip()
    # Truncate
    if len(name) > 80:
        name = name[:80].rsplit(" ", 1)[0]
    return name or "document"


@app.post("/api/download-pdfs")
async def download_pdfs(request: Request):
    """Download PDFs for the given matched_urls, returned as a zip file.

    Accepts JSON body: {"urls": [{"matched_url": "...", "case_name": "..."}, ...]}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    items = body.get("urls", [])
    if not isinstance(items, list) or not items:
        return JSONResponse({"error": "No URLs provided"}, status_code=400)

    if len(items) > 50:
        return JSONResponse(
            {"error": "Maximum 50 PDFs per download"}, status_code=400
        )

    # Phase 1: Resolve matched_urls to PDF download URLs (parallel, rate-limited)
    async def _resolve_one(
        client: AsyncCourtListenerClient, item: dict,
    ) -> dict[str, str | None]:
        matched_url = item.get("matched_url", "")
        case_name = item.get("case_name", "document")
        pdf_url = await client.get_pdf_url(matched_url)
        logger.info(
            "PDF resolve: %s -> %s",
            matched_url, pdf_url or "NO PDF URL",
        )
        return {"pdf_url": pdf_url, "case_name": case_name, "matched_url": matched_url}

    token = _get_api_token(request)

    async with AsyncCourtListenerClient(api_token=token) as client:
        resolved = await asyncio.gather(
            *[_resolve_one(client, item) for item in items]
        )

    # Phase 2: Download PDFs in parallel (storage.courtlistener.com, not CL API)
    download_sem = asyncio.Semaphore(5)
    ssl_ctx = _ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)

    async def _download_one(
        session: aiohttp.ClientSession, entry: dict,
    ) -> tuple[str, bytes | None]:
        """Return (case_name, pdf_bytes or None)."""
        case_name = entry["case_name"] or "document"
        pdf_url = entry["pdf_url"]
        if not pdf_url:
            return (case_name, None)
        async with download_sem:
            try:
                async with session.get(
                    pdf_url,
                    timeout=aiohttp.ClientTimeout(total=30),
                    allow_redirects=True,
                ) as resp:
                    if resp.status != 200:
                        logger.info("PDF download %s: HTTP %s", pdf_url, resp.status)
                        return (case_name, None)
                    content_type = resp.content_type or ""
                    if "html" in content_type:
                        logger.info("PDF download %s: got HTML instead of PDF", pdf_url)
                        return (case_name, None)
                    return (case_name, await resp.read())
            except Exception as exc:
                logger.info("PDF download %s: %s", pdf_url, exc)
                return (case_name, None)

    buf = io.BytesIO()
    downloaded = 0
    skipped_names: list[str] = []

    async with aiohttp.ClientSession(connector=connector) as session:
        results = await asyncio.gather(
            *[_download_one(session, entry) for entry in resolved]
        )

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        seen_filenames: set[str] = set()
        for case_name, pdf_bytes in results:
            if pdf_bytes is None:
                skipped_names.append(case_name)
                continue
            base = _sanitize_filename(case_name)
            filename = f"{base}.pdf"
            counter = 2
            while filename in seen_filenames:
                filename = f"{base} ({counter}).pdf"
                counter += 1
            seen_filenames.add(filename)
            zf.writestr(filename, pdf_bytes)
            downloaded += 1

    if downloaded == 0:
        return JSONResponse(
            {"error": f"No PDFs available for the selected citations ({len(skipped_names)} skipped — opinions may be HTML-only, dockets may lack documents)"},
            status_code=404,
        )

    buf.seek(0)
    headers = {
        "Content-Disposition": 'attachment; filename="citation_pdfs.zip"',
        "x-downloaded": str(downloaded),
        "x-skipped": str(len(skipped_names)),
        "Access-Control-Expose-Headers": "x-downloaded, x-skipped",
    }
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Text download endpoint
# ---------------------------------------------------------------------------

@app.post("/api/download-texts")
async def download_texts(request: Request):
    """Download opinion texts for the given matched_urls, returned as a zip of .txt files.

    Accepts JSON body: {"urls": [{"matched_url": "...", "case_name": "..."}, ...]}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    items = body.get("urls", [])
    if not isinstance(items, list) or not items:
        return JSONResponse({"error": "No URLs provided"}, status_code=400)

    if len(items) > 50:
        return JSONResponse(
            {"error": "Maximum 50 texts per download"}, status_code=400
        )

    # Fetch opinion text + metadata for each URL (parallel)
    async def _fetch_one(
        client: AsyncCourtListenerClient, item: dict,
    ) -> dict[str, Any]:
        matched_url = item.get("matched_url", "")
        case_name = item.get("case_name", "document")
        result = await client.get_opinion_text_with_metadata(matched_url)
        logger.info(
            "Text resolve: %s -> %s chars",
            matched_url, len(result["text"]) if result else 0,
        )
        if result:
            # Prefer the caller's case_name if the API didn't return one
            if not result.get("case_name"):
                result["case_name"] = case_name
            result["matched_url"] = matched_url
        else:
            result = {"text": None, "case_name": case_name, "matched_url": matched_url}
        return result

    token = _get_api_token(request)

    async with AsyncCourtListenerClient(api_token=token) as client:
        fetched = await asyncio.gather(
            *[_fetch_one(client, item) for item in items]
        )

    # Assemble zip of .txt files
    buf = io.BytesIO()
    downloaded = 0
    skipped_names: list[str] = []

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        seen_filenames: set[str] = set()
        for entry in fetched:
            case_name = entry.get("case_name") or "document"
            text = entry.get("text")
            if not text:
                skipped_names.append(case_name)
                continue

            # Build file content with rich metadata header
            lines = []
            lines.append(case_name)
            citations = entry.get("citations", [])
            if citations:
                lines.append(", ".join(citations))
            court = entry.get("court", "")
            if court:
                lines.append(court)
            date_filed = entry.get("date_filed", "")
            if date_filed:
                lines.append(f"Filed: {date_filed}")
            docket_number = entry.get("docket_number", "")
            if docket_number:
                lines.append(f"Docket No. {docket_number}")
            lines.append(f"Source: {entry.get('matched_url', '')}")
            lines.append("-" * 60)
            lines.append("")
            header = "\n".join(lines) + "\n"
            content = header + text

            base = _sanitize_filename(case_name)
            filename = f"{base}.txt"
            counter = 2
            while filename in seen_filenames:
                filename = f"{base} ({counter}).txt"
                counter += 1
            seen_filenames.add(filename)
            zf.writestr(filename, content)
            downloaded += 1

    if downloaded == 0:
        return JSONResponse(
            {"error": f"No text available for the selected citations ({len(skipped_names)} skipped -- no text on CourtListener)"},
            status_code=404,
        )

    buf.seek(0)
    headers = {
        "Content-Disposition": 'attachment; filename="citation_texts.zip"',
        "x-downloaded": str(downloaded),
        "x-skipped": str(len(skipped_names)),
        "Access-Control-Expose-Headers": "x-downloaded, x-skipped",
    }
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Download HTML
# ---------------------------------------------------------------------------

@app.post("/api/download-htmls")
async def download_htmls(request: Request):
    """Download opinion HTML for the given matched_urls, returned as a zip of .html files.

    Uses prefer_html=True to get formatted HTML with footnotes when available.
    Accepts JSON body: {"urls": [{"matched_url": "...", "case_name": "..."}, ...]}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    items = body.get("urls", [])
    if not isinstance(items, list) or not items:
        return JSONResponse({"error": "No URLs provided"}, status_code=400)

    if len(items) > 50:
        return JSONResponse(
            {"error": "Maximum 50 files per download"}, status_code=400
        )

    # Fetch opinion HTML + metadata for each URL (parallel)
    async def _fetch_one(
        client: AsyncCourtListenerClient, item: dict,
    ) -> dict[str, Any]:
        matched_url = item.get("matched_url", "")
        case_name = item.get("case_name", "document")
        result = await client.get_opinion_text_with_metadata(
            matched_url, prefer_html=True,
        )
        # Fall back to plain text if prefer_html returned nothing
        if not result or not result.get("text"):
            result = await client.get_opinion_text_with_metadata(matched_url)
        logger.info(
            "HTML resolve: %s -> %s chars (format=%s)",
            matched_url,
            len(result["text"]) if result else 0,
            result.get("format") if result else None,
        )
        if result:
            if not result.get("case_name"):
                result["case_name"] = case_name
            result["matched_url"] = matched_url
        else:
            result = {"text": None, "case_name": case_name, "matched_url": matched_url}
        return result

    token = _get_api_token(request)

    async with AsyncCourtListenerClient(api_token=token) as client:
        fetched = await asyncio.gather(
            *[_fetch_one(client, item) for item in items]
        )

    # Assemble zip of .html files
    buf = io.BytesIO()
    downloaded = 0
    skipped_names: list[str] = []

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        seen_filenames: set[str] = set()
        for entry in fetched:
            case_name = entry.get("case_name") or "document"
            text = entry.get("text")
            if not text:
                skipped_names.append(case_name)
                continue

            fmt = entry.get("format", "text")

            if fmt == "html":
                # Already HTML -- wrap with metadata header
                meta_parts = [f"<h1>{case_name}</h1>"]
                citations = entry.get("citations", [])
                if citations:
                    meta_parts.append(f"<p>{', '.join(citations)}</p>")
                court = entry.get("court", "")
                if court:
                    meta_parts.append(f"<p>{court}</p>")
                date_filed = entry.get("date_filed", "")
                if date_filed:
                    meta_parts.append(f"<p>Filed: {date_filed}</p>")
                docket_number = entry.get("docket_number", "")
                if docket_number:
                    meta_parts.append(f"<p>Docket No. {docket_number}</p>")
                meta_parts.append(
                    f"<p>Source: <a href=\"{entry.get('matched_url', '')}\">"
                    f"{entry.get('matched_url', '')}</a></p>"
                )
                meta_parts.append("<hr>")
                meta_header = "\n".join(meta_parts)
                content = (
                    "<!DOCTYPE html>\n<html><head>"
                    f"<meta charset=\"utf-8\"><title>{case_name}</title>"
                    "</head><body>\n"
                    f"{meta_header}\n{text}\n"
                    "</body></html>"
                )
            else:
                # Plain text -- convert to simple HTML
                lines = []
                lines.append(case_name)
                citations = entry.get("citations", [])
                if citations:
                    lines.append(", ".join(citations))
                court = entry.get("court", "")
                if court:
                    lines.append(court)
                date_filed = entry.get("date_filed", "")
                if date_filed:
                    lines.append(f"Filed: {date_filed}")
                docket_number = entry.get("docket_number", "")
                if docket_number:
                    lines.append(f"Docket No. {docket_number}")
                lines.append(f"Source: {entry.get('matched_url', '')}")
                lines.append("-" * 60)
                lines.append("")
                header = "\n".join(lines) + "\n"
                escaped_text = (header + text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                content = (
                    "<!DOCTYPE html>\n<html><head>"
                    f"<meta charset=\"utf-8\"><title>{case_name}</title>"
                    "</head><body>\n"
                    f"<pre>{escaped_text}</pre>\n"
                    "</body></html>"
                )

            base = _sanitize_filename(case_name)
            filename = f"{base}.html"
            counter = 2
            while filename in seen_filenames:
                filename = f"{base} ({counter}).html"
                counter += 1
            seen_filenames.add(filename)
            zf.writestr(filename, content)
            downloaded += 1

    if downloaded == 0:
        return JSONResponse(
            {"error": f"No HTML available for the selected citations ({len(skipped_names)} skipped -- no text on CourtListener)"},
            status_code=404,
        )

    buf.seek(0)
    headers = {
        "Content-Disposition": 'attachment; filename="citation_htmls.zip"',
        "x-downloaded": str(downloaded),
        "x-skipped": str(len(skipped_names)),
        "Access-Control-Expose-Headers": "x-downloaded, x-skipped",
    }
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Flag for FLP — save results for CourtListener issue evidence
# ---------------------------------------------------------------------------

_flp_csv = _project_root / "scratch" / "flp_findings.csv"
_FLP_COLUMNS = [
    "timestamp", "citation", "status", "confidence", "matched_url",
    "matched_case_name", "matched_court", "matched_date",
    "matched_description", "diagnostics",
]


@app.post("/api/flag-for-flp")
async def flag_for_flp(request: Request):
    """Append a flagged result to scratch/flp_findings.csv."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    # Create CSV with headers if it doesn't exist
    write_header = not _flp_csv.exists()
    with open(_flp_csv, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_FLP_COLUMNS)
        if write_header:
            writer.writeheader()
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "citation": body.get("citation", ""),
            "status": body.get("status", ""),
            "confidence": body.get("confidence", ""),
            "matched_url": body.get("matched_url", ""),
            "matched_case_name": body.get("matched_case_name", ""),
            "matched_court": body.get("matched_court", ""),
            "matched_date": body.get("matched_date", ""),
            "matched_description": body.get("matched_description", ""),
            "diagnostics": body.get("diagnostics", ""),
        }
        writer.writerow(row)

    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# QC review endpoints
# ---------------------------------------------------------------------------

@app.get("/qc", response_class=HTMLResponse)
async def qc_page():
    """Serve the QC review page."""
    html_path = _static_dir / "qc.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/api/qc/runs")
async def qc_runs():
    """List available JSON sidecar files from tests/data/results/."""
    if not _results_dir.exists():
        return []
    runs = []
    for p in sorted(_results_dir.glob("*.json"), reverse=True):
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            meta = data.get("_metadata", {})
            runs.append({
                "filename": p.name,
                "generated_at": meta.get("generated_at", ""),
                "sample_size": meta.get("sample_size", 0),
                "result_count": len(data.get("results", [])),
                "git_hash": meta.get("git_hash", ""),
                "seed": meta.get("seed", ""),
            })
        except (json.JSONDecodeError, OSError):
            continue
    return runs


@app.get("/api/qc/run/{filename}")
async def qc_run(filename: str):
    """Load a sidecar file, enriched with CSV QC data and fuzzy duplicates."""
    # Sanitize filename — only allow simple filenames
    if "/" in filename or "\\" in filename or ".." in filename:
        return JSONResponse({"error": "Invalid filename"}, status_code=400)
    sidecar_path = _results_dir / filename
    if not sidecar_path.exists():
        return JSONResponse({"error": "File not found"}, status_code=404)

    try:
        with open(sidecar_path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)

    enriched_results = []
    for item in data.get("results", []):
        cite_text = item.get("citation_text", "")
        csv_row = _master_csv.get_row(cite_text)

        enriched = {
            **item,
            "qc_status": "",
            "qc_notes": "",
            "context": "",
            "pdf": item.get("pdf", ""),
            "case_name": "",
            "volume": "",
            "reporter": "",
            "page": "",
        }

        if csv_row:
            enriched["qc_status"] = csv_row.get("qc_status", "")
            enriched["qc_notes"] = csv_row.get("qc_notes", "")
            enriched["context"] = csv_row.get("context", "")
            enriched["pdf"] = csv_row.get("pdf", enriched["pdf"])
            enriched["case_name"] = csv_row.get("case_name", "")
            enriched["volume"] = csv_row.get("volume", "")
            enriched["reporter"] = csv_row.get("reporter", "")
            enriched["page"] = csv_row.get("page", "")

        # Find duplicates
        enriched["duplicates"] = _master_csv.find_duplicates(
            case_name=enriched.get("case_name") or item.get("matched_case_name"),
            volume=enriched.get("volume"),
            reporter=enriched.get("reporter"),
            page=enriched.get("page"),
            exclude_citation=cite_text,
        )

        enriched_results.append(enriched)

    return {
        "metadata": data.get("_metadata", {}),
        "results": enriched_results,
    }


@app.post("/api/qc/save")
async def qc_save(request: Request):
    """Save a QC decision back to the master CSV."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    citation_text = body.get("citation_text", "").strip()
    qc_status = body.get("qc_status", "").strip()
    qc_notes = body.get("qc_notes", "").strip()

    if not citation_text:
        return JSONResponse({"error": "citation_text required"}, status_code=400)

    valid_statuses = {"approved", "rerun", "duplicate", "ignore", "investigate", "data", ""}
    if qc_status not in valid_statuses:
        return JSONResponse(
            {"error": f"Invalid qc_status. Must be one of: {', '.join(sorted(valid_statuses))}"},
            status_code=400,
        )

    # Get the full row before updating (for context in TODO/contributions)
    csv_row = _master_csv.get_row(citation_text)

    ok = _master_csv.update_qc(citation_text, qc_status, qc_notes)
    if not ok:
        return JSONResponse(
            {"error": "Citation not found in master CSV"},
            status_code=404,
        )

    # Auto-append to QC_TRIAGE.md for "investigate" items
    if qc_status == "investigate" and csv_row:
        _append_to_todo(citation_text, qc_notes, csv_row)

    # Auto-append to flp_contributions.md for "data" items
    if qc_status == "data" and csv_row:
        _append_to_contributions(citation_text, qc_notes, csv_row)

    return {"ok": True, "citation_text": citation_text, "qc_status": qc_status}


_qc_triage_path = _project_root / "scratch" / "QC_TRIAGE.md"
_contributions_path = _project_root / "scratch" / "flp_contributions.md"


def _append_to_todo(citation_text: str, notes: str, row: dict) -> None:
    """Append an investigate item to QC_TRIAGE.md for later categorization."""
    try:
        content = _qc_triage_path.read_text(encoding="utf-8") if _qc_triage_path.exists() else ""

        # Don't add duplicates
        if citation_text in content:
            return

        pdf = row.get("pdf", "unknown")
        v_status = row.get("v_status", "")
        v_confidence = row.get("v_confidence", "")
        v_url = row.get("v_url", "")

        entry = f"\n**{citation_text}**\n"
        entry += f"Source: {pdf}. Verification: {v_status} ({v_confidence}). "
        if v_url:
            entry += f"Matched: {v_url}. "
        if notes:
            entry += f"Notes: {notes}. "
        entry += f"Added {__import__('datetime').date.today().isoformat()} from QC review.\n"

        content += entry

        _qc_triage_path.write_text(content, encoding="utf-8")
        logger.info("Added investigate item to QC_TRIAGE.md: %s", citation_text)
    except Exception:
        logger.exception("Failed to append to QC_TRIAGE.md")


def _append_to_contributions(citation_text: str, notes: str, row: dict) -> None:
    """Append a data quality item to scratch/flp_contributions.md section 6."""
    try:
        content = _contributions_path.read_text(encoding="utf-8") if _contributions_path.exists() else ""

        # Don't add duplicates
        if citation_text in content:
            return

        pdf = row.get("pdf", "unknown")
        v_status = row.get("v_status", "")

        entry = f"  - **{citation_text}** — "
        if notes:
            entry += f"{notes}. "
        entry += f"Source: {pdf}. Verification: {v_status}. "
        entry += "Added automatically from QC review.\n"

        # Insert before the "Action: Collect more examples" line
        action_marker = "- Action: Collect more examples before reporting."
        if action_marker in content:
            content = content.replace(
                action_marker,
                f"{entry}{action_marker}",
            )
        else:
            content += f"\n{entry}"

        _contributions_path.write_text(content, encoding="utf-8")
        logger.info("Added data item to flp_contributions.md: %s", citation_text)
    except Exception:
        logger.exception("Failed to append to flp_contributions.md")


@app.get("/api/qc/opinion-text")
async def qc_opinion_text(url: str):
    """Fetch the first ~2000 chars of opinion text from CourtListener API."""
    import re

    opinion_match = re.search(r"/opinion/(\d+)/", url)
    docket_match = re.search(r"/docket/(\d+)/(\d+)/", url)

    try:
        async with AsyncCourtListenerClient() as client:
            text = ""
            case_name = ""
            date_filed = ""
            court = ""

            if opinion_match:
                # Fetch opinion text via cluster -> sub_opinions
                cluster_id = opinion_match.group(1)
                cluster_url = f"{client.BASE_URL}/clusters/{cluster_id}/"
                cluster_data = await client._request_with_retry("GET", cluster_url)
                case_name = cluster_data.get("case_name", "")
                date_filed = cluster_data.get("date_filed", "")
                court = cluster_data.get("court", "")

                sub_opinions = cluster_data.get("sub_opinions", [])
                if sub_opinions:
                    opinion_url = sub_opinions[0]
                    if isinstance(opinion_url, str) and opinion_url.startswith("http"):
                        opinion_data = await client._request_with_retry("GET", opinion_url)
                    elif isinstance(opinion_url, dict):
                        opinion_data = opinion_url
                    else:
                        opinion_url = f"{client.BASE_URL}/opinions/{opinion_url}/"
                        opinion_data = await client._request_with_retry("GET", opinion_url)

                    text = opinion_data.get("plain_text", "")
                    if not text:
                        html = opinion_data.get("html_with_citations", "") or opinion_data.get("html", "")
                        if html:
                            text = re.sub(r"<[^>]+>", " ", html)
                            text = re.sub(r"\s+", " ", text).strip()

            elif docket_match:
                # Fetch docket entry document text
                docket_id = docket_match.group(1)
                entry_number = docket_match.group(2)
                entry_url = f"{client.BASE_URL}/docket-entries/"
                entry_data = await client._request_with_retry(
                    "GET", entry_url,
                    params={"docket": docket_id, "entry_number": entry_number},
                )
                results = entry_data.get("results", [])
                if results:
                    entry = results[0]
                    recap_docs = entry.get("recap_documents", [])
                    if recap_docs:
                        text = recap_docs[0].get("plain_text", "")
                    desc = entry.get("description", "")
                    if desc:
                        case_name = desc
                    date_filed = entry.get("date_filed", "")
            else:
                return JSONResponse({"error": "Could not parse opinion or docket ID from URL"}, status_code=400)

            # Truncate to first ~2000 chars at a sentence boundary
            if len(text) > 2000:
                cutoff = text.rfind(".", 0, 2000)
                if cutoff > 500:
                    text = text[:cutoff + 1] + "\n\n[...]"
                else:
                    text = text[:2000] + "\n\n[...]"

            return {
                "text": text,
                "case_name": case_name,
                "date_filed": date_filed,
                "court": court,
            }

    except Exception as exc:
        logger.exception("Error fetching opinion text for %s", url)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Batch verification from QC page
# ---------------------------------------------------------------------------

def _get_git_hash() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


_NEW_COLUMNS = [
    "v_status", "v_confidence", "v_url", "v_matched_name",
    "v_git_hash", "qc_status", "qc_notes",
]


def _confidence_str(result: VerificationResult) -> str:
    """Stringify headline_confidence for CSV (empty string when None)."""
    c = result.headline_confidence
    return "" if c is None else str(c)


def _apply_verification_to_row(
    row: dict, result: VerificationResult, git_hash: str | None,
) -> None:
    """Write v_* columns to a CSV row from a VerificationResult."""
    row["v_status"] = result.status.value
    row["v_confidence"] = _confidence_str(result)
    row["v_url"] = result.final_ids.absolute_url or ""
    row["v_matched_name"] = _matched_case_name(result) or ""
    row["v_git_hash"] = git_hash or ""
    if row.get("qc_status") == "rerun":
        row["qc_status"] = ""
        row["qc_notes"] = ""


def _sidecar_entry(
    cite_text: str, row: dict, result: VerificationResult,
) -> dict[str, Any]:
    """Build a JSON-sidecar entry mirroring the historical schema, sourced
    from v0.3 fields. matched_court/date fall back to *cited* values (the
    v0.3 result no longer stores what CL matched); matched_description is
    always None in Phase 1.
    """
    court = None
    date = None
    if result.parsed_citation:
        court = result.parsed_citation.court
        if result.parsed_citation.year is not None:
            date = str(result.parsed_citation.year)
    return {
        "citation_text": cite_text,
        "classification": row.get("classification", ""),
        "pdf": row.get("pdf", ""),
        "status": result.status.value,
        "confidence": result.headline_confidence,
        "matched_case_name": _matched_case_name(result),
        "matched_url": result.final_ids.absolute_url,
        "matched_court": court,
        "matched_date": date,
        "matched_description": None,
        "diagnostics": _legacy_diagnostics(result),
        "warnings": [
            {"category": w.category.value, "message": w.message, "details": w.details}
            for w in result.warnings or []
        ],
        "stage_notes": _stage_notes(result) or None,
    }


def _is_actionable(row: dict) -> bool:
    if row.get("qc_status") == "rerun":
        return True
    if row.get("qc_status") in ("duplicate", "ignore", "investigate", "data"):
        return False
    if not row.get("v_status"):
        return True
    return False


def _parsed_citation_from_row(row: dict) -> ParsedCitation:
    def _int_or_none(val: str | None) -> int | None:
        if not val:
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    case_name = row.get("case_name") or None
    plaintiff = row.get("plaintiff") or None
    defendant = row.get("defendant") or None
    if not case_name and plaintiff and defendant:
        case_name = f"{plaintiff} v. {defendant}"

    return ParsedCitation(
        raw_text=row.get("citation_text", ""),
        case_name=case_name,
        plaintiff=plaintiff,
        defendant=defendant,
        volume=row.get("volume") or None,
        reporter=row.get("reporter") or None,
        page=row.get("page") or None,
        court=row.get("court") or None,
        year=_int_or_none(row.get("year")),
        month=_int_or_none(row.get("month")),
        day=_int_or_none(row.get("day")),
        docket_number=row.get("docket_number") or None,
        is_westlaw=row.get("is_westlaw", "").upper() in ("TRUE", "1", "YES"),
        wl_number=row.get("wl_number") or None,
    )


@app.post("/api/qc/run-batch")
async def qc_run_batch(request: Request):
    """Run a new verification batch via SSE, writing results to CSV and JSON sidecar."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    sample_size = body.get("sample_size", 10)
    sample_size = max(1, min(sample_size, 100))
    rerun_only = body.get("rerun_only", False)
    batch_filter = body.get("filter", "all")

    # Read CSV
    csv_path = _default_csv
    if not csv_path.exists():
        return JSONResponse({"error": "Master CSV not found"}, status_code=404)

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        all_rows = list(reader)

    for col in _NEW_COLUMNS:
        if col not in fieldnames:
            fieldnames.append(col)

    # Filter actionable
    if rerun_only:
        actionable = [r for r in all_rows if r.get("qc_status") == "rerun"]
    else:
        actionable = [r for r in all_rows if _is_actionable(r)]

    # Apply batch filter
    if batch_filter == "unpublished":
        actionable = [r for r in actionable
                      if r.get("is_westlaw", "").upper() in ("TRUE", "1", "YES")]
    elif batch_filter == "post2020":
        def _year_ge(row: dict, threshold: int) -> bool:
            try:
                return int(row.get("year", "0")) >= threshold
            except (ValueError, TypeError):
                return False
        actionable = [r for r in actionable if _year_ge(r, 2021)]
    elif batch_filter == "hard":
        def _is_hard(row: dict) -> bool:
            is_wl = row.get("is_westlaw", "").upper() in ("TRUE", "1", "YES")
            try:
                is_recent = int(row.get("year", "0")) >= 2021
            except (ValueError, TypeError):
                is_recent = False
            return is_wl or is_recent
        actionable = [r for r in actionable if _is_hard(r)]

    if not actionable:
        return JSONResponse({"error": "No pending citations to verify"}, status_code=200)

    # Sample
    seed_val = random.randrange(10000)
    random.seed(seed_val)
    to_verify = random.sample(actionable, min(sample_size, len(actionable)))

    async def event_generator():
        yield {
            "event": "start",
            "data": json.dumps({
                "total": len(to_verify),
                "seed": seed_val,
                "actionable": len(actionable),
            }),
        }

        git_hash = _get_git_hash()
        results_for_sidecar: list[dict] = []
        t_start = time.monotonic()

        # Separate short cites from real citations
        batch_citations: list[str] = []
        batch_parsed: list[ParsedCitation] = []
        batch_row_indices: list[int] = []
        skipped: list[tuple[int, dict]] = []

        for seq, row in enumerate(to_verify):
            citation_text = row.get("citation_text", "").strip()
            case_name = row.get("case_name", "").strip()

            if not case_name or case_name == "v." or case_name.startswith("None v. None"):
                row["v_status"] = "SKIPPED"
                row["v_confidence"] = ""
                row["v_url"] = ""
                row["v_matched_name"] = ""
                row["v_git_hash"] = git_hash or ""
                if row.get("qc_status") == "rerun":
                    row["qc_status"] = ""
                    row["qc_notes"] = ""
                sidecar_entry = {
                    "citation_text": citation_text,
                    "classification": row.get("classification", ""),
                    "pdf": row.get("pdf", ""),
                    "status": "SKIPPED",
                    "confidence": 0.0,
                    "matched_case_name": None,
                    "matched_url": None,
                    "diagnostics": [{"category": "info", "message": "Short cite with no case name"}],
                }
                results_for_sidecar.append(sidecar_entry)
                skipped.append((seq, row))
                yield {
                    "event": "result",
                    "data": json.dumps({
                        "index": seq,
                        "citation_text": citation_text,
                        "status": "SKIPPED",
                        "confidence": 0.0,
                    }),
                }
                continue

            batch_citations.append(citation_text)
            batch_parsed.append(_parsed_citation_from_row(row))
            batch_row_indices.append(seq)

        # Single batched citation_lookup for all rows; misses fan out
        # through verify_batch's internal opinion-search + RECAP fallback.
        # The QC page UI (qc.html) only consumes start/progress/done events
        # — per-result events are not rendered — so collecting all results
        # before streaming them is UX-neutral here.
        if batch_citations:
            completed_n = 0
            async with AsyncCourtListenerClient() as client:
                try:
                    batch_results = await _verifier.verify_batch(
                        batch_citations,
                        parsed_citations=batch_parsed,
                        client=client,
                    )
                except Exception as exc:
                    logger.exception(
                        "verify_batch failed for %d citations",
                        len(batch_citations),
                    )
                    yield {
                        "event": "error",
                        "data": json.dumps({"error": str(exc)}),
                    }
                    return

            for i, (cite_text, result) in enumerate(
                zip(batch_citations, batch_results)
            ):
                row = to_verify[batch_row_indices[i]]
                _apply_verification_to_row(row, result, git_hash)
                results_for_sidecar.append(
                    _sidecar_entry(cite_text, row, result)
                )
                completed_n += 1
                yield {
                    "event": "result",
                    "data": json.dumps({
                        "index": batch_row_indices[i],
                        "citation_text": cite_text,
                        "status": result.status.value,
                        "confidence": result.headline_confidence,
                        "matched_case_name": _matched_case_name(result),
                    }),
                }
                yield {
                    "event": "progress",
                    "data": json.dumps({
                        "completed": completed_n + len(skipped),
                        "total": len(to_verify),
                    }),
                }

        # Write CSV back
        bak = csv_path.with_suffix(".csv.bak")
        shutil.copy2(csv_path, bak)

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in all_rows:
                for col in _NEW_COLUMNS:
                    if col not in row:
                        row[col] = ""
                writer.writerow(row)

        # Force MasterCSV to reload
        _master_csv._rows = None

        # Write JSON sidecar
        _results_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d")
        sidecar_name = f"verification_{timestamp}_csv_seed{seed_val}.json"
        sidecar_path = _results_dir / sidecar_name
        elapsed = time.monotonic() - t_start

        by_status: dict[str, int] = {}
        for r in results_for_sidecar:
            s = r["status"]
            by_status[s] = by_status.get(s, 0) + 1

        sidecar_data = {
            "_metadata": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "git_hash": git_hash,
                "seed": str(seed_val),
                "sample_size": len(to_verify),
                "source": str(csv_path),
                "elapsed_seconds": round(elapsed, 1),
            },
            "results": results_for_sidecar,
        }
        with open(sidecar_path, "w") as f:
            json.dump(sidecar_data, f, indent=2)

        yield {
            "event": "done",
            "data": json.dumps({
                "total": len(to_verify),
                "by_status": by_status,
                "sidecar": sidecar_name,
                "elapsed": round(elapsed, 1),
                "seed": seed_val,
            }),
        }

    return EventSourceResponse(event_generator())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
