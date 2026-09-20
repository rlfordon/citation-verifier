"""Phrasing loop engine: a growing QUESTION BANK + a free combiner search.

Design (see LOOP.md): asking Jev new questions is the "expensive" tier (one
request per claim per round, ~3 cents); re-fitting the combination rule over
answers already collected is free. Each round a proposer adds probes to
bank/round_NN.json; this script asks them, scores every signal, greedily
builds a combiner, and accepts a new champion only on a pessimistic
(bootstrap lower-bound) win with no cross-brief false clears.

    venv/Scripts/python.exe scratch/jev/loop_bank.py            # run all rounds
    venv/Scripts/python.exe scratch/jev/loop_bank.py --diagnose # + failure cases
    venv/Scripts/python.exe scratch/jev/loop_bank.py --lock     # FINAL, once

Round file: {"state": {...optional field map...},
             "questions": {"<id>": {type, instructions, criteria,
                                    "_green": "yes"|"no"|"high"|"low"|[options]}}}
`_green` declares polarity up front (never fitted to data) and is stripped
before the request.
"""
from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from jev_common import HERE, ask, save_cache
from loop_data import load_dataset

BANK = HERE / "bank"
DEFAULT_STATE = {"proposition": "proposition",
                 "brief_sentence": "brief_sentence", "opinion": "excerpt"}
MARGIN = 0.03        # headroom required above the highest negative
MAX_SIGNALS = 4      # combiner size cap (few parameters for ~100 claims)
MIN_GAIN = 2.0       # claims of lower-bound improvement to take the crown
MAX_Q_CHARS = 900    # length cap per question (instructions + criteria)


# --------------------------------------------------------------------------
# Bank -> signal matrix
# --------------------------------------------------------------------------

