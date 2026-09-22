"""Test 7 -- stage 2: grading the claims the gate does NOT clear.

Stage 1 clears supported claims and escalates everything else. Stage 2 asks a
different question of the escalated pile: is this an OVERSTATEMENT (the case is
on point, the brief claimed more than it holds) or is the case NOT DOING THE
WORK (wrong subject, or the holding runs the other way)? That ordering drives a
review queue and never auto-clears anything, so it carries none of the gate's
safety risk -- which is why it can afford question types and combination rules
the gate cannot.

test6 found the two jobs want DIFFERENT questions: the "every part as written"
family gates well and grades badly; the topic / whose-view family does the
reverse. This tests that across four cells -- badge vs colour labels, crossed
with briefs tuning has seen vs briefs it has not.

    venv/Scripts/python.exe scratch/jev/test7_stage2.py          # cache only
    venv/Scripts/python.exe scratch/jev/test7_stage2.py --live   # ~$0.10
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
CARRIED = {"same_issue", "states_it", "whose_view", "support_level"}
# (label_source, split) -- the primary result is badge/held-out; colour/held-out is
# a bigger but coarser held-out check.
CELLS = [("detailed", "tuning"), ("detailed", "held_out"),
         ("coarse", "tuning"), ("coarse", "held_out")]


def auc(pos, neg) -> float:
    pos, neg = np.asarray(pos, float), np.asarray(neg, float)
    if not len(pos) or not len(neg):
        return float("nan")
    d = pos[:, None] - neg[None, :]
    return float(((d > 0) + 0.5 * (d == 0)).mean())


def boot_ci(pos, neg, n=2000, seed=0):
    """Percentile bootstrap on AUC -- these samples are small, so say so."""
    if len(pos) < 2 or len(neg) < 2:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    pos, neg = np.asarray(pos, float), np.asarray(neg, float)
    vals = [auc(rng.choice(pos, len(pos)), rng.choice(neg, len(neg)))
            for _ in range(n)]
    return float(np.percentile(vals, 5)), float(np.percentile(vals, 95))


def within_brief(X, j, grp, lab, mask):
    """Weighted mean of per-brief task-B AUCs.

    The review queue is ordered INSIDE one brief, so this is the operationally
    relevant number. Pooling across briefs mixes score scales and reads lower
    than every brief in the pool -- an artefact, not a finding.
    """
    num = den = 0.0
    for g in set(grp[mask]):
        m = mask & (grp == g)
        p, u = X[m & (lab == "partial"), j], X[m & (lab == "unsupported"), j]
        if len(p) < 2 or len(u) < 2:
            continue
        w = len(p) * len(u)
        num += w * auc(p, u)
        den += w
    return num / den if den else float("nan")


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


def main() -> None:
    if "--live" not in sys.argv:
        jev_common._get_client = lambda: (_ for _ in ()).throw(
            RuntimeError("cache miss -- rerun with --live to spend money"))

    rows = load_dataset(drop_quote_suspect="--strict" in sys.argv)
    names, X = signals(rows)
    cell = np.array([f"{r['label_source']}/{r['split']}" for r in rows])
    lab = np.array([r["label"] for r in rows])
    kind = np.array([r["kind"] for r in rows])
    keys = [f"{s}/{sp}" for s, sp in CELLS]

    for k in keys:
        m = cell == k
        print(f"{k:14s} {m.sum():3d} claims  {dict(Counter(lab[m]))}")
    print()
    print("Job 1 (clearing): supported claims vs everything else.")
    print("Job 2 (grading): among claims that are NOT supported, is it an")
    print("         overstatement (partial) or a case that does not do the work")
    print("         at all (unsupported)?  1.0 = perfect, 0.5 = coin flip.")
    print("Detailed labels are cleaner but the held-out group is small;")
    print("colour/held-out is 3x bigger with coarser labels.\n")

    head = f"{'signal':16s} {'kind':8s}" + "".join(f"{'clear ' + k:>16s}" for k in keys)
    print(head)
    A = {}
    for j, n in enumerate(names):
        vals = []
        for k in keys:
            m = cell == k
            vals.append(auc(X[m & (lab == "supported"), j],
                            X[m & (lab != "supported"), j]))
        A[n] = vals
        kk = "reused" if n in CARRIED else "new"
        print(f"{n:16s} {kk:8s}" + "".join(f"{v:16.3f}" for v in vals))

    print()
    head = (f"{'signal':16s} {'kind':8s}" + "".join(f"{'grade ' + k:>16s}" for k in keys)
            + f"{'coarse held-out CI':>20s}")
    print(head)
    table = []
    for j, n in enumerate(names):
        vals, ci = [], (float("nan"), float("nan"))
        for k in keys:
            m = cell == k
            par, uns = X[m & (lab == "partial"), j], X[m & (lab == "unsupported"), j]
            vals.append(auc(par, uns))
            if k == "coarse/held_out":
                ci = boot_ci(par, uns)
        table.append((vals[-1], n, vals, ci))
    for _, n, vals, ci in sorted(table, reverse=True):
        kk = "reused" if n in CARRIED else "new"
        print(f"{n:16s} {kk:8s}" + "".join(f"{v:16.3f}" for v in vals)
              + f"{'[%.2f, %.2f]' % ci:>20s}")

    print("\nwrong_subject vs overstated (detailed labels only -- coarse rows have "
          "no kind detail):")
    for j, n in enumerate(names):
        out = []
        for k in ("detailed/tuning", "detailed/held_out"):
            m = cell == k
            out.append(auc(X[m & (kind == "overstated"), j],
                           X[m & (kind == "wrong_subject"), j]))
        print(f"  {n:16s} {out[0]:8.3f} {out[1]:8.3f}")

    print("\nTask B measured WITHIN each brief (weighted mean of per-brief")
    print("AUCs) -- the queue is ordered inside one brief. Pooling across")
    print("briefs mixes score scales and reads low; see STAGE2.md.")
    src = np.array([r["label_source"] for r in rows])
    grp = np.array([r["group"] for r in rows])
    allm = np.ones(len(rows), bool)
    print(f"\n{'signal':16s} {'pooled':>9s} {'within all':>11s} "
          f"{'within detailed':>16s} {'within coarse':>14s}")
    wb_rows = []
    for j, n in enumerate(names):
        p, u = X[lab == "partial", j], X[lab == "unsupported", j]
        wb_rows.append((within_brief(X, j, grp, lab, allm), n, auc(p, u),
                        within_brief(X, j, grp, lab, src == "detailed"),
                        within_brief(X, j, grp, lab, src == "coarse")))
    for w, n, pooled, wbb, wbc in sorted(wb_rows, reverse=True):
        print(f"{n:16s} {pooled:9.3f} {w:11.3f} {wbb:16.3f} {wbc:14.3f}")

    idx = {n: j for j, n in enumerate(names)}
    combos = {
        "carried 4": ["same_issue", "states_it", "whose_view", "support_level"],
        "new 4": ["gap", "topic", "direction", "reach"],
        "whose_view + same_issue": ["whose_view", "same_issue"],
        "all 8": names,
    }
    print("\nUnweighted means (no fitting), task B:")
    print(f"{'combo':26s}" + "".join(f"{k:>16s}" for k in keys))
    for name, cols in combos.items():
        s = X[:, [idx[c] for c in cols]].mean(1)
        vals = []
        for k in keys:
            m = cell == k
            vals.append(auc(s[m & (lab == "partial")], s[m & (lab == "unsupported")]))
        print(f"{name:26s}" + "".join(f"{v:16.3f}" for v in vals))


if __name__ == "__main__":
    main()
