"""How would the Jev auto-Green gate have done on real runs?

Joins each workdir's jobs/jev_shadow.jsonl (Jev's answers, logged in shadow
mode) to its claims.csv `support` column (what the LLM assessment reported).
A "bad clear" = a gate-eligible claim the gate would have turned Green although
the assessment reported partial/unsupported.

    venv/Scripts/python.exe tools/jev_shadow_report.py            # all of matters/
    venv/Scripts/python.exe tools/jev_shadow_report.py matters/payne --gate gate_v0

Zero bad clears out of N not-supported claims still allows a true rate of
roughly 3/N (the "rule of three"); the report prints that bound.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

THRESHOLDS = (0.33, 0.50, 0.70, 0.90, 0.95)


def load(workdir: Path) -> list[dict]:
    log = workdir / "jobs" / "jev_shadow.jsonl"
    claims = workdir / "claims.csv"
    if not (log.is_file() and claims.is_file()):
        return []
    support = {c["claim_id"]: (c.get("support") or "").strip()
               for c in csv.DictReader(claims.open(encoding="utf-8"))}
    rows = {}
    for line in log.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if "error" in r:
            continue
        s = support.get(r["claim_id"], "")
        if s in ("supported", "partial", "unsupported"):
            rows[(r["claim_id"], r["rubric_version"])] = {
                **r, "support": s, "run": workdir.name}
    return list(rows.values())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("workdirs", nargs="*", type=Path)
    ap.add_argument("--gate", default="gate_v1",
                    help="gate_v1 = as-written (2026-09-19 loop champion); "
                         "gate_v0 = original min(relation, states_it, same_issue)")
    args = ap.parse_args()
    dirs = args.workdirs or sorted(
        p for p in Path("matters").iterdir() if p.is_dir())
    rows = [r for d in dirs for r in load(d)]
    if not rows:
        print("no shadow logs with assessed claims found")
        return

    elig = [r for r in rows if r["eligible"]]
    neg = [r for r in elig if r["support"] != "supported"]
    runs = sorted({r["run"] for r in rows})
    print(f"{len(rows)} assessed claims in {len(runs)} runs; {len(elig)} "
          f"gate-eligible ({len(elig) - len(neg)} supported, "
          f"{sum(r['support'] == 'partial' for r in neg)} partial, "
          f"{sum(r['support'] == 'unsupported' for r in neg)} unsupported)\n")

    g = args.gate
    print(f"{g}: claims cleared / bad clears, by threshold")
    print(f"{'run':22s}" + "".join(f"{t:>12.2f}" for t in THRESHOLDS))
    for run in runs + ["ALL"]:
        sub = [r for r in elig if run in ("ALL", r["run"])]
        cells = []
        for t in THRESHOLDS:
            hit = [r for r in sub if r[g] > t]
            bad = sum(r["support"] != "supported" for r in hit)
            cells.append(f"{len(hit):>7d} /{bad:>2d}")
        print(f"{run:22s}" + "".join(f"{c:>12s}" for c in cells))
    print(f"\nshare of ALL assessed claims cleared: " + "  ".join(
        f"{t:.2f}: {100 * sum(r[g] > t for r in elig) / len(rows):.0f}%"
        for t in THRESHOLDS))

    top = sorted(neg, key=lambda r: r[g], reverse=True)[:5]
    print(f"\nhighest-scoring not-supported claims ({g}):")
    for r in top:
        print(f"  {r[g]:.3f}  {r['support']:11s} {r['run']}:{r['claim_id']}")
    if neg:
        print(f"\n{len(neg)} eligible not-supported claims: zero bad clears "
              f"would still allow a true bad-clear rate up to "
              f"~{300 / len(neg):.0f}%.")


if __name__ == "__main__":
    main()