def load_rounds() -> list[tuple[str, dict]]:
    return [(p.stem, json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(BANK.glob("round_*.json"))]


def leakage_check(rounds, data) -> list[str]:
    """Questions must state general principles: no case names, no text lifted
    from the claims the proposer was shown, no bloat."""
    problems = []
    props = [r["proposition"].lower() for r in data]
    for rname, rnd in rounds:
        for qid, q in rnd["questions"].items():
            text = json.dumps({k: v for k, v in q.items() if k != "_green"})
            if len(text) > MAX_Q_CHARS:
                problems.append(f"{rname}.{qid}: {len(text)} chars > {MAX_Q_CHARS}")
            if re.search(r"\b[A-Z][a-z]+ v\. [A-Z]", text):
                problems.append(f"{rname}.{qid}: contains a case name")
            words = re.findall(r"[a-z']+", text.lower())
            for i in range(len(words) - 6):
                if any(" ".join(words[i:i + 7]) in p for p in props):
                    problems.append(f"{rname}.{qid}: 7-word overlap with a claim")
                    break
    return problems


def _green_value(ans: dict, green) -> float:
    if ans["type"] == "noul":
        return ans["noul"] if green == "yes" else 1.0 - ans["noul"]
    if ans["type"] == "score":
        v = ans["score"] / (len(ans["legend"]) - 1)
        return v if green == "high" else 1.0 - v
    opts = [green] if isinstance(green, str) else green
    return sum(ans["probabilities"].get(o, 0.0) for o in opts)


def signal_matrix(rounds, data):
    """-> (names, X[n_claims, n_signals]); one request per claim per round."""
    names, cols = [], []
    for rname, rnd in rounds:
        smap = rnd.get("state") or DEFAULT_STATE
        qs = {k: {a: b for a, b in q.items() if a != "_green"}
              for k, q in rnd["questions"].items()}

        def one(row):
            return ask({k: row[src] for k, src in smap.items()}, qs).answers

        with ThreadPoolExecutor(max_workers=8) as pool:
            answers = list(pool.map(one, data))
        save_cache()
        for qid, q in rnd["questions"].items():
            names.append(f"{rname[-2:]}.{qid}")
            cols.append([_green_value(a[qid], q["_green"]) for a in answers])
    return names, np.array(cols).T


# --------------------------------------------------------------------------
# Objective
# --------------------------------------------------------------------------

def auc(pos, neg) -> float:
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    d = pos[:, None] - neg[None, :]
    return float(((d > 0) + 0.5 * (d == 0)).mean())


def cleared(s, y) -> int:
    """Supported claims scoring above every unsupported one, with headroom."""
    return int((s[y] > s[~y].max() + MARGIN).sum())


def clear_lb(s, y, groups, n=300, seed=0) -> float:
    """20th percentile of `cleared` under resampling within each brief: a
    number one lucky negative cannot inflate."""
    rng = np.random.default_rng(seed)
    idx_by_g = [np.flatnonzero(groups == g) for g in np.unique(groups)]
    out = []
    for _ in range(n):
        idx = np.concatenate([rng.choice(i, len(i)) for i in idx_by_g])
        ys = y[idx]
        if ys.all() or not ys.any():
            continue
        out.append(cleared(s[idx], ys))
    return float(np.percentile(out, 20))


def logo(s, y, groups):
    """Threshold from the OTHER briefs' highest negative; score this brief."""
    clr = bad = 0
    for g in np.unique(groups):
        tr = groups != g
        thr = (s[tr & ~y].max() if (tr & ~y).any() else 1.0) + MARGIN
        hit = (groups == g) & (s > thr)
        clr += int(hit.sum())
        bad += int((hit & ~y).sum())
    return clr, bad


def combine(X, cols, how):
    sub = X[:, cols]
    return sub.min(axis=1) if how == "min" else sub.mean(axis=1)


def greedy(X, y, groups, names):
    """Forward selection, separately for min and mean; keep the better."""
    best = None
    for how in ("min", "mean"):
        chosen, score = [], -1.0
        while len(chosen) < MAX_SIGNALS:
            cands = []
            for j in range(X.shape[1]):
                if j in chosen:
                    continue
                s = combine(X, chosen + [j], how)
                cands.append((clear_lb(s, y, groups), auc(s[y], s[~y]), j))
            lb, _, j = max(cands)
            if lb < score + (1.0 if chosen else 0.0):
                break
            chosen, score = chosen + [j], lb
        if best is None or score > best["lb"]:
            best = {"how": how, "cols": chosen, "lb": score,
                    "signals": [names[j] for j in chosen]}
    return best


def describe(spec, X, y, groups, partial) -> str:
    s = combine(X, spec["cols"], spec["how"])
    lc, lbad = logo(s, y, groups)
    return (f"{spec['how']}({', '.join(spec['signals'])})\n"
            f"      cleared={cleared(s, y)}/{int(y.sum())} supported  "
            f"lower-bound={clear_lb(s, y, groups):.0f}  "
            f"cross-brief: {lc} cleared / {lbad} bad  "
            f"auc={auc(s[y], s[~y]):.3f}  auc-vs-partial={auc(s[y], s[partial]):.3f}  "
            f"highest-negative={s[~y].max():.3f}")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> None:
    lock = "--lock" in sys.argv
    rounds = load_rounds()
    search = load_dataset("search")
    for p in leakage_check(rounds, search):
        print("LEAKAGE/BLOAT:", p)

    data = [r for r in search if r["eligible"] and not r["hedged"]]
    names, X = signal_matrix(rounds, data)
    y = np.array([r["is_pos"] for r in data])
    groups = np.array([r["group"] for r in data])
    partial = np.array([r["label"] == "partial" for r in data])
    print(f"search set: {len(data)} eligible, unhedged claims "
          f"({int(y.sum())} supported, {int(partial.sum())} partial, "
          f"{int((~y & ~partial).sum())} unsupported); {len(names)} signals "
          f"from {len(rounds)} rounds\n")

    print(f"{'signal':34s} {'auc':>6s} {'vsPart':>6s} {'payne':>6s} {'withers':>7s} {'spread':>6s}")
    table = []
    for j, n in enumerate(names):
        s = X[:, j]
        per = [auc(s[y & (groups == g)], s[~y & (groups == g)])
               for g in ("payne", "withers")]
        table.append((auc(s[y], s[partial]), n, auc(s[y], s[~y]), per, s.std()))
    for vp, n, a, per, sd in sorted(table, reverse=True):
        flat = "  FLAT" if sd < 0.08 else ""
        print(f"{n:34s} {a:6.3f} {vp:6.3f} {per[0]:6.3f} {per[1]:7.3f} {sd:6.2f}{flat}")

    # Champions: round-by-round, a challenger built from rounds <= k must beat
    # the reigning champion's lower bound by MIN_GAIN and add no bad clears.
    champ = None
    print()
    for k in range(len(rounds)):
        upto = [j for j, n in enumerate(names) if int(n[:2]) <= k]
        spec = greedy(X[:, upto], y, groups, [names[j] for j in upto])
        spec["cols"] = [upto[j] for j in spec["cols"]]
        s = combine(X, spec["cols"], spec["how"])
        _, bad = logo(s, y, groups)
        if champ is None:
            champ, verdict = spec, "initial champion"
        else:
            cs = combine(X, champ["cols"], champ["how"])
            _, cbad = logo(cs, y, groups)
            if spec["lb"] >= champ["lb"] + MIN_GAIN and bad <= cbad:
                champ, verdict = spec, "ACCEPTED as new champion"
            else:
                verdict = (f"rejected (needs lower-bound >= "
                           f"{champ['lb'] + MIN_GAIN:.0f} and <= {cbad} bad)")
        print(f"round {k:02d} challenger: {describe(spec, X, y, groups, partial)}\n"
              f"      -> {verdict}")
    (BANK / "champion.json").write_text(json.dumps(
        {k: champ[k] for k in ("how", "signals", "lb")}, indent=1), encoding="utf-8")

    if "--diagnose" in sys.argv:
        s = combine(X, champ["cols"], champ["how"])
        thr = s[~y].max() + MARGIN
        print(f"\n=== failures under the champion (threshold {thr:.3f}) ===")
        print("-- highest-scoring NOT-supported claims (what blocks a lower threshold):")
        for i in np.argsort(-np.where(~y, s, -1))[:7]:
            r = data[i]
            print(f"  [{r['label']} {s[i]:.2f} {r['group']}] {r['proposition'][:260]}\n"
                  f"      WHY: {r['why'][:420]}")
        print("-- supported claims just below the threshold (cheapest coverage to win):")
        below = [i for i in np.argsort(-s) if y[i] and s[i] <= thr][:7]
        for i in below:
            r = data[i]
            sig = " ".join(f"{n.split('.')[1][:12]}={X[i, j]:.2f}"
                           for n, j in zip(champ["signals"], champ["cols"]))
            print(f"  [{s[i]:.2f} {r['group']}] {r['proposition'][:200]}\n      {sig}")

    if lock:
        ld = [r for r in load_dataset("lock") if r["eligible"]]
        _, XL = signal_matrix(rounds, ld)
        yl = np.array([r["is_pos"] for r in ld])
        print(f"\n=== LOCKBOX ({len(ld)} eligible claims, {int((~yl).sum())} "
              f"not supported) -- thresholds frozen from the search set ===")
        base = {"how": "min", "cols": [names.index(n) for n in
                ("00.relation", "00.states_it", "00.same_issue")]}
        for label, spec in (("baseline", base), ("champion", champ)):
            thr = combine(X, spec["cols"], spec["how"])[~y].max() + MARGIN
            sl = combine(XL, spec["cols"], spec["how"])
            hit = sl > thr
            n_neg = int((~yl).sum())
            print(f"  {label:9s} thr={thr:.3f}: cleared {int(hit.sum())}/{len(ld)} "
                  f"({int((hit & yl).sum())} right, {int((hit & ~yl).sum())} BAD) "
                  f"auc={auc(sl[yl], sl[~yl]):.3f}  "
                  f"[0 bad of {n_neg} negatives would still mean a true rate "
                  f"up to ~{300 / n_neg:.0f}%]")


if __name__ == "__main__":
    main()
