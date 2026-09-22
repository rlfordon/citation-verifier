"""Test 7 -- stage 2: grading the claims the gate does NOT clear.

Stage 1 clears supported claims and escalates everything else. Stage 2 asks a
different question of the escalated pile: is this an OVERSTATEMENT (the case is
on point, the brief claimed more than it holds) or is the case NOT DOING THE
WORK (wrong subject, or the holding runs the other way)? That ordering drives a
review queue and never auto-clears anything, so it carries none of the gate's
safety risk -- which is why it is a reasonable place to use question types and
combination rules the gate cannot afford.

test6 found the two jobs want DIFFERENT questions: the "every part as written"
family gates well and grades badly; the topic / whose-view family does the
reverse. This tests that on 209 badge-labelled claims (vs 97), with 71 of them
in briefs no Jev tuning has ever touched.

    venv/Scripts/python.exe scratch/jev/test7_stage2.py          # cache only
    venv/Scripts/python.exe scratch/jev/test7_stage2.py --live   # ~$0.07
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import jev_common
from jev_common import HERE, ask, save_cache
from loop_bank import _green_value
from stage2_data import load_dataset

ROUND = HERE / "bank" / "stage2_round_00.json"


def auc(pos, neg) -> float:
    pos, neg = np.asarray(pos, float), np.asarray(neg, float)
    if not len(pos) or not len(neg):
        return float("nan")
    d = pos[:, None] - neg[None, :]
    return float(((d > 0) + 0.5 * (d == 0)).mean())


def boot_ci(pos, neg, n=2000, seed=0):
    """Percentile bootstrap on AUC -- these samples are small, say so."""
    rng = np.random.default_rng(seed)
    pos, neg = np.asarray(pos, float), np.asarray(neg, float)
    vals = [auc(rng.choice(pos, len(pos)), rng.choice(neg, len(neg)))
            for _ in range(n)]
    return float(np.percentile(vals, 5)), float(np.percentile(vals, 95))


def signals(rows):
    spec = json.loads(ROUND.read_text(encoding="utf-8"))
    qs = {k: {a: b for a, b in q.items() if a != "_green"}
          for k, q in spec["questions"].items()}
    names = list(spec["questions"])
    X = np.zeros((len(rows), len(names)))
    for i, r in enumerate(rows):
        a = ask({k: r[src] for k, src in spec["state"].items()}, qs).answers
        for j, q in enumerate(names):
            X[i, j] = _green_value(a[q], spec["questions"][q]["_green"])
    save_cache()
    return names, X


CARRIED = {"same_issue", "states_it", "whose_view", "support_level"}
SPLITS = ("search", "lock2", "colour")


def main() -> None:
    if "--live" not in sys.argv:
        jev_common._get_client = lambda: (_ for _ in ()).throw(
            RuntimeError("cache miss -- rerun with --live to spend money"))

    rows = load_dataset()
    names, X = signals(rows)
    split = np.array([r["split"] for r in rows])
    lab = np.array([r["label"] for r in rows])
    kind = np.array([r["kind"] for r in rows])

    for s in SPLITS:
        m = split == s
        print(f"{s}: {m.sum()} claims  {dict(Counter(lab[m]))}")
        print(f"   not-supported kinds: {dict(Counter(kind[m & (lab != 'supported')]))}")
    print()

    print("Task A = supported vs rest (what stage 1 does).")
    print("Task B = partial vs unsupported, among non-supported claims only")
    print("         (what stage 2 would do). 1.0 = perfect, 0.5 = coin flip.\n")
    print(f"{'signal':16s} {'kind':8s} {'A search':>9s} {'A lock2':>8s} "
          f"{'B search':>9s} {'B lock2':>8s} {'B colour':>9s} {'B lock2 90% CI':>17s}")
    table = []
    for j, n in enumerate(names):
        row = {}
        for s in SPLITS:
            m = split == s
            sup, rest = X[m & (lab == "supported"), j], X[m & (lab != "supported"), j]
            par = X[m & (lab == "partial"), j]
            uns = X[m & (lab == "unsupported"), j]
            row[f"A{s}"] = auc(sup, rest)
            row[f"B{s}"] = auc(par, uns)
            if s == "lock2":
                row["ci"] = boot_ci(par, uns)
        table.append((row["Block2"], n, row))
    for _, n, r in sorted(table, reverse=True):
        k = "carried" if n in CARRIED else "new"
        print(f"{n:16s} {k:8s} {r['Asearch']:9.3f} {r['Alock2']:8.3f} "
              f"{r['Bsearch']:9.3f} {r['Block2']:8.3f} {r['Bcolour']:9.3f} "
              f"{'[%.2f, %.2f]' % r['ci']:>17s}")

    # Can stage 2 recover the specific badge? wrong_subject vs overstated is
    # the distinction with an obvious operational meaning.
    print("\nwrong_subject vs overstated (the two biggest non-supported kinds):")
    print(f"{'signal':16s} {'search':>8s} {'lock2':>8s}")
    for j, n in enumerate(names):
        out = []
        for s in ("search", "lock2"):
            m = split == s
            out.append(auc(X[m & (kind == "overstated"), j],
                           X[m & (kind == "wrong_subject"), j]))
        print(f"{n:16s} {out[0]:8.3f} {out[1]:8.3f}")

    # Unweighted means of small hand-picked sets -- weights need more data
    # than we have (test6: fitted 30-signal model tied a 3-signal mean).
    idx = {n: j for j, n in enumerate(names)}
    combos = {
        "carried 4": ["same_issue", "states_it", "whose_view", "support_level"],
        "new 4": ["gap", "topic", "direction", "reach"],
        "topic+direction": ["topic", "direction"],
        "topic+direction+whose_view": ["topic", "direction", "whose_view"],
        "all 8": names,
    }
    print("\nUnweighted means (no fitting):")
    print(f"{'combo':30s} {'A search':>9s} {'A lock2':>8s} {'B search':>9s} "
          f"{'B lock2':>8s} {'B colour':>9s}")
    for name, cols in combos.items():
        j = [idx[c] for c in cols]
        s = X[:, j].mean(1)
        out = []
        for sp in SPLITS:
            m = split == sp
            out += [auc(s[m & (lab == "supported")], s[m & (lab != "supported")]),
                    auc(s[m & (lab == "partial")], s[m & (lab == "unsupported")])]
        print(f"{name:30s} {out[0]:9.3f} {out[2]:8.3f} {out[1]:9.3f} "
              f"{out[3]:8.3f} {out[5]:9.3f}")


if __name__ == "__main__":
    main()
