"""CLI interface for citation verification."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .brief_pipeline import _DOWNLOADABLE_STATUSES
from .cache import VerificationCache
from .models import Status, VerificationResult
from .verifier import CitationVerifier

# Status -> display style.
# Phase 1 only emits Status.VERIFIED and Status.NOT_FOUND; the other entries
# are placeholders so Phase 3/4 don't trip on missing keys.
_STATUS_LABELS = {
    Status.VERIFIED: "[OK] VERIFIED",
    Status.VERIFIED_PARTIAL: "[OK] VERIFIED (partial)",
    Status.VERIFIED_VIA_RECAP: "[OK] VERIFIED (RECAP)",
    Status.VERIFIED_DOCKET_ONLY: "[OK] VERIFIED (docket only)",
    Status.WRONG_CASE: "[!] WRONG CASE",
    Status.CITE_UNCONFIRMED: "[!] CHECK CITE",
    Status.NOT_FOUND: "[X] NOT FOUND",
    Status.VERIFICATION_INCOMPLETE: "[?] VERIFICATION INCOMPLETE",
    Status.INSUFFICIENT_DATA: "[?] INSUFFICIENT DATA",
}


# Map old v0.2 status names from user-maintained workflow CSVs (in scratch/)
# onto v0.3 Status values. The CSV writer always emits v0.3 names; the reader
# is permissive for backward compatibility.
_LEGACY_STATUS_MAP = {
    "VERIFIED": Status.VERIFIED,
    "LIKELY_REAL": Status.VERIFIED,
    "POSSIBLE_MATCH": Status.VERIFIED,
    "NOT_FOUND": Status.NOT_FOUND,
}


def _read_status_from_csv(value: str) -> Status | None:
    """Accept both v0.3 status names and the four legacy names from
    user-maintained workflow CSVs in scratch/. Returns None for empty
    strings (so callers can skip rows with no status). Raises ValueError
    for an unrecognized non-empty value.
    """
    if not value:
        return None
    if value in _LEGACY_STATUS_MAP:
        return _LEGACY_STATUS_MAP[value]
    return Status(value)


def _matched_case_name(result: VerificationResult) -> str | None:
    """Matched CL caption via the VerificationResult.matched_case_name
    accessor (the stage-specific summary keys vary -- reading one key
    directly was design SS11 bug 1)."""
    return result.matched_case_name or None


def _stage_notes(result: VerificationResult) -> str:
    """Concatenated free-form notes for the resolving stage, if any."""
    if not result.resolution_path:
        return ""
    return result.resolution_path[-1].notes or ""


def _result_to_json_dict(result: VerificationResult) -> dict:
    """JSON shape for a single-citation verification result.

    The first ten fields are the canonical schema shared with the
    verify-batch CSV: ``citation``, ``status``, ``matched_cluster_id``,
    ``matched_docket_id``, ``matched_url``, ``matched_case_name``,
    ``matched_court_id``, ``matched_date_filed``, ``confidence``,
    ``diagnostics``.

    The tail (``candidates``, ``error``) is bonus debug data exposed
    only via ``--json``. Phase 1 doesn't carry candidates or a structured
    error channel on VerificationResult, so those are emitted as the
    empty default (``[]`` and ``None``) for shape stability. They become
    real again in Phase 3/4 when candidate tracking returns.
    """
    diagnostics: list[dict[str, str]] = []
    # v0.3 warnings (structured)
    for w in result.warnings or []:
        diagnostics.append({
            "category": w.category.value,
            "message": w.message,
        })
    # Legacy stage notes for backward-compat with downstream consumers
    notes = _stage_notes(result)
    if notes:
        diagnostics.append({"category": "info", "message": notes})

    # matched_court_id / matched_date_filed: the v0.3 result no longer
    # stores what CL matched; we surface what was *cited* (best-available
    # proxy for the JSON contract).
    court = None
    date = None
    if result.parsed_citation:
        court = result.parsed_citation.court
        if result.parsed_citation.year is not None:
            date = str(result.parsed_citation.year)

    return {
        "citation": result.citation_as_written,
        "status": result.status.value,
        "matched_cluster_id": result.final_ids.cluster_id,
        "matched_docket_id": result.final_ids.docket_id,
        "matched_url": result.final_ids.absolute_url,
        "matched_case_name": _matched_case_name(result),
        "matched_court_id": court,
        "matched_date_filed": date,
        "confidence": result.headline_confidence if result.headline_confidence is not None else 0.0,
        "diagnostics": diagnostics,
        # Phase 1 placeholders for the bonus debug fields.
        "candidates": [],
        "error": None,
    }


def _print_result(result: VerificationResult, json_mode: bool) -> None:
    if json_mode:
        # One JSON object per line so multi-citation output is NDJSON.
        print(json.dumps(_result_to_json_dict(result), ensure_ascii=False))
        return

    label = _STATUS_LABELS.get(result.status, result.status.value)
    confidence = result.headline_confidence if result.headline_confidence is not None else 0.0
    matched_name = _matched_case_name(result)
    print(f"\n  Citation: {result.citation_as_written}")
    print(f"  Status:   {label}")
    print(f"  Confidence: {confidence:.0%}")

    if matched_name:
        print(f"  Match:    {matched_name}")
    if result.final_ids.absolute_url:
        print(f"  URL:      {result.final_ids.absolute_url}")

    issue_lines: list[str] = []
    for w in result.warnings or []:
        issue_lines.append(f"[{w.category.value}] {w.message}")
    notes = _stage_notes(result)
    if notes:
        issue_lines.append(notes)
    if issue_lines:
        print("  Issues:")
        for line in issue_lines:
            print(f"    - {line}")

    # Candidates are not tracked on VerificationResult in Phase 1.
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="citation-verifier",
        description="Verify legal citations against CourtListener.",
    )
    parser.add_argument(
        "citations",
        nargs="*",
        help="Citation strings to verify",
    )
    parser.add_argument(
        "--file",
        "-f",
        help="File with one citation per line",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_mode",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the results cache",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Clear the results cache and exit",
    )
    args = parser.parse_args(argv)

    if args.clear_cache:
        cache = VerificationCache()
        count = cache.clear()
        print(f"Cache cleared ({count} entries removed).")
        return 0

    citations: list[str] = list(args.citations)
    if args.file:
        with open(args.file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    citations.append(line)

    if not citations:
        parser.error("No citations provided. Pass them as arguments or use --file.")

    cache = None if args.no_cache else VerificationCache()
    verifier = CitationVerifier()

    # Check cache for hits, collect misses for batch verification
    results: list[VerificationResult | None] = [None] * len(citations)
    to_verify: list[tuple[int, str]] = []

    for i, cite_text in enumerate(citations):
        if cache:
            cached = cache.get(cite_text)
            if cached:
                results[i] = cached
                continue
        to_verify.append((i, cite_text))

    if cache and len(citations) > len(to_verify) and to_verify:
        print(
            f"  Cache: {len(citations) - len(to_verify)} cached, "
            f"{len(to_verify)} to verify",
            file=sys.stderr if args.json_mode else sys.stdout,
        )

    if to_verify:
        if len(to_verify) == 1:
            # Single citation -- use sync path (simpler, no event loop needed)
            idx, cite_text = to_verify[0]
            result = verifier.verify(cite_text)
            results[idx] = result
            if cache:
                cache.put(cite_text, result)
        else:
            # Multiple citations -- use async batch verification
            uncached_cites = [cite for _, cite in to_verify]

            def _progress(done: int, total: int) -> None:
                print(
                    f"  Verifying {done}/{total}...",
                    file=sys.stderr if args.json_mode else sys.stdout,
                    flush=True,
                )

            batch_results = asyncio.run(
                verifier.verify_batch(uncached_cites, progress_callback=_progress)
            )
            for (idx, cite_text), result in zip(to_verify, batch_results):
                results[idx] = result
                if cache:
                    cache.put(cite_text, result)

    # Print all results in original order. Exit codes:
    #   0 = all citations verified or skipped
    #   1 = at least one NOT_FOUND (potential hallucination)
    #   2 = at least one VERIFICATION_INCOMPLETE (CL infra failure; rerun)
    #   3 = at least one INSUFFICIENT_DATA (parse too weak to verify; fix
    #       the input by adding court + year parenthetical and rerun)
    # Per design §2.8 "fail-closed at verifier integrity," INCOMPLETE and
    # INSUFFICIENT_DATA both mean "we couldn't tell," not "verified," so
    # CI callers must distinguish them from a confirmed-fake citation.
    # In a mixed batch, max() wins; INSUFFICIENT_DATA (3) > INCOMPLETE (2)
    # > NOT_FOUND (1) reflects "we don't know" outranking "we tried and
    # found nothing" — fix the most fundamental confidence issue first.
    # NOT_FOUND keeps exit 1 for backward compat with pre-Phase-5 callers
    # that treated any non-zero as "fake".
    exit_code = 0
    for result in results:
        assert result is not None
        _print_result(result, args.json_mode)
        if result.status == Status.NOT_FOUND:
            exit_code = max(exit_code, 1)
        elif result.status == Status.VERIFICATION_INCOMPLETE:
            exit_code = max(exit_code, 2)
        elif result.status == Status.INSUFFICIENT_DATA:
            exit_code = max(exit_code, 3)

    return exit_code


def verify_brief_main(argv: list[str] | None = None) -> int:
    """CLI for brief verification pipeline."""
    parser = argparse.ArgumentParser(
        prog="citation-verifier verify-brief",
        description="Verify citations in a legal brief working directory.",
    )
    parser.add_argument(
        "workdir",
        help="Brief working directory (contains claims.csv, citations_to_verify.txt)",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--wave1", action="store_true",
        help="Run wave 1 only (batch citation lookup + download)",
    )
    group.add_argument(
        "--wave2", action="store_true",
        help="Run wave 2 only (fallback search for misses)",
    )
    group.add_argument(
        "--merge", action="store_true",
        help="Merge verification results into claims.csv",
    )
    group.add_argument(
        "--check-quotes", action="store_true",
        help="Run verbatim quote checker on claims.csv",
    )
    group.add_argument(
        "--metadata-check", action="store_true",
        help="Run metadata sanity check on merged claims",
    )
    group.add_argument(
        "--report", action="store_true",
        help="Generate HTML report from assessed claims",
    )
    group.add_argument(
        "--full", action="store_true", default=True,
        help="Run full pipeline: wave1 + wave2 + merge (default)",
    )
    args = parser.parse_args(argv)

    from pathlib import Path
    from .brief_pipeline import (
        wave1_verify_and_download,
        wave2_fallback_and_download,
        merge_claims,
        full_pipeline,
    )

    workdir = Path(args.workdir)
    if not workdir.exists():
        print(f"Error: workdir does not exist: {workdir}", file=sys.stderr)
        return 1

    def _progress(done: int, total: int) -> None:
        print(f"  Verifying {done}/{total}...", flush=True)

    # Load citations from citations_to_verify.txt
    def _load_citations() -> list[str]:
        cite_file = workdir / "citations_to_verify.txt"
        if not cite_file.exists():
            print(f"Error: {cite_file} not found", file=sys.stderr)
            sys.exit(1)
        citations = []
        with open(cite_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    citations.append(line)
        return citations

    if args.metadata_check:
        from .brief_pipeline import metadata_check
        result = metadata_check(workdir)
        print(f"Metadata check: {result.total_claims} claims")
        print(f"  Name mismatches: {result.name_mismatches}")
        print(f"  NOT_FOUND: {result.not_found}")
        print(f"  No opinion: {result.no_opinion}")
        if result.flagged_claims:
            print(f"  Flagged for mandatory assessment ({len(result.flagged_claims)}):")
            for fc in result.flagged_claims:
                print(f"    - p.{fc['page']}: {fc['cited_case']} [{', '.join(fc['flags'])}]")
        if result.syllabus_items:
            print(f"  Syllabus check ({len(result.syllabus_items)}):")
            for si in result.syllabus_items:
                print(f'    - p.{si["page"]}: "{si["proposition"][:80]}" / Syllabus: "{si["syllabus"][:80]}"')
        return 0

    if args.report:
        from .brief_pipeline import generate_report
        import json as json_mod
        meta_path = workdir / "brief_metadata.json"
        meta = {}
        if meta_path.exists():
            meta = json_mod.loads(meta_path.read_text(encoding="utf-8"))
        report_path = generate_report(
            workdir,
            title=meta.get("title", ""),
            case_name=meta.get("case_name", ""),
            case_number=meta.get("case_number", ""),
            filed_date=meta.get("filed_date", ""),
            report_date=meta.get("report_date", ""),
        )
        print(f"Report generated: {report_path}")
        return 0

    if args.check_quotes:
        from .brief_pipeline import check_quotes
        stats = check_quotes(workdir)
        print(f"Quote check: {stats.total_claims} claims, {stats.checked} checked")
        print(f"  VERBATIM: {stats.verbatim}, CLOSE: {stats.close}, FABRICATED: {stats.fabricated}")
        print(f"  No quotes: {stats.no_quotes}, No opinion: {stats.no_opinion}")
        return 0

    if args.merge and not args.wave1 and not args.wave2 and not args.check_quotes:
        stats = merge_claims(workdir)
        print(f"Merge: {stats.matched} matched, {stats.unmatched} unmatched, "
              f"{stats.opinion_count} with opinions")
        for status, count in sorted(stats.statuses.items()):
            print(f"  {status}: {count}")
        if stats.unmatched_claims:
            print(f"  Unmatched claims ({len(stats.unmatched_claims)}):")
            for cite in stats.unmatched_claims:
                print(f"    - {cite}")
        return 0

    if args.wave1:
        citations = _load_citations()
        print(f"Wave 1: verifying {len(citations)} citations (quick lookup)...")
        result = asyncio.run(wave1_verify_and_download(workdir, citations, _progress))
        print(f"Wave 1 complete: {result.download_stats}")
        print(f"  Misses for wave 2: {len(result.miss_indices)}")
        return 0

    if args.wave2:
        citations = _load_citations()
        # Read wave1 results to find misses
        vr_path = workdir / "verification_results.csv"
        if not vr_path.exists():
            print("Error: run --wave1 first", file=sys.stderr)
            return 1
        import csv as csv_mod
        verified_cites = set()
        with open(vr_path, newline="", encoding="utf-8") as f:
            for row in csv_mod.DictReader(f):
                status = _read_status_from_csv(row.get("status", "") or "")
                if status is not None and status in _DOWNLOADABLE_STATUSES:
                    verified_cites.add(row.get("citation", ""))
        miss_indices = [
            i for i, c in enumerate(citations) if c not in verified_cites
        ]
        print(f"Wave 2: {len(miss_indices)} misses to resolve...")
        result = asyncio.run(wave2_fallback_and_download(
            workdir, citations, miss_indices, _progress,
        ))
        print(f"Wave 2 complete: {result.download_stats}")
        return 0

    # Default: full pipeline
    citations = _load_citations()
    print(f"Full pipeline: {len(citations)} citations...")
    result = asyncio.run(full_pipeline(workdir, citations, _progress))
    print(f"Wave 1: {result.wave1.download_stats}")
    print(f"  Misses: {len(result.wave1.miss_indices)}")
    print(f"Wave 2: {result.wave2.download_stats}")
    print(f"Merge: {result.merge.matched} matched, {result.merge.unmatched} unmatched")
    if result.merge.unmatched_claims:
        print(f"  Unmatched claims ({len(result.merge.unmatched_claims)}):")
        for cite in result.merge.unmatched_claims:
            print(f"    - {cite}")
    return 0


_VERIFY_BATCH_OUTPUT_COLUMNS = [
    "citation",
    "status",
    "matched_cluster_id",
    "matched_docket_id",
    "matched_url",
    "matched_case_name",
    "matched_court_id",
    "matched_date_filed",
    "confidence",
    "diagnostics_json",
]


def _result_to_row(result: VerificationResult) -> dict[str, str]:
    diagnostics: list[dict[str, str]] = []
    for w in result.warnings or []:
        diagnostics.append({"category": w.category.value, "message": w.message})
    notes = _stage_notes(result)
    if notes:
        diagnostics.append({"category": "info", "message": notes})

    # The new schema dropped matched_court / matched_date — fall back to
    # the parsed citation for the CSV columns (best-available proxy).
    court = ""
    date = ""
    if result.parsed_citation:
        court = result.parsed_citation.court or ""
        if result.parsed_citation.year is not None:
            date = str(result.parsed_citation.year)

    confidence = result.headline_confidence if result.headline_confidence is not None else 0.0

    return {
        "citation": result.citation_as_written,
        "status": result.status.value,
        "matched_cluster_id": (
            str(result.final_ids.cluster_id)
            if result.final_ids.cluster_id is not None
            else ""
        ),
        "matched_docket_id": (
            str(result.final_ids.docket_id)
            if result.final_ids.docket_id is not None
            else ""
        ),
        "matched_url": result.final_ids.absolute_url or "",
        "matched_case_name": _matched_case_name(result) or "",
        "matched_court_id": court,
        "matched_date_filed": date,
        "confidence": f"{confidence}",
        "diagnostics_json": json.dumps(diagnostics, ensure_ascii=False),
    }


def verify_propositions_main(argv: list[str] | None = None) -> int:
    """CLI for the proposition pipeline verbs (design §3).

    Usage: python -m citation_verifier verify-propositions <workdir> <verb>
    Verbs are idempotent; resume = rerun the verb. ASCII-only output.
    """
    parser = argparse.ArgumentParser(
        prog="citation-verifier verify-propositions",
        description="Run proposition-pipeline verbs over a workdir "
                    "(claims.csv per the design SS2 input contract).",
    )
    parser.add_argument("workdir", help="Pipeline working directory")
    parser.add_argument(
        "verb",
        choices=["extract", "verify", "merge", "check-quotes",
                 "crosscheck", "triage", "assess", "apply-assessments",
                 "report", "full"],
        help="extract = document -> claims.csv + TOA/body citation lists "
             "(LLM, needs --document); verify = wave1+wave2+downloads; "
             "merge = join claims to results + opinion linkage; "
             "check-quotes = quote verdicts + floors; crosscheck = "
             "TOA/court/pincite flags; triage = assessment depth per "
             "claim (--prescreen for Haiku hints); assess = LLM "
             "assessment jobs (jobs mode by default); "
             "apply-assessments = verdicts JSONL -> claims.csv with "
             "floors; report = claims.csv -> report.html (SS6.9 lanes); "
             "full = [extract ->] verify -> merge -> "
             "check-quotes -> crosscheck -> triage -> assess "
             "(-> apply -> report when verdicts are complete)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Rerun the verify/extract verb even if its output exists",
    )
    parser.add_argument(
        "--citations-file",
        help="Explicit citation list (one per line) instead of deriving "
             "from claims.csv cited_case",
    )
    parser.add_argument(
        "--prompt-version", default=None,
        help="Assess/apply prompt version (default assess-v2: two-axis "
             "support + report blocks; pass assess-v1 for the legacy "
             "single-color prompt)",
    )
    parser.add_argument(
        "--replay",
        help="Replay LLM verdicts from a recorded JSONL (offline) "
             "instead of jobs mode",
    )
    parser.add_argument(
        "--document",
        help="Source document (brief/motion PDF or text) for the extract "
             "verb / full chain",
    )
    parser.add_argument(
        "--executor", choices=["jobs", "sdk", "api"], default="jobs",
        help="LLM transport for extract/assess: jobs = write jobs file "
             "for Agent-tool subagents (in-session default); sdk = "
             "headless claude-agent-sdk (requires `claude login` "
             "credentials); api = direct Anthropic Messages API, opinion "
             "text inlined (requires ANTHROPIC_API_KEY in .env)",
    )
    parser.add_argument(
        "--model", default="opus",
        help="Model for the sdk/api executors (default opus; the api "
             "executor pins aliases to explicit model IDs)",
    )
    parser.add_argument(
        "--batch", action="store_true",
        help="With --executor api: submit one Message Batch (50%% off, "
             "async) instead of concurrent live calls",
    )
    parser.add_argument(
        "--prescreen", action="store_true",
        help="Triage: run Haiku summary-hint prescreen jobs for long "
             "opinions (default off pending the A/B re-run)",
    )
    parser.add_argument(
        "--route", choices=["single", "hybrid"], default="single",
        help="assess routing: single = one model (default); hybrid = "
             "Sonnet single-claim fast-track + Opus escalation "
             "(cost-audit F2; requires --executor api or sdk)",
    )
    args = parser.parse_args(argv)

    from pathlib import Path

    from . import proposition_pipeline as pp

    workdir = Path(args.workdir)
    if not workdir.exists():
        print(f"Error: workdir does not exist: {workdir}", file=sys.stderr)
        return 1

    def _progress(done: int, total: int) -> None:
        print(f"  Verifying {done}/{total}...", flush=True)

    def _make_executor():
        """LLM transport for extract/assess. --replay wins (offline
        determinism first); None = the verb's jobs-mode default."""
        if args.replay:
            from .executor import RecordedExecutor
            return RecordedExecutor(args.replay)
        if args.executor == "sdk":
            from .executor import AgentSDKExecutor
            return AgentSDKExecutor(model=args.model, cwd=str(workdir))
        if args.executor == "api":
            from .executor import MessagesAPIExecutor
            return MessagesAPIExecutor(model=args.model, cwd=str(workdir),
                                       batch=args.batch)
        return None

    from .executor import ExecutorAuthError
    try:
        return _dispatch_proposition_verbs(args, workdir, pp, _progress,
                                           _make_executor)
    except ExecutorAuthError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _dispatch_proposition_verbs(args, workdir, pp, _progress,
                                _make_executor) -> int:
    """Verb dispatch for verify_propositions_main (split out so the
    AgentSDKAuthError handler wraps every LLM-verb call site)."""
    from pathlib import Path

    if args.verb == "extract" or (args.verb == "full" and args.document):
        if not args.document:
            print("Error: extract requires --document <path>",
                  file=sys.stderr)
            return 1
        estats = pp.run_extract(workdir, args.document,
                                executor=_make_executor(),
                                force=args.force)
        if estats is None:
            print("[OK] extract: claims.csv already exists "
                  "(use --force to redo)")
        elif estats.pending:
            print("[OK] extract: PENDING -> jobs/extract.json "
                  "(dispatch an agent, append the verdict to "
                  "jobs/extract_results.jsonl, then rerun)")
            return 0  # full stops here until the verdict lands
        else:
            print(f"[OK] extract: {estats.claims} claims, "
                  f"{estats.toa} TOA citations, "
                  f"{estats.body} body citations")

    if args.verb in ("verify", "full"):
        citations = None
        if args.citations_file:
            citations = [
                ln.strip()
                for ln in Path(args.citations_file).read_text(
                    encoding="utf-8").splitlines()
                if ln.strip() and not ln.startswith("#")
            ]
        result = asyncio.run(pp.run_verify(
            workdir, citations=citations, force=args.force,
            progress_callback=_progress))
        if result is None:
            print("[OK] verify: already done (use --force to rerun)")
        else:
            print(f"[OK] verify: wave1 misses="
                  f"{len(result.wave1.miss_indices)}, downloads="
                  f"{result.wave1.download_stats} / "
                  f"{result.wave2.download_stats}")

    if args.verb in ("merge", "full"):
        stats = pp.run_merge(workdir)
        print(f"[OK] merge: {stats.matched} matched, "
              f"{stats.unmatched} unmatched, "
              f"{stats.opinion_count} opinion files linked")
        if stats.unmatched_claims:
            for c in stats.unmatched_claims:
                print(f"  UNMATCHED: {c[:80]}")

    if args.verb in ("check-quotes", "full"):
        qstats = pp.run_check_quotes(workdir)
        print(f"[OK] check-quotes: {qstats.total_claims} claims checked")

    if args.verb in ("crosscheck", "full"):
        xstats = pp.run_crosscheck(workdir)
        print(f"[OK] crosscheck: {xstats.total} claims, "
              f"{xstats.toa_mismatches} TOA, "
              f"{xstats.court_mismatches} court, "
              f"{xstats.pincite_flags} pincite flags")

    if args.verb in ("triage", "full"):
        tstats = pp.run_triage(workdir, prescreen=args.prescreen,
                               executor=_make_executor())
        print(f"[OK] triage: {tstats.full} full, {tstats.fast} fast, "
              f"{tstats.skipped} deterministic")
        if tstats.prescreen_pending:
            print(f"  PENDING: {tstats.prescreen_pending} prescreen "
                  f"jobs -> jobs/prescreen.json (dispatch agents, "
                  f"append to jobs/prescreen_results.jsonl, rerun)")

    # Product default is assess-v2 (two-axis + report blocks). The library
    # constant DEFAULT_PROMPT_VERSION stays assess-v1 for the frozen-cassette
    # replay tests; the CLI is the user-facing surface and defaults to v2
    # (shakedown 2026-06-13: a naive `full --document` was silently getting
    # the thin v1 cards). Pass --prompt-version assess-v1 to opt back.
    prompt_version = args.prompt_version or pp.ASSESS_V2_PROMPT_VERSION

    if args.verb in ("assess", "full"):
        if args.route == "hybrid":
            if not args.replay and args.executor not in ("api", "sdk"):
                print("Error: --route hybrid requires --executor api or sdk "
                      "(jobs mode is interactive; it can't do the two-pass "
                      "escalation in-process)", file=sys.stderr)
                return 1
            if args.replay:
                from .executor import RecordedExecutor
                fast_ex = full_ex = RecordedExecutor(args.replay)
            elif args.executor == "sdk":
                from .executor import AgentSDKExecutor
                fast_ex = AgentSDKExecutor(model="claude-sonnet-5",
                                           cwd=str(workdir))
                full_ex = AgentSDKExecutor(model=args.model, cwd=str(workdir))
            else:  # api
                from .executor import MessagesAPIExecutor
                fast_ex = MessagesAPIExecutor(model="claude-sonnet-5",
                                              cwd=str(workdir),
                                              batch=args.batch)
                full_ex = MessagesAPIExecutor(model=args.model,
                                              cwd=str(workdir),
                                              batch=args.batch)
            astats = pp.run_assess_hybrid(
                workdir, fast_executor=fast_ex, full_executor=full_ex,
                prompt_version=prompt_version)
            print(f"[OK] assess (hybrid): {astats.eligible} eligible, "
                  f"{astats.done} done, fast_kept={astats.fast_kept}, "
                  f"escalated={astats.escalated}, "
                  f"escalated_cost=${astats.escalated_cost_usd:.4f}, "
                  f"pending={astats.pending}")
        else:
            astats = pp.run_assess(workdir, executor=_make_executor(),
                                   prompt_version=prompt_version)
            print(f"[OK] assess: {astats.eligible} eligible, "
                  f"{astats.done} done, {astats.pending} pending, "
                  f"{astats.skipped_deterministic} deterministic")
        if astats.pending:
            print(f"  PENDING: dispatch agents over jobs/assess.json, "
                  f"append verdicts to jobs/assess_results.jsonl, then "
                  f"rerun this verb to ingest")
            return 0  # full stops here until verdicts are complete

    if args.verb in ("apply-assessments", "full"):
        pstats = pp.run_apply_assessments(workdir,
                                          prompt_version=prompt_version)
        print(f"[OK] apply-assessments: {pstats.applied} applied, "
              f"{pstats.invalid} invalid, {pstats.missing} missing")
        for cid in pstats.invalid_claims:
            print(f"  INVALID verdict (not applied): {cid}")

    if args.verb in ("report", "full"):
        rstats = pp.run_report(workdir)
        print(f"[OK] report: {rstats.path} -- {rstats.findings} findings, "
              f"{rstats.check_cite} check-cite, {rstats.verified} "
              f"verified, {rstats.unable} unable-to-verify")

    return 0


