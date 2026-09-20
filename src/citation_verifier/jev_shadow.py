"""Jev shadow logging: record what TypeSafe's Jev model says about each claim,
next to (never instead of) the LLM assessment.

Shadow means shadow: this verb writes ONE file, ``jobs/jev_shadow.jsonl``. It
never touches claims.csv, the verdicts, or the report, and a failure here can
never fail a run. The point is to grow a labelled pool -- every run's Opus
verdicts become ground truth for Jev's answers -- so the auto-Green gate can be
re-checked on briefs nobody tuned against (scratch/jev/LOOP.md: the binding
constraint is labelled negatives, not question wording).

Off by default. It sends the brief's propositions and opinion excerpts to a
third-party API, so it runs only when asked: the ``jev-shadow`` verb, or the
``full`` chain with ``JEV_SHADOW=1`` in the environment/.env. Needs
``TYPESAFE_API_KEY`` and ``pip install -e ".[jev]"``.

Per claim: one locator request (rank <=250 ID-tagged passages, TypeSafe's
"line-by-line search" pattern), then the rubric on the top passages under two
state framings. ~0.5 s and ~$0.0005 per claim. Analysis:
``tools/jev_shadow_report.py``.
"""
from __future__ import annotations

import csv
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# Bump on ANY change to the questions, the state layout, or the excerpting:
# rows logged under different versions are not comparable.
RUBRIC_VERSION = "jev-rubric-v1"
MODEL = "jev-1.13.0"  # pinned -- aliases move, tuned thresholds don't
TOP_K = 5
RESULTS_NAME = "jev_shadow.jsonl"

# AskFn(state, questions) -> {"answers": {...}, "input_tokens": int}
AskFn = Callable[[object, dict], dict]


def enabled() -> bool:
    """True when the full chain should run the shadow step."""
    from . import client as _cl_client  # noqa: F401  (import loads .env)
    return os.environ.get("JEV_SHADOW", "").strip().lower() in (
        "1", "true", "yes", "on")


# --------------------------------------------------------------------------
# Questions (jev-rubric-v1). Literal and atomic: Jev reads instructions at
# face value. `as_written` + `coverage` came out of the 2026-09-19 phrasing
# loop; relation/states_it/same_issue are the original gate, kept so old and
# new scores stay comparable.
# --------------------------------------------------------------------------

_AS_WRITTEN = {
    "type": "choice",
    "instructions": "Is `proposition`, exactly as written, fully supported "
                    "by `opinion`?",
    "criteria": {
        "fully": "The opinion supports the proposition exactly as written, "
                 "with nothing added or strengthened",
        "not_fully": "The proposition adds, strengthens, or changes "
                     "something compared with what the opinion says, or the "
                     "opinion does not address it",
    },
}
_COVERAGE = {
    "type": "choice",
    "instructions": "How much of `proposition` does `opinion` state or "
                    "directly imply?",
    "criteria": {
        "all_parts": "Every clause and qualifier of the proposition is "
                     "stated or directly implied by the opinion",
        "main_part_only": "The opinion supports the main point, but one "
                          "clause, qualifier, or detail of the proposition "
                          "is not in the opinion",
        "little_or_none": "The opinion does not state the main point of the "
                          "proposition",
    },
}

RUBRIC_FULL_STATE = {
    "as_written": _AS_WRITTEN,
    "coverage": _COVERAGE,
    "adds_specifics": {
        "type": "noul",
        "instructions": "Does `proposition` add a specific standard, "
                        "element, category, or example that does not appear "
                        "in `opinion`?",
    },
    "own_words": {
        "type": "noul",
        "instructions": "Could every legal rule in `proposition` be found in "
                        "the words of `opinion` without relying on any other "
                        "source?",
    },
    "relation": {
        "type": "choice",
        "instructions": "How does the court opinion in `opinion` relate to "
                        "the legal statement in `proposition`?",
        "criteria": {
            "supports": "The opinion states the proposition, or directly "
                        "implies it is true, as the court's own view",
            "partly": "The opinion addresses the same point, but the "
                      "proposition claims more than the opinion says or "
                      "omits a condition the opinion requires",
            "contradicts": "The opinion states the opposite of the "
                           "proposition or implies it is false",
            "says_nothing": "The opinion does not address what the "
                            "proposition asserts, either way",
        },
    },
    "same_issue": {
        "type": "noul",
        "instructions": "Does `opinion` discuss the same legal issue that "
                        "`proposition` is about?",
    },
    "states_it": {
        "type": "noul",
        "instructions": "Does `opinion` contain a passage that states "
                        "`proposition` or directly implies it is true?",
    },
}
# Same wording, leaner state (no brief_sentence). Averaging the two framings
# was the loop's champion; extra state measurably moves Jev's answers.
RUBRIC_LEAN_STATE = {"as_written": _AS_WRITTEN, "coverage": _COVERAGE}


