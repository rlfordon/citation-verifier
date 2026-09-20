"""Test 2 -- auto-Green gate. Pure analysis over Test 1's CSV (no API calls).

    venv/Scripts/python.exe scratch/jev/test2_gate.py [rubric_full|rubric_focused]

A "bad clear" = a human-yellow/red claim the gate would wave through as Green.
"""
from __future__ import annotations

import csv
import sys

from jev_common import CORPORA, RESULTS, pct

F = float

# Each rule maps a row to a "confidence it is Green" score in [0, 1].
RULES = {
    "p_supports": lambda r: F(r["p_supports"]),
    "states_it": lambda r: F(r["states_it"]),
    "support_score/3": lambda r: F(r["support_score"]) / 3,
    "min(supports,states,issue)": lambda r: min(
        F(r["p_supports"]), F(r["states_it"]), F(r["same_issue"])),
    "min(+court,-opposite)": lambda r: min(
        F(r["p_supports"]), F(r["states_it"]), F(r["same_issue"]),
        F(r["p_court"]), 1 - F(r["opposite"])),
    "min(all,-overstated)": lambda r: min(
        F(r["p_supports"]), F(r["states_it"]), F(r["same_issue"]),
        F(r["p_court"]), 1 - F(r["opposite"]), 1 - F(r["overstated"])),
    "min(all,score/3)": lambda r: min(
        F(r["p_supports"]), F(r["states_it"]), F(r["same_issue"]),
        F(r["p_court"]), 1 - F(r["opposite"]), F(r["support_score"]) / 3),
}


def zero_bad_threshold(rows, rule) -> float:
    """Smallest threshold that clears no yellow/red claim in `rows`."""
    bad = [rule(r) for r in rows if r["expected"] != "green"]
    return max(bad) + 1e-9 if bad else 0.0


def clears(rows, rule, thr):
    hit = [r for r in rows if rule(r) >= thr]
    return hit, [r for r in hit if r["expected"] != "green"]


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "rubric_full"
    rows = list(csv.DictReader((RESULTS / f"{name}.csv").open(encoding="utf-8")))
    for label, pool in (("ELIGIBLE claims only (clean VERIFIED, no quote "
                         "problem)", [r for r in rows if r["eligible"] == "1"]),
                        ("ALL claims with an opinion", rows)):
        n_green = sum(r["expected"] == "green" for r in pool)
        print(f"\n=== {name} | {label}: {len(pool)} claims "
              f"({n_green} green, {len(pool) - n_green} yellow/red)")
        print(f"{'rule':30s} {'thr@0bad':>8s} {'cleared':>13s} | "
              f"{'@0.90':>12s} {'@0.80':>12s}   leave-one-corpus-out")
        for rname, rule in RULES.items():
            thr = zero_bad_threshold(pool, rule)
            hit, _ = clears(pool, rule, thr)
            cells = []
            for t in (0.90, 0.80):
                h, b = clears(pool, rule, t)
                cells.append(f"{len(h):3d} ({len(b)} bad)")
            loco_clear = loco_bad = 0
            for held in CORPORA:
                train = [r for r in pool if r["corpus"] != held]
                test = [r for r in pool if r["corpus"] == held]
                h, b = clears(test, rule, zero_bad_threshold(train, rule))
                loco_clear += len(h)
                loco_bad += len(b)
            print(f"{rname:30s} {thr:8.3f} {pct(len(hit), len(pool)):>13s} | "
                  f"{cells[0]:>12s} {cells[1]:>12s}   "
                  f"{pct(loco_clear, len(pool))}, {loco_bad} bad")

        # Which yellow/red claims look most Green to Jev? (the gate's enemies)
        rule = RULES["min(+court,-opposite)"]
        worst = sorted((r for r in pool if r["expected"] != "green"),
                       key=rule, reverse=True)[:6]
        print("  highest-scoring yellow/red claims under min(+court,-opposite):")
        for r in worst:
            print(f"    {r['claim_id']:15s} {r['expected']:7s} score={rule(r):.2f} "
                  f"relation={r['relation']} overstated={F(r['overstated']):.2f}")


if __name__ == "__main__":
    main()
