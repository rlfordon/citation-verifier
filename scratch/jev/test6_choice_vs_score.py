"""Test 6 — Choice vs Score question types, and where each belongs.

A `score` answer is a `choice` with ordered labels plus an expectation
computed for you:

    {"score": 2.77, "legend": {...}, "probabilities": {"0": .06, ..., "3": .90}}

The expectation is the only real difference, and it is the wrong summary for a
safety gate: it averages away *where* the probability mass sits, which is
exactly the partial-vs-full distinction the gate turns on.

Run (cache-only, free):
    venv/Scripts/python.exe scratch/jev/test6_choice_vs_score.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import jev_common
import loop_bank as LB
from jev_common import ask
from loop_data import load_dataset

CACHE_ONLY = "--live" not in sys.argv


def auc(pos, neg) -> float:
    pos, neg = np.asarray(pos, float), np.asarray(neg, float)
    if not len(pos) or not len(neg):
        return float("nan")
    d = pos[:, None] - neg[None, :]
    return float(((d > 0) + 0.5 * (d == 0)).mean())


def _search_rows():
    return [r for r in load_dataset("search") if r["eligible"] and not r["hedged"]]


def part_a() -> None:
    """Every signal in the bank, by type: discrimination AND margin."""
    data = _search_rows()
    rounds = LB.load_rounds()
    names, X = LB.signal_matrix(rounds, data)
    y = np.array([r["is_pos"] for r in data])
    partial = np.array([r["label"] == "partial" for r in data])
    types = {f"{rn[-2:]}.{q}": qq["type"]
             for rn, rd in rounds for q, qq in rd["questions"].items()}

    print(f"claims={len(data)} supported={y.sum()} partial={partial.sum()} "
          f"unsupported={(~y & ~partial).sum()}\n")
    print("AUCall/AUCpart: higher separates better.  topneg: the highest-scoring")
    print("NOT-supported claim -- the gate's threshold must clear it, so lower is")
    print("better.  clean: greens scoring above every negative.\n")
    print(f"{'signal':30s} {'type':7s} {'AUCall':>7s} {'AUCpart':>8s} "
          f"{'topneg':>7s} {'clean':>6s} {'ties@1.0':>9s}")
    rows = []
    for j, n in enumerate(names):
        s = X[:, j]
        rows.append((auc(s[y], s[partial]), n, types[n], auc(s[y], s[~y]),
                     s[~y].max(), int((s[y] > s[~y].max()).sum()),
                     int((s > 0.995).sum())))
    for vp, n, t, a, tn, cl, ties in sorted(rows, reverse=True):
        print(f"{n:30s} {t:7s} {a:7.3f} {vp:8.3f} {tn:7.3f} {cl:6d} {ties:9d}")


def part_b() -> None:
    """Score questions: the built-in expectation vs reading p(top level).

    `coverage` (choice) and `coverage_level` (score) are near-identical wording
    and the cleanest A/B in the bank.
    """
    data = _search_rows()
    y = np.array([r["is_pos"] for r in data])
    partial = np.array([r["label"] == "partial" for r in data])

    print("\n\nScore questions: which field to read\n")
    print(f"{'question':22s} {'readout':14s} {'AUCall':>7s} {'AUCpart':>8s} "
          f"{'topneg':>7s} {'clean':>6s}")
    for rname, rnd in LB.load_rounds():
        smap = rnd.get("state") or LB.DEFAULT_STATE
        qs = {k: {a: b for a, b in q.items() if a != "_green"}
              for k, q in rnd["questions"].items()}
        score_qs = [q for q, qq in rnd["questions"].items()
                    if qq["type"] == "score"]
        if not score_qs:
            continue
        answers = [ask({k: r[src] for k, src in smap.items()}, qs).answers
                   for r in data]
        for q in score_qs:
            top = max(answers[0][q]["legend"], key=int)
            readouts = {
                "score (default)": [a[q]["score"] / (len(a[q]["legend"]) - 1)
                                    for a in answers],
                "p(top level)": [a[q]["probabilities"][top] for a in answers],
            }
            for label, sig in readouts.items():
                s = np.array(sig)
                print(f"{rname[-2:] + '.' + q:22s} {label:14s} "
                      f"{auc(s[y], s[~y]):7.3f} {auc(s[y], s[partial]):8.3f} "
                      f"{s[~y].max():7.3f} {int((s[y] > s[~y].max()).sum()):6d}")


def main() -> None:
    if CACHE_ONLY:
        jev_common._get_client = lambda: (_ for _ in ()).throw(
            RuntimeError("cache miss -- rerun with --live to spend money"))
    part_a()
    part_b()
    print("\nReading: score and choice discriminate about equally (AUC), but every")
    print("score question's worst negative sits at >=0.91 using the default")
    print("expectation -- no room for a threshold. Reading p(top level) recovers")
    print("most of the margin. Choice questions with an explicit competitor")
    print("option ('main_part_only') beat both. See STAGE2.md.")


if __name__ == "__main__":
    main()