# --------------------------------------------------------------------------
# Passages
# --------------------------------------------------------------------------

def _clean(raw: str) -> str:
    s = re.sub(r"<[^>]+>", " ", raw)
    s = re.sub(r"&\w+;", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def split_passages(raw: str, max_passages: int = 250, min_chars: int = 250,
                   max_chars: int = 1200) -> list[str]:
    """Opinion -> passages. Block tags / blank lines, fragments merged, long
    blocks cut at sentence ends, then neighbours merged until the count fits
    one Choice question (255-option cap)."""
    blocks = re.split(r"</p>|<p[ >]|</blockquote>|<blockquote|\n\s*\n", raw)
    out: list[str] = []
    for p in filter(None, (_clean(b) for b in blocks)):
        while len(p) > max_chars:
            cut = p.rfind(". ", 0, max_chars)
            cut = cut + 1 if cut > max_chars // 2 else max_chars
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        if out and len(out[-1]) < min_chars:
            out[-1] = out[-1] + " " + p
        else:
            out.append(p)
    while len(out) > max_passages:
        i = min(range(len(out) - 1),
                key=lambda k: len(out[k]) + len(out[k + 1]))
        out[i:i + 2] = [out[i] + " " + out[i + 1]]
    return out


def _pid(i: int) -> str:
    return f"P{i:03d}"


def _locator_questions(prop: str, n: int) -> dict:
    return {
        "where": {
            "type": "choice",
            "instructions": "Which passage of the court opinion best supports "
                            f'or addresses this legal statement: "{prop}"?',
            "criteria": {_pid(i): None for i in range(n)},
        },
        "exists": {
            "type": "noul",
            "instructions": "Does any passage of the court opinion state or "
                            f'directly imply this legal statement: "{prop}"?',
            "criteria": {
                "true": "At least one passage states or directly implies it",
                "false": "No passage addresses it",
            },
        },
    }


def _excerpt(passages: list[str], ranked: list[int], k: int = TOP_K) -> str:
    """Top-k passages plus one neighbour each side, in document order."""
    keep: set[int] = set()
    for i in ranked[:k]:
        keep.update(j for j in (i - 1, i, i + 1) if 0 <= j < len(passages))
    out, prev = [], None
    for i in sorted(keep):
        if prev is not None and i != prev + 1:
            out.append("[...]")
        out.append(passages[i])
        prev = i
    return " ".join(out)


# --------------------------------------------------------------------------
# The verb
# --------------------------------------------------------------------------

@dataclass
class ShadowStats:
    """Statistics from run_jev_shadow."""
    logged: int = 0
    already: int = 0      # logged on an earlier run under this rubric version
    skipped: int = 0      # no readable opinion text
    errors: int = 0
    input_tokens: int = 0
    error_claims: list[str] = field(default_factory=list)


def gate_eligible(claim: dict) -> bool:
    """Would the auto-Green gate even consider this claim? Recorded, never
    acted on. Deterministic signals only: a clean VERIFIED, no quote problem,
    no crosscheck flag."""
    flags = (claim.get("crosscheck_flags") or "").strip()
    return (claim.get("cl_status") == "VERIFIED"
            and claim.get("quote_check_worst", "") in
            ("VERBATIM", "NO_QUOTES", "")
            and not (claim.get("quote_floor") or "").strip()
            and flags in ("", "[]"))


def _default_ask() -> AskFn:
    try:
        from typesafe_sdk import TypeSafeClient
    except ImportError as e:  # pragma: no cover - depends on the extra
        raise RuntimeError(
            'typesafe-sdk is not installed: pip install -e ".[jev]"') from e
    from . import client as _cl_client  # noqa: F401  (loads .env)
    if not os.environ.get("TYPESAFE_API_KEY"):
        raise RuntimeError("TYPESAFE_API_KEY is not set (add it to .env)")
    sdk = TypeSafeClient(model=MODEL, timeout=120.0)

    def ask(state, questions):
        raw = sdk.system_one(state, questions).raw_http_response.json()
        return {"answers": raw["answers"],
                "input_tokens": raw["usage"]["input_tokens"]}
    return ask


def _assess_one(claim: dict, raw: str, ask: AskFn) -> dict:
    prop = claim.get("cited_for") or claim.get("proposition", "")
    passages = split_passages(raw)
    tokens = 0

    loc = ask("\n".join(f"{_pid(i)}| {p}" for i, p in enumerate(passages)),
              _locator_questions(prop, len(passages)))
    tokens += loc["input_tokens"]
    probs = loc["answers"]["where"]["probabilities"]
    ranked = sorted(range(len(passages)),
                    key=lambda i: probs.get(_pid(i), 0.0), reverse=True)
    excerpt = _excerpt(passages, ranked)

    full = ask({"proposition": prop,
                "brief_sentence": claim.get("brief_sentence", ""),
                "opinion": excerpt}, RUBRIC_FULL_STATE)
    lean = ask({"proposition": prop, "opinion": excerpt}, RUBRIC_LEAN_STATE)
    tokens += full["input_tokens"] + lean["input_tokens"]

    a, b = full["answers"], lean["answers"]
    return {
        "n_passages": len(passages),
        "top_passages": ranked[:TOP_K],
        "exists": loc["answers"]["exists"]["noul"],
        "answers": a,
        "answers_lean": b,
        # v0 = the original gate; v1 = the 2026-09-19 loop's champion.
        "gate_v0": min(a["relation"]["probabilities"].get("supports", 0.0),
                       a["states_it"]["noul"], a["same_issue"]["noul"]),
        "gate_v1": (a["as_written"]["probabilities"].get("fully", 0.0)
                    + b["as_written"]["probabilities"].get("fully", 0.0)) / 2,
        "input_tokens": tokens,
    }


def _already_logged(path: Path) -> set[str]:
    done: set[str] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("rubric_version") == RUBRIC_VERSION and "error" not in row:
                done.add(row.get("claim_id", ""))
    return done


def run_jev_shadow(workdir: Path, ask: AskFn | None = None) -> ShadowStats:
    """Log Jev's answers for every claim with opinion text.

    Idempotent: claims already logged under RUBRIC_VERSION are skipped, so
    resume = rerun. Per-claim failures are recorded and the loop continues.
    Reads claims.csv; writes only jobs/jev_shadow.jsonl.
    """
    workdir = Path(workdir)
    with open(workdir / "claims.csv", newline="", encoding="utf-8") as f:
        claims = list(csv.DictReader(f))

    out_path = workdir / "jobs" / RESULTS_NAME
    done = _already_logged(out_path)
    stats = ShadowStats()
    todo = []
    for c in claims:
        cid = c.get("claim_id", "")
        if cid in done:
            stats.already += 1
            continue
        rel = c.get("opinion_file", "")
        path = workdir / rel if rel else None
        if (not cid or path is None or not path.is_file()
                or path.suffix.lower() == ".pdf"):
            stats.skipped += 1
            continue
        todo.append((c, path))
    if not todo:
        return stats

    if ask is None:
        ask = _default_ask()
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "a", encoding="utf-8") as out:
        for c, path in todo:
            row = {"claim_id": c["claim_id"], "rubric_version": RUBRIC_VERSION,
                   "model": MODEL, "eligible": gate_eligible(c),
                   "at": datetime.now(timezone.utc).isoformat(
                       timespec="seconds")}
            t0 = time.perf_counter()
            try:
                raw = path.read_text(encoding="utf-8", errors="ignore")
                if not _clean(raw):
                    stats.skipped += 1
                    continue
                row.update(_assess_one(c, raw, ask))
                stats.logged += 1
                stats.input_tokens += row["input_tokens"]
            except Exception as e:  # shadow must never fail a run
                row["error"] = f"{type(e).__name__}: {e}"[:300]
                stats.errors += 1
                stats.error_claims.append(c["claim_id"])
            row["latency_s"] = round(time.perf_counter() - t0, 3)
            out.write(json.dumps(row) + "\n")
            out.flush()
    return stats