def verify_batch_main(argv: list[str] | None = None) -> int:
    """CLI for batch citation verification from a CSV.

    Reads citations from a CSV column and writes verification results
    to an output CSV with a stable column schema.
    """
    import csv as csv_mod
    from pathlib import Path

    from .models import ParsedCitation
    from .parser import parse_citation

    parser = argparse.ArgumentParser(
        prog="citation-verifier verify-batch",
        description="Verify a batch of citations from a CSV file.",
    )
    parser.add_argument("input", help="Input CSV file with a citation column")
    parser.add_argument(
        "--column",
        required=True,
        help="Name of the column in the input CSV containing citation strings",
    )
    parser.add_argument(
        "--name-column",
        default=None,
        help="Optional column name with the case name (used to enrich the parsed citation)",
    )
    parser.add_argument(
        "--court-column",
        default=None,
        help="Optional column name with the court ID (e.g. 'scotus', 'ca2')",
    )
    parser.add_argument(
        "--year-column",
        default=None,
        help="Optional column name with the citation year (integer)",
    )
    parser.add_argument(
        "--output",
        "-o",
        required=True,
        help="Output CSV path",
    )
    parser.add_argument(
        "--quick-only",
        action="store_true",
        help="Skip opinion-search/RECAP fallback (citation-lookup only)",
    )
    args = parser.parse_args(argv)

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"Error: input file does not exist: {in_path}", file=sys.stderr)
        return 1

    with in_path.open(encoding="utf-8", newline="") as f:
        reader = csv_mod.DictReader(f)
        if args.column not in (reader.fieldnames or []):
            print(
                f"Error: column '{args.column}' not found in {in_path}. "
                f"Available columns: {reader.fieldnames}",
                file=sys.stderr,
            )
            return 1
        rows = list(reader)

    citations: list[str] = [(row.get(args.column) or "").strip() for row in rows]

    use_metadata = bool(args.name_column or args.court_column or args.year_column)
    parsed_citations: list[ParsedCitation | None] | None = None
    if use_metadata:
        parsed_citations = []
        for cite, row in zip(citations, rows):
            base = parse_citation(cite) if cite else ParsedCitation(raw_text=cite)
            if args.name_column:
                name_val = (row.get(args.name_column) or "").strip()
                if name_val:
                    base.case_name = name_val
            if args.court_column:
                court_val = (row.get(args.court_column) or "").strip()
                if court_val:
                    base.court = court_val
            if args.year_column:
                year_val = (row.get(args.year_column) or "").strip()
                if year_val:
                    try:
                        base.year = int(year_val)
                    except ValueError:
                        pass
            parsed_citations.append(base)

    verifier = CitationVerifier()

    def _progress(done: int, total: int) -> None:
        print(f"  Verifying {done}/{total}...", file=sys.stderr, flush=True)

    kwargs: dict = {
        "progress_callback": _progress,
        "quick_only": args.quick_only,
    }
    if parsed_citations is not None:
        kwargs["parsed_citations"] = parsed_citations

    results = asyncio.run(verifier.verify_batch(citations, **kwargs))

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv_mod.DictWriter(f, fieldnames=_VERIFY_BATCH_OUTPUT_COLUMNS)
        writer.writeheader()
        for result in results:
            writer.writerow(_result_to_row(result))

    print(f"Wrote {len(results)} rows to {out_path}", file=sys.stderr)
    return 0


