"""Build/refresh the frozen assessment corpora (design SS7).

Idempotent: safe to rerun. Each corpus directory under
tests/data/assessment_corpora/ becomes a conforming pipeline workdir:

  claims.csv                 input + deterministic-phase columns + claim_id
  opinions/                  downloaded opinion texts
  ground_truth.csv           expected verdicts + provenance
  jobs/assess_results.jsonl  recorded live LLM verdicts (the cassette)

Sources (all committed):
  withers     workdir frozen from the 2026-06-11 measurement run
              (tests/measure_withers_assessment.py); ground truth from
              withers_aberdeen_corpus.csv via withers_baseline_results.csv;
              cassette from withers_assessment_runs.jsonl (agent rows only;
              deterministic lanes are recomputed by scoring, not recorded).
  payne       claims rows + opinions from briefs/payne-proposed/ selected by
              tests/ab_test_cases.json; cassette from the recorded opus run
              tests/data/results/ab_opus-baseline_20260323-002228.jsonl.
  wainwright  same, from briefs/wainwright-v-state/.

prompt_version "assess-v1" = the established single-claim assessment prompt
(identical criteria text in ab_test_runner.build_prompt and
measure_withers_assessment.build_prompt).

Usage:
    venv/Scripts/python.exe tests/build_assessment_corpora.py
"""
from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from citation_verifier.executor import Verdict, append_verdict_jsonl
from citation_verifier.proposition_pipeline import (
    check_quotes,
    extract_quoted_spans,
)

PROJECT_ROOT = Path(__file__).parent.parent
DATA = Path(__file__).parent / "data"
CORPORA = DATA / "assessment_corpora"
PROMPT_VERSION = "assess-v1"

# Mirrors measure_withers_assessment.py's sample definition.
GREEN_SAMPLE = [
    "withers-01", "withers-07", "withers-21", "withers-39", "withers-26",
    "withers-36", "withers-46", "withers-11", "withers-20", "withers-30",
    "withers-50", "withers-29",
]

