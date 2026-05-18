"""CourtListener API wrapper with rate limiting (sync and async)."""

from __future__ import annotations

import asyncio
import os
import re
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import certifi
import requests
from dotenv import load_dotenv

# Load .env from project root (walk up from this file)
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_path)


# CL opinion content can live in several fields. `plain_text` is empty for
# many state opinions (and some older federal ones); `html_lawbox` and
# `xml_harvard` cover most of the gap. This list is the canonical fallback
# order used everywhere we need opinion text.
_OPINION_HTML_FIELDS: tuple[str, ...] = (
    "html_with_citations",
    "html",
    "html_lawbox",
    "html_columbia",
    "html_anon_2020",
)
_OPINION_XML_FIELDS: tuple[str, ...] = ("xml_harvard",)


def _strip_markup(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _extract_opinion_text(
    opinion: dict[str, Any], *, prefer_html: bool = False,
) -> tuple[str, str]:
    """Pull text out of a CL opinion JSON, walking the canonical fallback chain.

    Returns ``(text, fmt)``. ``fmt`` is one of ``"text"``, ``"html"``, or
    ``""`` when nothing usable was found.

    - ``prefer_html=True``: returns the first non-empty HTML field as raw
      HTML (``fmt="html"``), or the first non-empty plain/XML field stripped
      to text. Callers that want raw markup pass this; the assessor wants
      plain text and uses the default.
    - ``prefer_html=False``: returns plain text when available, else the
      first non-empty HTML/XML field stripped to text. ``fmt`` is always
      ``"text"`` in this mode.
    """
    if prefer_html:
        for f in _OPINION_HTML_FIELDS:
            v = (opinion.get(f) or "").strip()
            if v:
                return v, "html"

    plain = (opinion.get("plain_text") or "").strip()
    if plain:
        return plain, "text"

    for f in _OPINION_HTML_FIELDS + _OPINION_XML_FIELDS:
        v = (opinion.get(f) or "")
        if v.strip():
            stripped = _strip_markup(v)
            if stripped:
                return stripped, "text"

    return "", ""


class CourtListenerClient:
    """Client for the CourtListener REST API v4."""

    BASE_URL = "https://www.courtlistener.com/api/rest/v4"
    REQUEST_TIMEOUT = 15  # seconds

    def __init__(self, api_token: str | None = None):
        self.api_token = api_token or os.environ.get("COURTLISTENER_API_TOKEN", "")
        self._session = requests.Session()
        if self.api_token:
            self._session.headers["Authorization"] = f"Token {self.api_token}"
        self._session.headers["User-Agent"] = "citation-verifier/0.1"
        self._last_request_time: float = 0.0

    MAX_RETRIES = 3

    def _rate_limit(self) -> None:
        """Enforce at least 1 second between requests."""
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        self._last_request_time = time.monotonic()

    def _request_with_retry(
        self, method: str, url: str, **kwargs: Any
    ) -> requests.Response:
        """Make an HTTP request with 429 retry handling.

        On 429 (Too Many Requests), respects the ``wait_until`` timestamp
        from the response body or falls back to ``Retry-After`` header.
        """
        kwargs.setdefault("timeout", self.REQUEST_TIMEOUT)
        for attempt in range(self.MAX_RETRIES):
            self._rate_limit()
            resp = self._session.request(method, url, **kwargs)
            if resp.status_code != 429:
                resp.raise_for_status()
                return resp

            # Parse wait time from response
            wait_seconds = 60.0  # conservative default
            try:
                body = resp.json()
                wait_until = body.get("wait_until")
                if wait_until:
                    target = datetime.fromisoformat(wait_until)
                    if target.tzinfo is None:
                        target = target.replace(tzinfo=timezone.utc)
                    wait_seconds = max(
                        0.0, (target - datetime.now(timezone.utc)).total_seconds()
                    )
            except Exception:
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait_seconds = float(retry_after)
                    except ValueError:
                        pass

            if attempt < self.MAX_RETRIES - 1:
                time.sleep(wait_seconds + 1.0)  # +1s buffer

        # Final attempt failed
        resp.raise_for_status()
        return resp  # unreachable but satisfies type checker

    def citation_lookup(self, text: str) -> list[dict[str, Any]]:
        """Look up citations using the Citation Lookup API.

        Returns a list of matched opinion clusters.
        """
        url = f"{self.BASE_URL}/citation-lookup/"
        resp = self._request_with_retry("POST", url, json={"text": text})
        data: Any = resp.json()
        # The API returns a list of matched clusters
        if isinstance(data, list):
            return data
        # Or it may return a dict with results
        if isinstance(data, dict):
            results_val = data.get("results", data.get("clusters", []))
            if isinstance(results_val, list):
                return results_val
        return []

    def search_opinions(
        self,
        q: str | None = None,
        court: str | None = None,
        filed_after: str | None = None,
        filed_before: str | None = None,
        case_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search opinions using the CourtListener Search API.

        Returns a list of search result dicts.
        """
        params: dict[str, str] = {
            "type": "o",
            # Include all precedential statuses -- the default only returns
            # Published, hiding the many district-court opinions ingested from
            # RECAP that are classified as Unknown.
            "stat_Published": "on",
            "stat_Unpublished": "on",
            "stat_Unknown": "on",
        }
        if q:
            params["q"] = q
        if court:
            params["court"] = court
        if filed_after:
            params["filed_after"] = filed_after
        if filed_before:
            params["filed_before"] = filed_before
        if case_name:
            params["case_name"] = case_name

        url = f"{self.BASE_URL}/search/"
        resp = self._request_with_retry("GET", url, params=params)
        data: Any = resp.json()
        results: list[dict[str, Any]] = data.get("results", [])
        return results

    def search_recap(
        self,
        q: str | None = None,
        court: str | None = None,
        filed_after: str | None = None,
        filed_before: str | None = None,
        case_name: str | None = None,
        docket_number: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search RECAP docket entries (orders, opinions from PACER).

        Returns a list of search result dicts.
        """
        params: dict[str, str] = {"type": "r"}
        if q:
            params["q"] = q
        if court:
            params["court"] = court
        if filed_after:
            params["filed_after"] = filed_after
        if filed_before:
            params["filed_before"] = filed_before
        if case_name:
            params["case_name"] = case_name
        if docket_number:
            # Use q with quoted string — the docket param is unreliable
            q_parts = [params.get("q", ""), f'"{docket_number}"']
            params["q"] = " ".join(p for p in q_parts if p)

        url = f"{self.BASE_URL}/search/"
        resp = self._request_with_retry("GET", url, params=params)
        data: Any = resp.json()
        results: list[dict[str, Any]] = data.get("results", [])
        return results

    MAX_DOCKET_ENTRIES = 200

    def get_docket_entries(
        self,
        docket_id: int,
        date_filed_after: str | None = None,
        date_filed_before: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch docket entries for a specific docket, optionally filtered by date.

        Follows cursor pagination to retrieve all matching entries (up to
        MAX_DOCKET_ENTRIES to avoid runaway requests on very large dockets).

        Returns individual docket entries with their recap_documents.
        """
        params: dict[str, str] = {"docket": str(docket_id)}
        if date_filed_after:
            params["date_filed__gte"] = date_filed_after
        if date_filed_before:
            params["date_filed__lte"] = date_filed_before

        url: str | None = f"{self.BASE_URL}/docket-entries/"
        results: list[dict[str, Any]] = []
        while url and len(results) < self.MAX_DOCKET_ENTRIES:
            resp = self._request_with_retry("GET", url, params=params)
            data: Any = resp.json()
            results.extend(data.get("results", []))
            url = data.get("next")
            # Only pass params on the first request; subsequent pages
            # encode params in the next URL already.
            params = {}
        return results

    def get_opinion_text(self, matched_url: str) -> str | None:
        """Fetch the plain text of an opinion or RECAP document (sync).

        Mirrors AsyncCourtListenerClient.get_opinion_text().
        """
        result = self.get_opinion_text_with_metadata(matched_url)
        return result.get("text") if result else None

    def get_opinion_text_with_metadata(
        self, matched_url: str, *, prefer_html: bool = False,
    ) -> dict[str, Any] | None:
        """Fetch opinion text with metadata (sync).

        Returns dict with: text, case_name, court, date_filed,
        docket_number, citations, source_url, format. Returns None if not found.

        When prefer_html=True, returns raw HTML (format="html") if available,
        falls back to plain text (format="text"), then PDF bytes (format="pdf").
        """
        if not matched_url:
            return None

        if "/opinion/" in matched_url:
            return self._resolve_opinion_text_with_metadata(
                matched_url, prefer_html=prefer_html,
            )

        docket_match = re.search(r"/docket/(\d+)/", matched_url)
        if not docket_match:
            return None

        docket_id = docket_match.group(1)
        docket_entry_match = re.search(r"/docket/\d+/(\d+)/", matched_url)

        try:
            text = None

            if docket_entry_match:
                entry_number = docket_entry_match.group(1)
                resp = self._request_with_retry(
                    "GET",
                    f"{self.BASE_URL}/docket-entries/",
                    params={"docket": docket_id, "entry_number": entry_number},
                )
                entry_data = resp.json()
                for entry in entry_data.get("results", []):
                    for doc in entry.get("recap_documents", []):
                        doc_id = doc.get("id")
                        if not doc_id:
                            continue
                        doc_resp = self._request_with_retry(
                            "GET",
                            f"{self.BASE_URL}/recap-documents/{doc_id}/",
                        )
                        t = doc_resp.json().get("plain_text", "")
                        if t and t.strip():
                            text = t
                            break
                    if text:
                        break

            if not text:
                resp = self._request_with_retry(
                    "GET",
                    f"{self.BASE_URL}/docket-entries/",
                    params={"docket": docket_id, "page_size": "5"},
                )
                entry_data = resp.json()
                for entry in entry_data.get("results", []):
                    for doc in entry.get("recap_documents", []):
                        doc_id = doc.get("id")
                        if not doc_id:
                            continue
                        doc_resp = self._request_with_retry(
                            "GET",
                            f"{self.BASE_URL}/recap-documents/{doc_id}/",
                        )
                        t = doc_resp.json().get("plain_text", "")
                        if t and t.strip():
                            text = t
                            break
                    if text:
                        break

            if not text:
                return None

            docket_resp = self._request_with_retry(
                "GET", f"{self.BASE_URL}/dockets/{docket_id}/",
            )
            docket = docket_resp.json()
            court_url = docket.get("court", "")
            court_name = ""
            if court_url:
                court_id = court_url.rstrip("/").split("/")[-1]
                try:
                    court_data = self._request_with_retry(
                        "GET", f"{self.BASE_URL}/courts/{court_id}/",
                    )
                    court_name = court_data.json().get("full_name", "")
                except Exception:
                    pass

            return {
                "text": text,
                "format": "text",
                "case_name": docket.get("case_name", ""),
                "court": court_name,
                "date_filed": docket.get("date_filed", ""),
                "docket_number": docket.get("docket_number", ""),
                "citations": [],
                "source_url": matched_url,
            }

        except Exception:
            pass

        return None

    STORAGE_BASE = "https://storage.courtlistener.com/"

    def _resolve_opinion_text_with_metadata(
        self, matched_url: str, *, prefer_html: bool = False,
    ) -> dict[str, Any] | None:
        """Resolve an opinion URL to text + metadata (sync).

        When prefer_html=True, returns raw HTML with format="html" if
        available; falls back to plain text (format="text"), then PDF
        download (format="pdf").
        """
        cluster_match = re.search(r"/opinion/(\d+)/", matched_url)
        if not cluster_match:
            return None

        cluster_id = cluster_match.group(1)

        try:
            cluster_resp = self._request_with_retry(
                "GET", f"{self.BASE_URL}/clusters/{cluster_id}/"
            )
            cluster = cluster_resp.json()

            text = None
            fmt = "text"
            for op_url in cluster.get("sub_opinions", []):
                op_id = op_url.rstrip("/").split("/")[-1]
                opinion_resp = self._request_with_retry(
                    "GET", f"{self.BASE_URL}/opinions/{op_id}/"
                )
                opinion = opinion_resp.json()
                t, f = _extract_opinion_text(opinion, prefer_html=prefer_html)
                if t:
                    text = t
                    fmt = f
                    break

            # PDF fallback when no text or HTML found
            if not text and prefer_html:
                pdf_path = cluster.get("filepath_pdf_with_extracted_text", "")
                if pdf_path:
                    pdf_url = f"{self.STORAGE_BASE}{pdf_path}"
                    pdf_resp = self._request_with_retry("GET", pdf_url)
                    return {
                        "text": None,
                        "pdf_bytes": pdf_resp.content,
                        "format": "pdf",
                        "case_name": cluster.get("case_name", ""),
                        "court": "",
                        "date_filed": cluster.get("date_filed", ""),
                        "docket_number": "",
                        "citations": [],
                        "source_url": matched_url,
                    }

            if not text:
                return None

            citations = []
            for cite in cluster.get("citations", []):
                if isinstance(cite, str):
                    continue
                if isinstance(cite, dict):
                    vol = cite.get("volume", "")
                    rep = cite.get("reporter", "")
                    pg = cite.get("page", "")
                    if vol and rep and pg:
                        citations.append(f"{vol} {rep} {pg}")

            court_name = ""
            docket_number = ""
            docket_url = cluster.get("docket", "")
            if docket_url:
                docket_id = docket_url.rstrip("/").split("/")[-1]
                try:
                    docket_resp = self._request_with_retry(
                        "GET", f"{self.BASE_URL}/dockets/{docket_id}/",
                    )
                    docket = docket_resp.json()
                    docket_number = docket.get("docket_number", "")
                    court_url = docket.get("court", "")
                    if court_url:
                        court_id = court_url.rstrip("/").split("/")[-1]
                        court_data = self._request_with_retry(
                            "GET", f"{self.BASE_URL}/courts/{court_id}/",
                        )
                        court_name = court_data.json().get("full_name", "")
                except Exception:
                    pass

            return {
                "text": text,
                "format": fmt,
                "case_name": cluster.get("case_name", ""),
                "court": court_name,
                "date_filed": cluster.get("date_filed", ""),
                "docket_number": docket_number,
                "citations": citations,
                "source_url": matched_url,
            }

        except Exception:
            pass

        return None


class AsyncCourtListenerClient:
    """Async client for the CourtListener REST API v4.

    Uses aiohttp with a semaphore to limit concurrent requests.
    Must be used as an async context manager::

        async with AsyncCourtListenerClient() as client:
            results = await client.citation_lookup("576 U.S. 644")
    """

    BASE_URL = CourtListenerClient.BASE_URL
    REQUEST_TIMEOUT = CourtListenerClient.REQUEST_TIMEOUT
    MAX_RETRIES = CourtListenerClient.MAX_RETRIES
    MAX_DOCKET_ENTRIES = CourtListenerClient.MAX_DOCKET_ENTRIES

    # Max concurrent HTTP connections to CourtListener
    MAX_CONCURRENT = 5
    # Minimum interval between any two requests (seconds).
    # Mirrors the sync client's 1-second rate limit but slightly faster
    # since we're authenticated and want some parallelism benefit.
    MIN_REQUEST_INTERVAL = 0.5

    def __init__(self, api_token: str | None = None):
        self.api_token = api_token or os.environ.get("COURTLISTENER_API_TOKEN", "")
        self._session: aiohttp.ClientSession | None = None
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT)
        self._rate_lock = asyncio.Lock()
        self._last_request_time: float = 0.0

    async def __aenter__(self) -> AsyncCourtListenerClient:
        headers: dict[str, str] = {"User-Agent": "citation-verifier/0.1"}
        if self.api_token:
            headers["Authorization"] = f"Token {self.api_token}"
        timeout = aiohttp.ClientTimeout(total=self.REQUEST_TIMEOUT)
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        self._session = aiohttp.ClientSession(
            headers=headers, timeout=timeout, connector=connector
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def _rate_limit(self) -> None:
        """Enforce minimum interval between requests (async-safe).

        Uses a lock so only one coroutine checks/updates the timestamp
        at a time, guaranteeing a global minimum interval.
        """
        async with self._rate_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self.MIN_REQUEST_INTERVAL:
                await asyncio.sleep(self.MIN_REQUEST_INTERVAL - elapsed)
            self._last_request_time = time.monotonic()

    async def _request_with_retry(
        self, method: str, url: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Make an HTTP request with rate limiting and 429 retry.

        Rate limiting enforces a global minimum interval between requests.
        The semaphore caps concurrent connections. On 429, the semaphore
        is released during backoff so other requests can proceed.

        Returns parsed JSON response.
        """
        assert self._session is not None, "Use 'async with' to create the client"
        for attempt in range(self.MAX_RETRIES):
            await self._rate_limit()
            async with self._semaphore:
                async with self._session.request(method, url, **kwargs) as resp:
                    if resp.status == 429:
                        wait_seconds = 60.0
                        try:
                            body = await resp.json()
                            wait_until = body.get("wait_until")
                            if wait_until:
                                target = datetime.fromisoformat(wait_until)
                                if target.tzinfo is None:
                                    target = target.replace(tzinfo=timezone.utc)
                                wait_seconds = max(
                                    0.0,
                                    (target - datetime.now(timezone.utc)).total_seconds(),
                                )
                        except Exception:
                            retry_after = resp.headers.get("Retry-After")
                            if retry_after:
                                try:
                                    wait_seconds = float(retry_after)
                                except ValueError:
                                    pass
                        if attempt < self.MAX_RETRIES - 1:
                            # Release semaphore during backoff sleep
                            backoff = wait_seconds * (2**attempt)
                            await asyncio.sleep(backoff + 1.0)
                            continue
                        resp.raise_for_status()

                    resp.raise_for_status()
                    return await resp.json()
        # Unreachable but satisfies type checker
        return {}

    async def citation_lookup(self, text: str) -> list[dict[str, Any]]:
        """Look up citations using the Citation Lookup API."""
        url = f"{self.BASE_URL}/citation-lookup/"
        data = await self._request_with_retry("POST", url, json={"text": text})
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            results_val = data.get("results", data.get("clusters", []))
            if isinstance(results_val, list):
                return results_val
        return []

    async def search_opinions(
        self,
        q: str | None = None,
        court: str | None = None,
        filed_after: str | None = None,
        filed_before: str | None = None,
        case_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search opinions using the CourtListener Search API."""
        params: dict[str, str] = {
            "type": "o",
            "stat_Published": "on",
            "stat_Unpublished": "on",
            "stat_Unknown": "on",
        }
        if q:
            params["q"] = q
        if court:
            params["court"] = court
        if filed_after:
            params["filed_after"] = filed_after
        if filed_before:
            params["filed_before"] = filed_before
        if case_name:
            params["case_name"] = case_name

        url = f"{self.BASE_URL}/search/"
        data = await self._request_with_retry("GET", url, params=params)
        return data.get("results", [])

    async def search_recap(
        self,
        q: str | None = None,
        court: str | None = None,
        filed_after: str | None = None,
        filed_before: str | None = None,
        case_name: str | None = None,
        docket_number: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search RECAP docket entries."""
        params: dict[str, str] = {"type": "r"}
        if q:
            params["q"] = q
        if court:
            params["court"] = court
        if filed_after:
            params["filed_after"] = filed_after
        if filed_before:
            params["filed_before"] = filed_before
        if case_name:
            params["case_name"] = case_name
        if docket_number:
            q_parts = [params.get("q", ""), f'"{docket_number}"']
            params["q"] = " ".join(p for p in q_parts if p)

        url = f"{self.BASE_URL}/search/"
        data = await self._request_with_retry("GET", url, params=params)
        return data.get("results", [])

    async def get_docket_entries(
        self,
        docket_id: int,
        date_filed_after: str | None = None,
        date_filed_before: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch docket entries for a specific docket, optionally filtered by date."""
        params: dict[str, str] = {"docket": str(docket_id)}
        if date_filed_after:
            params["date_filed__gte"] = date_filed_after
        if date_filed_before:
            params["date_filed__lte"] = date_filed_before

        url: str | None = f"{self.BASE_URL}/docket-entries/"
        results: list[dict[str, Any]] = []
        while url and len(results) < self.MAX_DOCKET_ENTRIES:
            data = await self._request_with_retry("GET", url, params=params)
            results.extend(data.get("results", []))
            url = data.get("next")
            params = {}
        return results

    STORAGE_BASE = "https://storage.courtlistener.com/"

    async def get_opinion_text(self, matched_url: str) -> str | None:
        """Fetch the plain text of an opinion or RECAP document.

        For opinions: cluster API -> sub_opinions -> opinion API -> plain_text
        (falls back to stripped html_with_citations if plain_text is empty).
        For RECAP dockets: docket-entries API -> recap_documents -> recap-document
        API -> plain_text.

        Returns None if no text is available.
        """
        result = await self.get_opinion_text_with_metadata(matched_url)
        return result.get("text") if result else None

    async def get_opinion_text_with_metadata(
        self, matched_url: str, *, prefer_html: bool = False,
    ) -> dict[str, Any] | None:
        """Fetch opinion text along with metadata (case name, court, date, etc.).

        Returns a dict with keys: text, case_name, court, date_filed,
        docket_number, citations, source_url, format.  Returns None if no text found.

        When prefer_html=True, returns raw HTML (format="html") if available,
        falls back to plain text (format="text"), then PDF bytes (format="pdf").
        """
        if not matched_url:
            return None

        if "/opinion/" in matched_url:
            return await self._resolve_opinion_text_with_metadata(
                matched_url, prefer_html=prefer_html,
            )

        docket_match = re.search(r"/docket/(\d+)/", matched_url)
        if not docket_match:
            return None

        docket_id = docket_match.group(1)
        docket_entry_match = re.search(r"/docket/\d+/(\d+)/", matched_url)

        try:
            text = None

            if docket_entry_match:
                entry_number = docket_entry_match.group(1)
                entry_data = await self._request_with_retry(
                    "GET",
                    f"{self.BASE_URL}/docket-entries/",
                    params={"docket": docket_id, "entry_number": entry_number},
                )
                for entry in entry_data.get("results", []):
                    for doc in entry.get("recap_documents", []):
                        doc_id = doc.get("id")
                        if not doc_id:
                            continue
                        doc_detail = await self._request_with_retry(
                            "GET",
                            f"{self.BASE_URL}/recap-documents/{doc_id}/",
                        )
                        t = doc_detail.get("plain_text", "")
                        if t and t.strip():
                            text = t
                            break
                    if text:
                        break

            if not text:
                # Fallback: fetch recent entries
                entry_data = await self._request_with_retry(
                    "GET",
                    f"{self.BASE_URL}/docket-entries/",
                    params={"docket": docket_id, "page_size": "5"},
                )
                for entry in entry_data.get("results", []):
                    for doc in entry.get("recap_documents", []):
                        doc_id = doc.get("id")
                        if not doc_id:
                            continue
                        doc_detail = await self._request_with_retry(
                            "GET",
                            f"{self.BASE_URL}/recap-documents/{doc_id}/",
                        )
                        t = doc_detail.get("plain_text", "")
                        if t and t.strip():
                            text = t
                            break
                    if text:
                        break

            if not text:
                return None

            # Fetch docket metadata
            docket = await self._request_with_retry(
                "GET", f"{self.BASE_URL}/dockets/{docket_id}/",
            )
            court_url = docket.get("court", "")
            court_name = ""
            if court_url:
                court_id = court_url.rstrip("/").split("/")[-1]
                try:
                    court_data = await self._request_with_retry(
                        "GET", f"{self.BASE_URL}/courts/{court_id}/",
                    )
                    court_name = court_data.get("full_name", "")
                except Exception:
                    pass

            return {
                "text": text,
                "format": "text",
                "case_name": docket.get("case_name", ""),
                "court": court_name,
                "date_filed": docket.get("date_filed", ""),
                "docket_number": docket.get("docket_number", ""),
                "citations": [],
                "source_url": matched_url,
            }

        except Exception:
            pass

        return None

    async def _resolve_opinion_text_with_metadata(
        self, matched_url: str, *, prefer_html: bool = False,
    ) -> dict[str, Any] | None:
        """Resolve an opinion URL to text + metadata (async).

        When prefer_html=True, returns raw HTML with format="html" if
        available; falls back to plain text (format="text"), then PDF
        download (format="pdf").
        """
        cluster_match = re.search(r"/opinion/(\d+)/", matched_url)
        if not cluster_match:
            return None

        cluster_id = cluster_match.group(1)

        try:
            cluster = await self._request_with_retry(
                "GET", f"{self.BASE_URL}/clusters/{cluster_id}/"
            )

            text = None
            fmt = "text"
            for op_url in cluster.get("sub_opinions", []):
                op_id = op_url.rstrip("/").split("/")[-1]
                opinion = await self._request_with_retry(
                    "GET", f"{self.BASE_URL}/opinions/{op_id}/"
                )
                t, f = _extract_opinion_text(opinion, prefer_html=prefer_html)
                if t:
                    text = t
                    fmt = f
                    break

            # PDF fallback when no text or HTML found
            if not text and prefer_html:
                pdf_path = cluster.get("filepath_pdf_with_extracted_text", "")
                if pdf_path:
                    pdf_url = f"{self.STORAGE_BASE}{pdf_path}"
                    assert self._session is not None
                    async with self._semaphore:
                        await self._rate_limit()
                        async with self._session.get(pdf_url) as resp:
                            resp.raise_for_status()
                            pdf_bytes = await resp.read()
                    return {
                        "text": None,
                        "pdf_bytes": pdf_bytes,
                        "format": "pdf",
                        "case_name": cluster.get("case_name", ""),
                        "court": "",
                        "date_filed": cluster.get("date_filed", ""),
                        "docket_number": "",
                        "citations": [],
                        "source_url": matched_url,
                    }

            if not text:
                return None

            # Extract citations from cluster
            citations = []
            for cite in cluster.get("citations", []):
                if isinstance(cite, str):
                    continue
                if isinstance(cite, dict):
                    vol = cite.get("volume", "")
                    rep = cite.get("reporter", "")
                    pg = cite.get("page", "")
                    if vol and rep and pg:
                        citations.append(f"{vol} {rep} {pg}")

            # Resolve court name from docket
            court_name = ""
            docket_number = ""
            docket_url = cluster.get("docket", "")
            if docket_url:
                docket_id = docket_url.rstrip("/").split("/")[-1]
                try:
                    docket = await self._request_with_retry(
                        "GET", f"{self.BASE_URL}/dockets/{docket_id}/",
                    )
                    docket_number = docket.get("docket_number", "")
                    court_url = docket.get("court", "")
                    if court_url:
                        court_id = court_url.rstrip("/").split("/")[-1]
                        court_data = await self._request_with_retry(
                            "GET", f"{self.BASE_URL}/courts/{court_id}/",
                        )
                        court_name = court_data.get("full_name", "")
                except Exception:
                    pass

            return {
                "text": text,
                "format": fmt,
                "case_name": cluster.get("case_name", ""),
                "court": court_name,
                "date_filed": cluster.get("date_filed", ""),
                "docket_number": docket_number,
                "citations": citations,
                "source_url": matched_url,
            }

        except Exception:
            pass

        return None

    async def get_pdf_url(self, matched_url: str) -> str | None:
        """Resolve a CL matched_url to a PDF download URL.

        For opinions: resolves via cluster API -> sub_opinions -> opinion API
        to find local_path (storage.courtlistener.com) or download_url.
        For RECAP dockets: fetches docket-entries via API to find
        recap_documents with filepath_local.

        Returns None if no PDF is available (HTML-only opinions, etc.).
        """
        if not matched_url:
            return None

        # Opinion URL: https://www.courtlistener.com/opinion/12345/slug/
        if "/opinion/" in matched_url:
            return await self._resolve_opinion_pdf(matched_url)

        # RECAP docket URL: /docket/12345/...
        docket_match = re.search(r"/docket/(\d+)/", matched_url)
        if not docket_match:
            return None

        docket_id = docket_match.group(1)

        # Check for entry number (second digit group): /docket/12345/67/
        docket_entry_match = re.search(r"/docket/\d+/(\d+)/", matched_url)

        try:
            if docket_entry_match:
                entry_number = docket_entry_match.group(1)
                entry_data = await self._request_with_retry(
                    "GET",
                    f"{self.BASE_URL}/docket-entries/",
                    params={"docket": docket_id, "entry_number": entry_number},
                )
                for entry in entry_data.get("results", []):
                    url = self._first_recap_doc_url(entry)
                    if url:
                        return url

            # Fallback: fetch recent entries for this docket
            entry_data = await self._request_with_retry(
                "GET",
                f"{self.BASE_URL}/docket-entries/",
                params={"docket": docket_id, "page_size": "5"},
            )
            for entry in entry_data.get("results", []):
                url = self._first_recap_doc_url(entry)
                if url:
                    return url

        except Exception:
            pass

        return None

    def _first_recap_doc_url(self, entry: dict[str, Any]) -> str | None:
        """Extract the PDF URL from the first recap_document in a docket entry."""
        for doc in entry.get("recap_documents", []):
            filepath = doc.get("filepath_local") or ""
            if filepath:
                return f"{self.STORAGE_BASE}{filepath}"
        return None

    async def _resolve_opinion_pdf(self, matched_url: str) -> str | None:
        """Resolve an opinion URL to a downloadable PDF URL via the API.

        Strategy: cluster API -> sub_opinions -> opinion API, then:
        1. local_path starting with "pdf/" -> storage.courtlistener.com/{path}
        2. download_url (external court site PDF link, skip .html/.xml)
        3. filepath_pdf_harvard on cluster -> storage (Harvard CAP scan)
        """
        # Extract cluster ID from /opinion/{cluster_id}/slug/
        cluster_match = re.search(r"/opinion/(\d+)/", matched_url)
        if not cluster_match:
            return None

        cluster_id = cluster_match.group(1)

        try:
            cluster = await self._request_with_retry(
                "GET", f"{self.BASE_URL}/clusters/{cluster_id}/"
            )
            for op_url in cluster.get("sub_opinions", []):
                op_id = op_url.rstrip("/").split("/")[-1]
                opinion = await self._request_with_retry(
                    "GET", f"{self.BASE_URL}/opinions/{op_id}/"
                )

                # Prefer local_path on storage (reliable, fast)
                local_path = opinion.get("local_path") or ""
                if local_path.startswith("pdf/"):
                    return f"{self.STORAGE_BASE}{local_path}"

                # Fall back to external download_url (skip obvious non-PDFs)
                download_url = opinion.get("download_url") or ""
                if download_url and not download_url.endswith(
                    (".html", ".htm", ".xml")
                ):
                    return download_url

            # Fall back to Harvard CAP PDF scan (cluster-level field)
            harvard_pdf = cluster.get("filepath_pdf_harvard") or ""
            if harvard_pdf:
                return f"{self.STORAGE_BASE}{harvard_pdf}"

        except Exception:
            pass

        return None