_AUDIT_MISSES_ADDED_COLUMNS = [
    "fallback_status",
    "fallback_path",
    "fallback_confidence",
    "fallback_url",
    "fallback_matched_name",
    "fallback_court_id",
    "fallback_date_filed",
]


def _classify_fallback_path(
    quick_result: VerificationResult | None,
    full_result: VerificationResult | None,
) -> str:
    """Attribute which production path resolved (or didn't resolve) a citation.

    - ``citation-lookup`` if the quick (citation-lookup-only) pass already
      returned a verified-class hit. The chunking fix matters here: a
      citation that missed in an old run can recover in a fresh one
      without needing search fallback.
    - ``RECAP`` if the full pipeline returned a hit AND any warning or
      stage-note on the result mentions RECAP. Phase 1 doesn't yet emit
      structured RECAP-source metadata, so we look at the resolving
      stage's notes for "recap" (case-insensitive).
    - ``opinion-search`` if the full pipeline returned a hit with no
      RECAP signal — implying the opinion-search step resolved it.
    - ``no_match`` if the full pipeline still returned NOT_FOUND (or
      another non-verified status).
    """
    if quick_result is not None and quick_result.status in _DOWNLOADABLE_STATUSES:
        return "citation-lookup"
    if full_result is None or full_result.status not in _DOWNLOADABLE_STATUSES:
        return "no_match"
    # Look at the resolving stage's notes for a recap marker, and at any
    # warnings with category names mentioning recap.
    notes = (_stage_notes(full_result) or "").lower()
    has_recap = "recap" in notes
    if not has_recap:
        for w in full_result.warnings or []:
            if "recap" in w.category.value.lower() or "recap" in w.message.lower():
                has_recap = True
                break
    return "RECAP" if has_recap else "opinion-search"


