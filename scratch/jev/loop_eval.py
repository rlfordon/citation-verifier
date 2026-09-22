"""Score one candidate rubric against the loop dataset.

A candidate is JSON:
  {"name": "...",
   "state": {"proposition": "proposition", "opinion": "excerpt", ...},   # optional
   "questions": {"<id>": {"type": "noul|choice|score", "instructions": ..., "criteria": ...}},
   "signals": [{"q": "<id>", "field": "noul|score|confidence|p:<option>", "invert": false}],
   "combine": "min" | "mean" | "product"}

Gate score per claim = combine(signals), each signal in [0, 1] where 1 = Green.

    venv/Scripts/python.exe scratch/jev/loop_eval.py candidates/baseline.json
"""
from __future__ import annotations

import json
import math
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from jev_common import ask, save_cache
from loop_data import load_dataset

DEFAULT_STATE = {"proposition": "proposition",
                 "brief_sentence": "brief_sentence", "opinion": "excerpt"}


def _score_top(a: dict) -> float:
    """A score answer's probability of its TOP level, not its expectation.

    `a["score"]` is the mean over the ordered legend. Averaging hides where
    the mass sits: "90% sure every part is there" and "split 50/50 between
    most-of-it and all-of-it" land on nearly the same number, and that is
    precisely the partial-vs-full distinction. p(top level) keeps it, and beat
    the expectation on every score question in the bank (test6).
    """
    return a["probabilities"][max(a["legend"], key=int)]


def _signal(answers: dict, sig: dict) -> float:
    a, field = answers[sig["q"]], sig["field"]
    if field.startswith("p:"):
        v = a["probabilities"].get(field[2:], 0.0)
    elif field == "score":
        v = _score_top(a)
    else:
        v = a[field]
    return 1.0 - v if sig.get("invert") else v


def _combine(vals: list[float], how: str) -> float:
    if how == "mean":
        return sum(vals) / len(vals)
    if how == "product":
        return math.prod(vals)
    return min(vals)


def score_claims(cand: dict, data: list[dict]) -> list[dict]:
    smap = cand.get("state") or DEFAULT_STATE

    def one(row):
        state = {k: row[src] for k, src in smap.items()}
        r = ask(state, cand["questions"])
        sigs = [_signal(r.answers, s) for s in cand["signals"]]
        return {**{k: row[k] for k in ("id", "group", "label", "is_pos", "eligible")},
                "score": _combine(sigs, cand.get("combine", "min")),
                "signals": sigs, "tokens": r.input_tokens,
                "latency_s": r.latency_s}

    with ThreadPoolExecutor(max_workers=8) as pool:
        out = list(pool.map(one, data))
    save_cache()
    return out


def auc(pos: list[float], neg: list[float]) -> float:
    if not pos or not neg:
        return float("nan")
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def cleared_at_zero_bad(rows: list[dict], margin: float = 0.0):
    """Positives scoring above every negative (+margin). In-sample ceiling."""
    neg = [r["score"] for r in rows if not r["is_pos"]]
    thr = (max(neg) if neg else 0.0) + margin
    return sum(r["is_pos"] and r["score"] > thr for r in rows), thr


def logo(rows: list[dict], margin: float = 0.02):
    """Leave-one-brief-out: threshold = highest negative in the OTHER briefs
    (+margin); count clears and false clears in the held-out brief."""
    cleared = bad = 0
    for g in sorted({r["group"] for r in rows}):
        train = [r for r in rows if r["group"] != g]
        neg = [r["score"] for r in train if not r["is_pos"]]
        thr = (max(neg) if neg else 1.0) + margin
        for r in rows:
            if r["group"] == g and r["score"] > thr:
                cleared += 1
                bad += not r["is_pos"]
    return cleared, bad


def bootstrap_clear_lb(rows: list[dict], n: int = 400, seed: int = 0) -> float:
    """5th-percentile of in-sample zero-bad clears under resampling: a
    pessimistic coverage number that a single lucky negative can't inflate."""
    rng = random.Random(seed)
    vals = []
    for _ in range(n):
        sample = [rng.choice(rows) for _ in rows]
        vals.append(cleared_at_zero_bad(sample)[0] / max(1, len(sample)))
    return sorted(vals)[int(0.05 * n)] * len(rows)


def metrics(scored: list[dict]) -> dict:
    out = {}
    for name, rows in (("all", scored),
                       ("elig", [r for r in scored if r["eligible"]])):
        pos = [r["score"] for r in rows if r["is_pos"]]
        neg = [r["score"] for r in rows if not r["is_pos"]]
        part = [r["score"] for r in rows if r["label"] == "partial"]
        clr, thr = cleared_at_zero_bad(rows)
        lc, lb = logo(rows)
        out[name] = {
            "n": len(rows), "pos": len(pos), "neg": len(neg),
            "auc": round(auc(pos, neg), 4),
            "auc_vs_partial": round(auc(pos, part), 4),
            "clear0": clr, "thr0": round(thr, 3),
            "clear0_lb": round(bootstrap_clear_lb(rows), 1),
            "logo_clear": lc, "logo_bad": lb,
            "max_neg": round(max(neg), 3) if neg else None,
        }
    out["tokens_med"] = sorted(r["tokens"] for r in scored)[len(scored) // 2]
    return out


def evaluate(cand: dict, data: list[dict] | None = None) -> tuple[dict, list[dict]]:
    scored = score_claims(cand, data if data is not None else load_dataset())
    return metrics(scored), scored


def fmt(name: str, m: dict) -> str:
    a, e = m["all"], m["elig"]
    return (f"{name:34s} ALL auc={a['auc']:.3f} vsPartial={a['auc_vs_partial']:.3f} "
            f"clear0={a['clear0']:3d} (lb {a['clear0_lb']:.0f}) "
            f"logo={a['logo_clear']:3d}/{a['logo_bad']}bad | "
            f"ELIG auc={e['auc']:.3f} clear0={e['clear0']:3d} "
            f"logo={e['logo_clear']:3d}/{e['logo_bad']}bad | tok={m['tokens_med']}")


if __name__ == "__main__":
    for path in sys.argv[1:]:
        cand = json.loads(Path(path).read_text(encoding="utf-8"))
        m, _ = evaluate(cand)
        print(fmt(cand.get("name", Path(path).stem), m))
