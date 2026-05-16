"""
Diagnose the 7 in_recap cases — why doesn't CL have an opinion cluster
ingested for these PACER dockets?

For each docket URL in unified_review.csv where coverage='in_recap',
this script:

  1. Fetches the docket metadata to confirm court_id, case_name.
  2. Fetches docket entries near the cited year (entry_date_filed__year=Y).
  3. For each candidate entry's recap_documents, captures:
       - is_free_on_pacer (free RECAP'd doc vs paywalled)
       - description (doc type: Memorandum Opinion, Order, etc.)
       - page_count
       - has_plain_text (OCR done vs not)
       - has_ocr (whether plain_text was OCR-derived)
  4. Classifies the dominant reason no opinion cluster exists:
       - recap_doc_paywalled       no free document available
       - recap_doc_not_opinion_typed   description doesn't trigger opinion scraper
       - recap_doc_no_text         document is free but no OCR/plain_text yet
       - recap_doc_court_not_scraped   CL's opinion scraper not active for this court
       - recap_doc_other           catch-all

Output: recap_diagnosis.csv  (one row per cited case)

The output is consumed by 17_build_unified_review.py to refine the
cl_docket_only_no_cluster diagnosis with a more specific subreason.
"""
from __future__ import annotations

import asyncio
import csv
import re
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv

ROOT = Path(__file__).resolve().parents[3]
load_dotenv(find_dotenv(usecwd=False), override=True)
sys.path.insert(0, str(ROOT / "src"))

from citation_verifier.client import AsyncCourtListenerClient  # noqa: E402

HERE = Path(__file__).parent
UNIFIED_CSV = HERE / "unified_review.csv"
OUT_CSV = HERE / "recap_diagnosis.csv"


# Courts known to be in CL's opinion scraper (from
# https://www.courtlistener.com/coverage/ — federal districts). This list
# is approximate; if a court appears here we don't assume scraper
# coverage is the problem. If a court is missing, it's a likely cause.
# Conservative — only flag a court_not_scraped when we have strong reason.


_URL_DOCKET_RE = re.compile(r"/docket/(\d+)/")
_URL_DOC_NUMBER_RE = re.compile(r"/docket/\d+/(\d+)/")


def parse_url(url: str) -> tuple[str, str]:
    """Return (docket_id, document_number_or_empty) from a CL URL."""
    if not url:
        return "", ""
    d = _URL_DOCKET_RE.search(url)
    dn = _URL_DOC_NUMBER_RE.search(url)
    return (d.group(1) if d else ""), (dn.group(1) if dn else "")


def best_url(row: dict[str, str]) -> str:
    """Per the precedence used elsewhere: user_corrected_url > stage_b_url > stage_a_url."""
    return (
        (row.get("user_corrected_url") or "").strip()
        or (row.get("p4c_stage_b_url") or "").strip()
        or (row.get("p4c_stage_a_url") or "").strip()
    )


async def fetch_docket(client: AsyncCourtListenerClient, docket_id: str) -> dict[str, Any] | None:
    try:
        return await client._request_with_retry(
            "GET", f"{client.BASE_URL}/dockets/{docket_id}/", timeout=30
        )
    except Exception as e:
        return {"_fetch_error": str(e)[:200]}


