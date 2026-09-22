"""Test 8 -- the two-sided checker: clear the obvious greens, flag the obvious
reds, send only the middle to the LLM.

One Jev request per claim carries both sides (`bank/checker_v0.json`). Two
fixed thresholds cut the score line into three buckets:

    score_clear > clear_thr   -> CLEAR   (no LLM call)
    score_flag  < flag_thr    -> FLAG    (no LLM call, reported as a problem)
    everything else           -> the LLM

The two errors are not symmetric and are not traded off against each other:

    a BAD CLEAR waves a bad citation through           -- the gate's error
    a FALSE ACCUSATION calls a good citation bad       -- the flag's error

**Protocol.** Both thresholds are chosen on `search` by a rule declared here,
before looking at `lock2`, and then applied unchanged. LOOP.md's lesson was
that a threshold sitting flush against the worst training example does not
survive contact with a new brief, so each side carries an explicit margin.

    venv/Scripts/python.exe scratch/jev/test8_checker.py          # cache only
    venv/Scripts/python.exe scratch/jev/test8_checker.py --live   # ~$0.04
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import jev_common
from jev_common import HERE, ask, save_cache
from stage2_data import load_dataset

ROUND = HERE / "bank" / "checker_v0.json"

# Declared before looking at lock2.
CLEAR_MARGIN = 0.10   # LOOP.md: a flush threshold does not survive a new brief
FLAG_MARGIN = 0.05


def _green_value(ans: dict, green) -> float:
    if ans["type"] == "noul":
        return ans["noul"] if green == "yes" else 1.0 - ans["noul"]
    if ans["type"] == "score":
        lo, hi = min(ans["legend"], key=int), max(ans["legend"], key=int)
        return ans["probabilities"][hi if green == "high" else lo]
    opts = [green] if isinstance(green, str) else green
    return sum(ans["probabilities"].get(o, 0.0) for o in opts)


def signals(rows):
    spec = json.loads(ROUND.read_text(encoding="utf-8"))
    qs = {k: {a: b for a, b in q.items() if not a.startswith("_")}
          for k, q in spec["questions"].items()}
    names = list(spec["questions"])
    X = np.zeros((len(rows), len(names)))
    for i, r in enumerate(rows):
        a = ask({k: r[src] for k, src in spec["state"].items()}, qs).answers
        for j, q in enumerate(names):
            X[i, j] = _green_value(a[q], spec["questions"][q]["_green"])
    save_cache()
    sides = {q: spec["questions"][q]["_side"] for q in names}
    return names, X, sides


def report(tag, clear, flag, lab, n_total, cell):
    mid = cell & ~clear & ~flag
    bad_clear = int(((lab != "supported") & clear).sum())
    false_acc = int(((lab == "supported") & flag).sum())
    print(f"  {tag}")
    print(f"    CLEAR {clear.sum():3d} ({clear.sum()/n_total:3.0%})  "
          f"bad clears: {bad_clear}"
          f"{'  <-- ' + str(bad_clear) + ' bad citations waved through' if bad_clear else '  (clean)'}")
    print(f"    FLAG  {flag.sum():3d} ({flag.sum()/n_total:3.0%})  "
          f"false accusations: {false_acc}"
          f"   precision {(((lab != 'supported') & flag).sum() / flag.sum()) if flag.sum() else float('nan'):.0%}")
    print(f"    LLM   {mid.sum():3d} ({mid.sum()/n_total:3.0%})  "
          f"-> LLM work avoided on {1 - mid.sum()/n_total:.0%} of claims")
    unsup = (lab == "unsupported") & cell
    print(f"    recall of unsupported claims by the flag: "
          f"{((unsup & flag).sum() / unsup.sum()) if unsup.sum() else float('nan'):.0%}")


def main() -> None:
    if "--live" not in sys.argv:
        jev_common._get_client = lambda: (_ for _ in ()).throw(
            RuntimeError("cache miss -- rerun with --live to spend money"))

    rows = load_dataset()
    names, X, sides = signals(rows)
    lab = np.array([r["label"] for r in rows])
    split = np.array([r["split"] for r in rows])
    src = np.array([r["label_source"] for r in rows])

    clear_sig = X[:, names.index("as_written")]
    flag_sig = np.minimum(X[:, names.index("whose_view")],
                          X[:, names.index("same_issue")])

    s = split == "search"
    # CLEAR: above every not-supported search claim, plus a margin.
    worst_neg = clear_sig[s & (lab != "supported")].max()
    clear_thr = min(0.995, worst_neg + CLEAR_MARGIN)
    # FLAG: below every supported search claim, minus a margin.
    best_pos = flag_sig[s & (lab == "supported")].min()
    flag_thr = max(0.0, best_pos - FLAG_MARGIN)

    print(f"Thresholds chosen on `search` ({s.sum()} claims), then frozen:")
    print(f"  clear if as_written          > {clear_thr:.3f}  "
          f"(worst search negative {worst_neg:.3f} + {CLEAR_MARGIN} margin)")
    print(f"  flag  if min(whose_view,same_issue) < {flag_thr:.3f}  "
          f"(lowest search supported {best_pos:.3f} - {FLAG_MARGIN} margin)\n")

    for tag, m in (("SEARCH (thresholds fitted here)", s),
                   ("LOCK2  (never seen -- the real test)", split == "lock2"),
                   ("  of which badge-labelled", (split == "lock2") & (src == "badge")),
                   ("  of which colour-labelled", (split == "lock2") & (src == "colour")),
                   ("ALL 13 BRIEFS", np.ones(len(rows), bool))):
        if not m.sum():
            continue
        report(tag, (clear_sig > clear_thr) & m, (flag_sig < flag_thr) & m,
               lab, int(m.sum()), m)
        print()

    # sanity: what the two sides would do on their own
    print("For comparison, one-sided operation on all claims:")
    only_clear = clear_sig > clear_thr
    only_flag = flag_sig < flag_thr
    print(f"  clear-only: {only_clear.sum()} cleared, "
          f"{int(((lab != 'supported') & only_clear).sum())} bad")
    print(f"  flag-only:  {only_flag.sum()} flagged, "
          f"{int(((lab == 'supported') & only_flag).sum())} good accused")
    print(f"  overlap (both fire): {int((only_clear & only_flag).sum())}")


if __name__ == "__main__":
    main()
