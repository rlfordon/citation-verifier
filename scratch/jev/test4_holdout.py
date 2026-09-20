"""Test 4 -- held-out check of the gate on a real prior run.

    venv/Scripts/python.exe scratch/jev/test4_holdout.py matters/payne

Thresholds are FIXED in advance from the frozen corpora (Test 2): 0.90 (strict)
and 0.50 (loose), rule = min(p_supports, states_it, same_issue) on located
passages. Reference = what the pipeline actually reported (the recorded Opus
`support` column), so a "bad clear" here means "the gate would have turned a
reported Yellow/Red into a Green".
"""
from __future__ import annotations

import csv
import statistics
import sys
from pathlib import Path

from jev_common import REPO, RESULTS, Claim, ask, pct, save_cache
from test1_rubric import RUBRIC, build_state, flatten
from test3_locator import locate

THRESHOLDS = (0.90, 0.50)


def score(f: dict) -> float:
    return min(f["p_supports"], f["states_it"], f["same_issue"])


def main() -> None:
    workdir = (REPO / sys.argv[1]).resolve()
    rows = list(csv.DictReader((workdir / "claims.csv").open(encoding="utf-8")))
    out, lat, cost = [], [], 0.0
    for n, row in enumerate(rows, 1):
        f = row.get("opinion_file", "")
        path = workdir / f if f and (workdir / f).is_file() else None
        support = (row.get("support") or "").strip()
        if path is None or not support:
            continue
        claim = Claim(workdir.name, row["claim_id"], row,
                      "green" if support == "supported" else "not", path)
        flags = (row.get("crosscheck_flags") or "").strip() not in ("", "[]")
        eligible = claim.gate_eligible and not flags
        raw = path.read_text(encoding="utf-8", errors="ignore")
        loc = locate(claim, raw)[-1]
        rub = ask(build_state(claim, focused=True), RUBRIC)
        flat = flatten(rub.answers)
        lat.append(loc.latency_s + rub.latency_s)
        cost += loc.cost_usd + rub.cost_usd
        out.append({"claim_id": row["claim_id"], "opus_support": support,
                    "opus_color": row.get("assessment", ""),
                    "eligible": int(eligible), "score": round(score(flat), 4),
                    "jev_s": round(lat[-1], 3), **flat})
        if n % 20 == 0:
            save_cache()
    save_cache()

    RESULTS.mkdir(exist_ok=True)
    dest = RESULTS / f"holdout_{workdir.name}.csv"
    with dest.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)

    elig = [r for r in out if r["eligible"]]
    print(f"\n{workdir.name}: {len(rows)} claims in the brief, {len(out)} assessed "
          f"by Opus with an opinion, {len(elig)} gate-eligible")
    print(f"  Jev time (locate + rubric): median {statistics.median(lat):.2f}s/claim, "
          f"{sum(lat):.0f}s total sequential; cost ${cost:.4f}")
    for thr in THRESHOLDS:
        hit = [r for r in elig if r["score"] >= thr]
        bad = [r for r in hit if r["opus_support"] != "supported"]
        print(f"  threshold {thr:.2f}: cleared {pct(len(hit), len(rows))} of the "
              f"brief | {pct(len(hit), len(out))} of Opus's claims | "
              f"bad clears: {len(bad)}")
        for r in bad:
            print(f"      BAD {r['claim_id']} opus={r['opus_support']}/"
                  f"{r['opus_color']} score={r['score']}")
    # jobs are packed per opinion: a job is only skipped if ALL its claims clear
    by_op: dict[str, list] = {}
    idx = {r["claim_id"]: r for r in out}
    for row in rows:
        if row["claim_id"] in idx:
            by_op.setdefault(row["opinion_file"], []).append(idx[row["claim_id"]])
    for thr in THRESHOLDS:
        skipped = sum(all(r["eligible"] and r["score"] >= thr for r in rs)
                      for rs in by_op.values())
        print(f"  threshold {thr:.2f}: per-opinion Opus jobs skipped entirely: "
              f"{pct(skipped, len(by_op))}")


if __name__ == "__main__":
    main()