async def fetch_entries(
    client: AsyncCourtListenerClient,
    docket_id: str,
    params_extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Fetch docket entries. `params_extra` lets the caller supply
    entry_number, date_filed__year, etc."""
    params: dict[str, Any] = {"docket": docket_id, "page_size": 100}
    if params_extra:
        params.update(params_extra)
    try:
        data = await client._request_with_retry(
            "GET", f"{client.BASE_URL}/docket-entries/", params=params, timeout=30
        )
        return data.get("results", []) if isinstance(data, dict) else []
    except Exception:
        return []


async def fetch_opinion_entries(
    client: AsyncCourtListenerClient,
    docket_id: str,
    entry_number: str,
    cited_year: str,
) -> tuple[list[dict[str, Any]], str]:
    """Return (entries, lookup_method).

    Strategy:
      1. If we have entry_number from the URL, filter on that — most precise.
      2. Else filter by date_filed__year and prefer entries whose
         recap_documents have is_available=true (they're real docs, not
         RSS-feed stubs that lack doc info).
      3. If neither yields a candidate, fall back to paginating the whole
         docket and looking for available + opinion-typed entries.
    """
    if entry_number:
        entries = await fetch_entries(client, docket_id, {"entry_number": entry_number})
        if entries:
            return entries, f"entry_number={entry_number}"

    if cited_year:
        entries = await fetch_entries(client, docket_id, {"date_filed__year": cited_year})
        # Prefer entries with at least one available doc
        with_doc = [
            e for e in entries
            if any((d or {}).get("is_available") for d in (e.get("recap_documents") or []))
        ]
        if with_doc:
            return with_doc, f"date_filed__year={cited_year} (filtered to available)"
        if entries:
            return entries, f"date_filed__year={cited_year} (none available)"

    # Fall back: scan all entries (up to 200) looking for available + opinion-ish ones
    entries = await fetch_entries(client, docket_id, {})
    avail_op = []
    for e in entries:
        desc = e.get("description") or ""
        docs = e.get("recap_documents") or []
        if not any((d or {}).get("is_available") for d in docs):
            continue
        if _OPINION_DESCRIPTION_RE.search(desc):
            avail_op.append(e)
    if avail_op:
        return avail_op, "scanned (available + opinion-typed)"
    return entries, "scanned (no specific match)"


async def fetch_recap_document(
    client: AsyncCourtListenerClient,
    doc_id: int | str,
) -> dict[str, Any] | None:
    try:
        return await client._request_with_retry(
            "GET", f"{client.BASE_URL}/recap-documents/{doc_id}/", timeout=30
        )
    except Exception:
        return None


_OPINION_DESCRIPTION_RE = re.compile(
    r"\b(opinion|memorandum|memorandum\s+and\s+order|memorandum\s+opinion|"
    r"findings\s+of\s+fact|order\s+granting|order\s+denying|report\s+and\s+recommendation|"
    r"r\s*&\s*r|judgment\s+as\s+a\s+matter|amended\s+order|amended\s+memorandum)\b",
    re.IGNORECASE,
)


def classify(documents: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
    """Given a candidate document set, return (subreason, detail, summary).

    Looks at the most opinion-like document in the set (preferring those
    whose description matches the opinion regex; falling back to the
    largest doc by page_count).
    """
    if not documents:
        return "recap_doc_missing", "no candidate document found in expected year", {}

    # Rank by: (available, opinion-like, page_count). Entry descriptions
    # are more useful than recap_document descriptions (which are often
    # empty) — we copied the entry description into _entry_description.
    def score(d: dict[str, Any]) -> tuple[int, int, int]:
        avail = 1 if d.get("is_available") else 0
        desc = (d.get("description") or "") + " " + (d.get("_entry_description") or "")
        opinion_like = 1 if _OPINION_DESCRIPTION_RE.search(desc) else 0
        try:
            pc = int(d.get("page_count") or 0)
        except (TypeError, ValueError):
            pc = 0
        return (avail, opinion_like, pc)

    documents = sorted(documents, key=score, reverse=True)
    best = documents[0]
    desc = (best.get("description") or "") or (best.get("_entry_description") or "")
    is_available = bool(best.get("is_available"))
    is_free = bool(best.get("is_free_on_pacer"))
    plain_text = best.get("plain_text") or ""
    page_count = best.get("page_count")
    ocr_status = best.get("ocr_status")

    summary = {
        "best_doc_id": best.get("id"),
        "best_description": desc[:200],
        "is_available": is_available,
        "is_free_on_pacer": is_free,
        "page_count": page_count,
        "has_plain_text": bool(plain_text.strip()) if isinstance(plain_text, str) else False,
        "ocr_status": ocr_status,
        "n_candidate_docs": len(documents),
    }

    # Classification logic
    if not is_available:
        return (
            "recap_doc_unavailable",
            f"no available recap_document on this entry (is_available=false). "
            f"PACER has the doc but CL hasn't received a RECAP upload of it.",
            summary,
        )
    if is_available and not is_free:
        return (
            "recap_doc_paywalled",
            f"document is uploaded to CL but flagged paywalled "
            f"(is_free_on_pacer={is_free}). CL has the doc but not as 'free'.",
            summary,
        )
    if is_available and not summary["has_plain_text"]:
        return (
            "recap_doc_no_text",
            f"available PDF on CL but no plain_text extracted yet "
            f"(ocr_status={ocr_status}, page_count={page_count})",
            summary,
        )
    if not _OPINION_DESCRIPTION_RE.search(desc):
        return (
            "recap_doc_not_opinion_typed",
            f"available doc with text but description doesn't match opinion-typing "
            f"patterns: \"{desc[:120]}\"",
            summary,
        )
    return (
        "recap_doc_opinion_not_ingested",
        f"available opinion-typed doc with text exists on CL, but no opinion "
        f"cluster was ingested: \"{desc[:120]}\"",
        summary,
    )


async def main() -> int:
    rows = list(csv.DictReader(UNIFIED_CSV.open(encoding="utf-8")))
    recap_rows = [r for r in rows if r.get("coverage") == "in_recap"]
    print(f"Loaded {len(recap_rows)} in_recap rows from unified_review.csv")
    if not recap_rows:
        print("Nothing to diagnose")
        return 0

    out_rows: list[dict[str, Any]] = []
    t0 = time.monotonic()
    async with AsyncCourtListenerClient() as client:
        for i, r in enumerate(recap_rows, 1):
            url = best_url(r)
            docket_id, doc_number = parse_url(url)
            cited_year = (r.get("cited_year") or "").strip()
            cited_cite = (r.get("citation_string") or "").strip()
            cited_name = (r.get("cited_case_name") or "").strip()

            print(f"\n[{i}/{len(recap_rows)}] docket={docket_id}  doc#={doc_number or '-'}  "
                  f"{cited_name[:35]}  cite={cited_cite[:25]}  year={cited_year}")

            if not docket_id:
                out_rows.append({
                    "citing_cluster": r["citing_cluster"],
                    "citation_string": cited_cite,
                    "cited_case_name": cited_name,
                    "docket_id": "",
                    "docket_url": url,
                    "subreason": "no_docket_id_in_url",
                    "subreason_detail": "couldn't extract docket id from URL",
                })
                continue

            docket = await fetch_docket(client, docket_id)
            court_id = (docket or {}).get("court_id") or (docket or {}).get("court", "")
            if isinstance(court_id, str) and court_id.startswith("http"):
                court_id = court_id.rstrip("/").rsplit("/", 1)[-1]

            entries, lookup_method = await fetch_opinion_entries(
                client, docket_id, doc_number, cited_year
            )
            print(f"  {len(entries)} entries via {lookup_method}")
            candidate_docs: list[dict[str, Any]] = []
            for entry in entries:
                docs = entry.get("recap_documents", []) or []
                for d in docs:
                    if isinstance(d, dict):
                        candidate_docs.append({**d, "_entry_description": entry.get("description", "")})

            subreason, detail, summary = classify(candidate_docs)
            summary["lookup_method"] = lookup_method

            out_rows.append({
                "citing_cluster": r["citing_cluster"],
                "citation_string": cited_cite,
                "cited_case_name": cited_name,
                "cited_year": cited_year,
                "docket_id": docket_id,
                "docket_url": url,
                "court_id": court_id or "",
                "lookup_method": summary.get("lookup_method", ""),
                "n_candidate_docs": summary.get("n_candidate_docs", 0),
                "best_doc_id": summary.get("best_doc_id", ""),
                "best_description": summary.get("best_description", ""),
                "is_available": summary.get("is_available", ""),
                "is_free_on_pacer": summary.get("is_free_on_pacer", ""),
                "page_count": summary.get("page_count", ""),
                "has_plain_text": summary.get("has_plain_text", ""),
                "ocr_status": summary.get("ocr_status", ""),
                "subreason": subreason,
                "subreason_detail": detail,
            })
            print(f"  -> {subreason}: {detail[:120]}")

    elapsed = time.monotonic() - t0
    print(f"\nDone in {elapsed:.1f}s")

    fields = list(out_rows[0].keys())
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out_rows)
    print(f"wrote {OUT_CSV.name} ({len(out_rows)} rows)")

    # Subreason rollup
    from collections import Counter
    sub_counts = Counter(r["subreason"] for r in out_rows)
    print("\n=== Subreason distribution ===")
    for s, n in sub_counts.most_common():
        print(f"  {n:>2}  {s}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
