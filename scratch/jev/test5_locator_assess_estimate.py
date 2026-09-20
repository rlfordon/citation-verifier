"""Test 5 -- locator -> LLM assessment: the OFFLINE cost estimate. No API calls.

    venv/Scripts/python.exe scratch/jev/test5_locator_assess_estimate.py

The paid run was designed and then NOT run (see LOCATOR_ASSESS.md). This
reproduces the numbers behind that decision from locator_assess/jobs.csv:
one row per assessment job (= one opinion), with the full-opinion and
excerpt prompt sizes, what the job actually cost in the 2026-07-01 direct-API
assess-v2 run, the locator's `exists` answer per claim, and the support
verdicts of the frozen v2 cassette and of that July run.
"""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
USD_PER_INPUT_CHAR = 5.0 / 1_000_000 / 4   # Opus $5/MTok, ~4 chars per token
LOW = 0.5


def main() -> None:
    jobs = list(csv.DictReader(
        (HERE / "locator_assess" / "jobs.csv").open(encoding="utf-8")))
    for j in jobs:
        j["cost"] = float(j["july_api_cost_usd"])
        j["saved"] = (int(j["v2_input_chars"]) - int(j["v3_input_chars"])) \
            * USD_PER_INPUT_CHAR
        j["ex"] = [float(x) for x in j["exists"].split()]
        j["v2"] = j["v2_support"].split()
        j["july"] = j["july_api_support"].split()
    n_claims = sum(len(j["ex"]) for j in jobs)
    base = sum(j["cost"] for j in jobs)
    v2_in = sum(int(j["v2_input_chars"]) for j in jobs)
    v3_in = sum(int(j["v3_input_chars"]) for j in jobs)
    print(f"{len(jobs)} jobs, {n_claims} claims")
    print(f"full-opinion run (2026-07-01, direct API): ${base:.2f} "
          f"= ${base / n_claims:.3f}/claim = ${30 * base / n_claims:.2f} "
          f"per 30-claim brief")
    print(f"input: {v2_in // 4:,} est. tokens full vs {v3_in // 4:,} excerpts "
          f"({100 * v3_in / v2_in:.0f}%); input share of the full-opinion "
          f"bill ~{100 * v2_in * USD_PER_INPUT_CHAR / base:.0f}%")

    def report(name: str, cost: float, fallbacks: int) -> None:
        save = base - cost
        print(f"  {name:<58} ${cost:5.2f}  saves {100 * save / base:4.0f}%"
              f" = ${30 * save / n_claims:5.2f}/brief  ({fallbacks} jobs in full)")

    print("\nestimated cost under each rule (excerpt job = July cost minus "
          "the input it no longer sends):")
    excerpt = {id(j): j["cost"] - j["saved"] for j in jobs}
    low = [j for j in jobs if min(j["ex"]) < LOW]
    unsup = [j for j in jobs if "unsupported" in j["v2"]]
    notsup = [j for j in jobs if set(j["v2"]) - {"supported"}]
    report("no fallback", sum(excerpt.values()), 0)
    report(f"exists < {LOW}: read excerpts, then re-read in full",
           sum(excerpt.values()) + sum(j["cost"] for j in low), len(low))
    report(f"exists < {LOW}: skip excerpts, go straight to full",
           sum(j["cost"] if j in low else excerpt[id(j)] for j in jobs),
           len(low))
    report("re-read when v3 says unverifiable (proxy: v2 unsupported)",
           sum(excerpt.values()) + sum(j["cost"] for j in unsup), len(unsup))
    report("re-read on anything but supported (proxy: v2 not supported)",
           sum(excerpt.values()) + sum(j["cost"] for j in notsup), len(notsup))

    print(f"\nlocator `exists` vs the frozen v2 verdict (claims under {LOW} / all):")
    tally: dict[str, list[float]] = {}
    for j in jobs:
        for e, s in zip(j["ex"], j["v2"]):
            tally.setdefault(s, []).append(e)
    for s, ex in sorted(tally.items()):
        print(f"  {s:<12} {sum(e < LOW for e in ex):>2} / {len(ex):<3} "
              f"max exists {max(ex):.2f}  min {min(ex):.2f}")

    flips = Counter((a, b) for j in jobs for a, b in zip(j["v2"], j["july"])
                    if a != b)
    print(f"\nnoise floor -- same prompt, same full opinions, frozen v2 vs the "
          f"July API run: {sum(flips.values())} of {n_claims} claims changed "
          f"support")
    for (a, b), n in flips.most_common():
        print(f"  {a} -> {b}: {n}")


if __name__ == "__main__":
    main()