def audit_misses_main(argv: list[str] | None = None) -> int:
    """CLI for auditing CL misses against the full production fallback path.

    Two-pass design:
      1. ``verify_batch(quick_only=True)`` — citation-lookup with chunking.
         Catches anything that recovers from the bare lookup endpoint
         (e.g. fixed by recent chunking changes). Path attributed as
         ``citation-lookup``.
      2. ``verify_batch()`` (full) on the quick-misses — runs opinion-
         search and, for federal courts, RECAP.
    """
    import csv as csv_mod
    from pathlib import Path

    from .models import ParsedCitation
    from .parser import parse_citation

    parser = argparse.ArgumentParser(
        prog="citation-verifier audit-misses",
        description="Audit CL misses against the full fallback pipeline.",
    )
    parser.add_argument("input", help="Input CSV with one citation per row")
    parser.add_argument(
        "--column",
        required=True,
        help="Column with the citation string",
    )
    parser.add_argument(
        "--name-column",
        default=None,
        help="Optional column with the case name (improves search recall)",
    )
    parser.add_argument(
        "--court-column",
        default=None,
        help="Optional column with the court ID (e.g. 'ca9', 'dcd')",
    )
    parser.add_argument(
        "--year-column",
        default=None,
        help="Optional column with the citation year",
    )
    parser.add_argument(
        "--output",
        "-o",
        required=True,
        help="Output CSV path (input columns passed through, fallback_* added)",
    )
    args = parser.parse_args(argv)

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"Error: input file does not exist: {in_path}", file=sys.stderr)
        return 1

    with in_path.open(encoding="utf-8", newline="") as f:
        reader = csv_mod.DictReader(f)
        in_fieldnames = list(reader.fieldnames or [])
        if args.column not in in_fieldnames:
            print(
                f"Error: column '{args.column}' not found. Available: {in_fieldnames}",
                file=sys.stderr,
            )
            return 1
        rows = list(reader)

    citations: list[str] = [(row.get(args.column) or "").strip() for row in rows]

    use_metadata = bool(args.name_column or args.court_column or args.year_column)
    parsed_citations: list[ParsedCitation | None] | None = None
    if use_metadata:
        parsed_citations = []
        for cite, row in zip(citations, rows):
            base = parse_citation(cite) if cite else ParsedCitation(raw_text=cite)
            if args.name_column:
                v = (row.get(args.name_column) or "").strip()
                if v:
                    base.case_name = v
            if args.court_column:
                v = (row.get(args.court_column) or "").strip()
                if v:
                    base.court = v
            if args.year_column:
                v = (row.get(args.year_column) or "").strip()
                if v:
                    try:
                        base.year = int(v)
                    except ValueError:
                        pass
            parsed_citations.append(base)

    verifier = CitationVerifier()

    def _progress(label: str):
        def _cb(done: int, total: int) -> None:
            print(f"  [{label}] {done}/{total}...", file=sys.stderr, flush=True)
        return _cb

    # Pass 1: quick (citation-lookup with chunking only)
    quick_kwargs: dict = {
        "progress_callback": _progress("quick"),
        "quick_only": True,
    }
    if parsed_citations is not None:
        quick_kwargs["parsed_citations"] = parsed_citations
    quick_results = asyncio.run(verifier.verify_batch(citations, **quick_kwargs))

    # Identify the indices that need the full pipeline.  NOT_FOUND is
    # the obvious case (quick lookup didn't find it; try full pipeline).
    # VERIFICATION_INCOMPLETE means the quick stage errored out (CL infra
    # failure -- 5xx / timeout per design §2.8); the full pipeline's
    # search-fallback path may recover via opinion-search or RECAP.
    miss_indices: list[int] = [
        i for i, r in enumerate(quick_results)
        if r.status in (Status.NOT_FOUND, Status.VERIFICATION_INCOMPLETE)
    ]

    full_results: dict[int, VerificationResult] = {}
    if miss_indices:
        miss_citations = [citations[i] for i in miss_indices]
        miss_parsed = (
            [parsed_citations[i] for i in miss_indices]
            if parsed_citations is not None else None
        )
        full_kwargs: dict = {"progress_callback": _progress("full")}
        if miss_parsed is not None:
            full_kwargs["parsed_citations"] = miss_parsed
        full_list = asyncio.run(verifier.verify_batch(miss_citations, **full_kwargs))
        for idx, res in zip(miss_indices, full_list):
            full_results[idx] = res

    # Build output rows: passthrough + fallback_* columns
    out_fieldnames = list(in_fieldnames) + [
        c for c in _AUDIT_MISSES_ADDED_COLUMNS if c not in in_fieldnames
    ]

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv_mod.DictWriter(f, fieldnames=out_fieldnames)
        writer.writeheader()
        for idx, row in enumerate(rows):
            quick = quick_results[idx]
            full = full_results.get(idx)
            chosen = full if full is not None else quick
            path = _classify_fallback_path(quick, full)
            chosen_conf = chosen.headline_confidence if chosen.headline_confidence is not None else 0.0
            chosen_court = ""
            chosen_date = ""
            if chosen.parsed_citation:
                chosen_court = chosen.parsed_citation.court or ""
                if chosen.parsed_citation.year is not None:
                    chosen_date = str(chosen.parsed_citation.year)
            out_row = dict(row)
            out_row["fallback_status"] = chosen.status.value
            out_row["fallback_path"] = path
            out_row["fallback_confidence"] = f"{chosen_conf}"
            out_row["fallback_url"] = chosen.final_ids.absolute_url or ""
            out_row["fallback_matched_name"] = _matched_case_name(chosen) or ""
            out_row["fallback_court_id"] = chosen_court
            out_row["fallback_date_filed"] = chosen_date
            writer.writerow(out_row)

    # Per-path summary to stderr
    counts: dict[str, int] = {}
    for idx in range(len(rows)):
        path = _classify_fallback_path(
            quick_results[idx], full_results.get(idx)
        )
        counts[path] = counts.get(path, 0) + 1
    print(
        f"Audit complete: {sum(counts.values())} rows. "
        + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    # Dispatch subcommands if first arg matches
    if len(sys.argv) > 1 and sys.argv[1] == "verify-brief":
        sys.exit(verify_brief_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "verify-propositions":
        sys.exit(verify_propositions_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "verify-batch":
        sys.exit(verify_batch_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "audit-misses":
        sys.exit(audit_misses_main(sys.argv[2:]))
    sys.exit(main())
