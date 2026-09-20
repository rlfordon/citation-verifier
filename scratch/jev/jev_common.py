"""Shared harness for the Jev experiments (see README.md).

Cached, timed calls to TypeSafe's System One API + frozen-corpus loading.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
CORPORA_ROOT = REPO / "tests" / "data" / "assessment_corpora"
CORPORA = ("withers", "payne", "wainwright")
CACHE_PATH = HERE / "cache.json"
RESULTS = HERE / "results"

MODEL = "jev-1.13.0"            # pinned: aliases move, thresholds don't
USD_PER_TOKEN = 0.042 / 1_000_000  # input only; output is free


# --------------------------------------------------------------------------
# Cached API calls
# --------------------------------------------------------------------------

_cache: dict | None = None
_client = None


def _load_cache() -> dict:
    global _cache
    if _cache is None:
        _cache = (json.loads(CACHE_PATH.read_text(encoding="utf-8"))
                  if CACHE_PATH.exists() else {})
    return _cache


def save_cache() -> None:
    if _cache is not None:
        CACHE_PATH.write_text(json.dumps(_cache, indent=0, sort_keys=True),
                              encoding="utf-8")


def _get_client():
    global _client
    if _client is None:
        from dotenv import load_dotenv
        from typesafe_sdk import TypeSafeClient
        load_dotenv(REPO / ".env")
        _client = TypeSafeClient(model=MODEL, timeout=120.0)
    return _client


@dataclass
class JevResult:
    answers: dict        # raw answers JSON, keyed by question id
    input_tokens: int
    latency_s: float     # wall-clock of the LIVE call (kept in the cache)
    cached: bool

    @property
    def cost_usd(self) -> float:
        return self.input_tokens * USD_PER_TOKEN


def ask(state, questions: dict) -> JevResult:
    """One System One request. `questions` are raw dicts (hashable, and the
    SDK accepts them as-is). Live latency is stored so replays keep timings."""
    key = hashlib.sha256(json.dumps(
        [MODEL, state, questions], sort_keys=True).encode("utf-8")).hexdigest()
    cache = _load_cache()
    if key in cache:
        e = cache[key]
        return JevResult(e["answers"], e["input_tokens"], e["latency_s"], True)
    client = _get_client()
    t0 = time.perf_counter()
    resp = client.system_one(state, questions)
    latency = time.perf_counter() - t0
    raw = resp.raw_http_response.json()
    entry = {"answers": raw["answers"],
             "input_tokens": raw["usage"]["input_tokens"],
             "latency_s": round(latency, 3), "model": raw.get("model", MODEL)}
    cache[key] = entry
    return JevResult(entry["answers"], entry["input_tokens"], latency, False)


# --------------------------------------------------------------------------
# Corpus loading
# --------------------------------------------------------------------------

def clean_text(raw: str) -> str:
    """Same cleaning check_quotes uses: strip tags/entities, collapse space."""
    s = re.sub(r"<[^>]+>", " ", raw)
    s = re.sub(r"&\w+;", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def split_passages(raw: str, max_passages: int = 250,
                   min_chars: int = 250, max_chars: int = 1200) -> list[str]:
    """Opinion -> passages. Splits on block tags / blank lines, merges
    fragments, splits long blocks at sentence ends, then merges neighbours
    until the count fits one Choice question (255-option cap)."""
    blocks = re.split(r"</p>|<p[ >]|</blockquote>|<blockquote|\n\s*\n", raw)
    parts = [clean_text(b) for b in blocks]
    parts = [p for p in parts if p]
    out: list[str] = []
    for p in parts:
        while len(p) > max_chars:
            cut = p.rfind(". ", 0, max_chars)
            cut = cut + 1 if cut > max_chars // 2 else max_chars
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        if out and len(out[-1]) < min_chars:
            out[-1] = out[-1] + " " + p
        else:
            out.append(p)
    while len(out) > max_passages:  # merge the shortest adjacent pair
        i = min(range(len(out) - 1), key=lambda k: len(out[k]) + len(out[k + 1]))
        out[i:i + 2] = [out[i] + " " + out[i + 1]]
    return out


@dataclass
class Claim:
    corpus: str
    claim_id: str
    row: dict
    expected: str          # 'green' | 'yellow' | 'red' | '' (lowercased)
    opinion_path: Path | None

    @property
    def is_green(self) -> bool:
        return self.expected == "green"

    @property
    def gate_eligible(self) -> bool:
        return (self.row.get("cl_status") == "VERIFIED"
                and self.opinion_path is not None
                and self.row.get("quote_check_worst", "") in
                ("VERBATIM", "NO_QUOTES", ""))


def load_claims() -> list[Claim]:
    claims = []
    for corpus in CORPORA:
        d = CORPORA_ROOT / corpus
        gt = {r["claim_id"]: r for r in csv.DictReader(
            (d / "ground_truth.csv").open(encoding="utf-8"))}
        for row in csv.DictReader((d / "claims.csv").open(encoding="utf-8")):
            f = row.get("opinion_file", "")
            path = d / f if f and (d / f).is_file() else None
            exp = (gt.get(row["claim_id"], {}).get("expected") or "").lower()
            claims.append(Claim(corpus, row["claim_id"], row, exp, path))
    return claims


def load_verdicts(corpus: str, prompt_version: str = "assess-v2") -> dict:
    """claim_id -> recorded Opus verdict envelope (last write wins)."""
    out = {}
    p = CORPORA_ROOT / corpus / "jobs" / "assess_results.jsonl"
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            v = json.loads(line)
            if v.get("prompt_version") == prompt_version:
                out[v["claim_id"]] = v
    return out


def pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "0/0"