AB_SOURCE_DIRS = {
    "payne": PROJECT_ROOT / "briefs" / "payne-proposed",
    "wainwright": PROJECT_ROOT / "briefs" / "wainwright-v-state",
}
AB_OPUS_RUN = DATA / "results" / "ab_opus-baseline_20260323-002228.jsonl"


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def write_cassette(path: Path, verdicts: list[Verdict]) -> None:
    """Rewrite the rows this builder owns, keeping every other prompt version.

    One cassette file holds all prompt versions (RecordedExecutor keys on
    claim_id + prompt_version). The builder only regenerates PROMPT_VERSION
    rows from their recorded source run; the live re-records (assess-v2) are
    not reproducible from anything committed, so truncating the file would
    destroy them.
    """
    kept = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("prompt_version") != PROMPT_VERSION:
                kept.append(line)
    # Build beside the cassette and swap it in, so the rows being preserved
    # are never held only in memory: a crash mid-write leaves the original
    # file intact rather than losing recordings that cannot be regenerated.
    tmp = path.with_name(path.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    for v in verdicts:
        append_verdict_jsonl(tmp, v)
    if kept:
        with tmp.open("a", encoding="utf-8") as f:
            f.write("\n".join(kept) + "\n")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Withers
# ---------------------------------------------------------------------------

def build_withers() -> None:
    corpus = CORPORA / "withers"
    baseline = list(csv.DictReader(
        (DATA / "withers_baseline_results.csv").open(encoding="utf-8")))

    # Replay the measurement sample selection to recover row_id per claim row.
    sample = [r for r in baseline
              if r["label"] in ("yellow", "red") or r["row_id"] in GREEN_SAMPLE]

    claims = list(csv.DictReader((corpus / "claims.csv").open(encoding="utf-8")))
    assert len(claims) == len(sample), (len(claims), len(sample))

    if "claim_id" not in claims[0]:
        stamped = []
        for s, c in zip(sample, claims):
            assert c["proposition"] == s["proposition"], s["row_id"]
            assert c["cited_case"] == s["citation"], s["row_id"]
            stamped.append({"claim_id": s["row_id"], **c})
        write_csv(corpus / "claims.csv", stamped)
        print(f"withers: stamped claim_id on {len(stamped)} claims")
    else:
        print("withers: claim_id already present")

    # SS6.4 quote regeneration: the corpus's quoted_text was auto-extracted
    # from the exhibit's propositions at >=4 words (measurement-era rule).
    # Re-derive from the same source at >=2 words, then re-run the quote
    # checker so quote_check/quote_check_worst/quote_floor reflect the
    # current deterministic phase. Cassette keys (claim_id+prompt_version)
    # are untouched -- no re-record needed.
    claims = list(csv.DictReader((corpus / "claims.csv").open(encoding="utf-8")))
    from collections import Counter
    before = Counter(c.get("quote_check_worst", "") for c in claims)
    for c in claims:
        c["quoted_text"] = json.dumps(extract_quoted_spans(c["proposition"]))
    write_csv(corpus / "claims.csv", claims)
    q = check_quotes(corpus)
    claims = list(csv.DictReader((corpus / "claims.csv").open(encoding="utf-8")))
    after = Counter(c.get("quote_check_worst", "") for c in claims)
    print(f"withers: quote re-derivation -- worst counts before={dict(before)}")
    print(f"withers: quote re-derivation -- worst counts after ={dict(after)} "
          f"(checked={q.checked}, derived={q.derived_quotes})")

    # Ground truth: ALL 54 exhibit rows (the workdir holds 34; scoring only
    # scores rows present in claims.csv, the rest await corpus expansion).
    gt = [{
        "claim_id": r["row_id"],
        "scale": "withers_exhibit",
        "expected": r["label"],
        "exists": r["exists"],
        "hedged": r["hedged"],
        "notes": r["irregularity"],
        "provenance": "exhibit1_doc112-1.pdf via withers_aberdeen_corpus.csv",
    } for r in baseline]
    write_csv(corpus / "ground_truth.csv", gt)
    print(f"withers: ground_truth.csv {len(gt)} rows")

    # Cassette: agent rows only. Deterministic lanes (WRONG_CASE -> Red,
    # no-text -> Gray, located-no-opinion -> Yellow) are recomputed from
    # claims.csv state by the scorer -- they are not LLM verdicts.
    runs = [json.loads(line) for line in
            (DATA / "withers_assessment_runs.jsonl").open(encoding="utf-8")]
    verdicts = [Verdict(
        claim_id=r["row_id"],
        fields={"assessment": r["predicted"], "rationale": r["rationale"]},
        model="opus",
        prompt_version=PROMPT_VERSION,
        elapsed_s=r.get("elapsed_s", 0.0),
        cost_usd=r.get("cost_usd", 0.0),
    ) for r in runs if r.get("mode") == "agent" and r.get("predicted")]
    write_cassette(corpus / "jobs" / "assess_results.jsonl", verdicts)
    print(f"withers: cassette {len(verdicts)} agent verdicts")


# ---------------------------------------------------------------------------
# Payne / Wainwright (from ab_test_cases.json + recorded opus run)
# ---------------------------------------------------------------------------

def build_ab_corpus(source: str) -> None:
    corpus = CORPORA / source
    src_dir = AB_SOURCE_DIRS[source]
    cases = [c for c in json.loads(
        (Path(__file__).parent / "ab_test_cases.json").read_text(
            encoding="utf-8"))["cases"] if c["source"] == source]
    src_claims = list(csv.DictReader(
        (src_dir / "claims.csv").open(encoding="utf-8")))

    (corpus / "opinions").mkdir(parents=True, exist_ok=True)
    (corpus / "jobs").mkdir(exist_ok=True)

    rows = []
    for case in sorted(cases, key=lambda c: c["id"]):
        row = src_claims[case["id"]]
        # ab_test_cases.json ids are row indices into the brief's claims.csv
        # (verified 2026-06-11: 61/61 match on cited_case AND opinion_file).
        assert row["cited_case"] == case["cited_case"], (source, case["id"])
        assert row.get("opinion_file", "") == case["opinion_file"]
        claim_id = f"{source}-{case['id']:02d}"
        out = {"claim_id": claim_id, **row}
        out.setdefault("syllabus", "")
        out.setdefault("brief_sentence", "")
        rows.append(out)
        opinion = src_dir / case["opinion_file"]
        dest = corpus / case["opinion_file"]
        if not dest.exists():
            shutil.copy2(opinion, dest)
    write_csv(corpus / "claims.csv", rows)
    # Re-run the deterministic quote phase so every corpus carries the
    # CURRENT matcher's quote_check/quote_check_worst/quote_floor, not the
    # values frozen into the source brief when it was run.
    q = check_quotes(corpus)
    print(f"{source}: claims.csv {len(rows)} rows, "
          f"{len(list((corpus / 'opinions').iterdir()))} opinions; quotes "
          f"verbatim={q.verbatim} close={q.close} fabricated={q.fabricated}")

    gt = [{
        "claim_id": f"{source}-{c['id']:02d}",
        "scale": "internal",
        "expected": c["expected_assessment"],
        "exists": "",
        "hedged": "",
        "notes": c.get("notes", ""),
        "provenance": "ab_test_cases.json (human-reviewed ledger)",
    } for c in sorted(cases, key=lambda c: c["id"])]
    write_csv(corpus / "ground_truth.csv", gt)
    print(f"{source}: ground_truth.csv {len(gt)} rows")

    # Cassette from the recorded opus baseline. case_id alone is ambiguous
    # across sources (ids overlap); key by (case_id, cited_case).
    by_key = {(c["id"], c["cited_case"]): c for c in cases}
    verdicts = []
    for line in AB_OPUS_RUN.open(encoding="utf-8"):
        r = json.loads(line)
        case = by_key.get((r["case_id"], r["cited_case"]))
        if case is None:
            continue  # belongs to the other source
        verdicts.append(Verdict(
            claim_id=f"{source}-{case['id']:02d}",
            fields={"assessment": r["actual"], "rationale": r["rationale"]},
            model=r.get("model", "opus"),
            prompt_version=PROMPT_VERSION,
            elapsed_s=r.get("elapsed_s", 0.0),
            cost_usd=r.get("cost_usd", 0.0),
        ))
    assert len(verdicts) == len(cases), (source, len(verdicts), len(cases))
    write_cassette(corpus / "jobs" / "assess_results.jsonl", verdicts)
    print(f"{source}: cassette {len(verdicts)} verdicts")


def main() -> None:
    build_withers()
    for source in AB_SOURCE_DIRS:
        build_ab_corpus(source)
    print("done")


if __name__ == "__main__":
    main()
