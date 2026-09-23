"""A fast, LLM-free proposition check, built on Jev.

Rough prototype -- prints to the terminal, writes one CSV, touches nothing the
real pipeline owns. Run it on a workdir that already has claims.csv and
downloaded opinions (i.e. after `verify-propositions <workdir> verify merge`).

    venv/Scripts/python.exe tools/poor_mans_check.py matters/ohio-mailbox
    venv/Scripts/python.exe tools/poor_mans_check.py briefs/maxwell-v-michael --truth

Three outcomes per claim:

    LOOKS OK     every check passed and the clearing question was confident
    PROBLEM      a check fired that almost never fires on a good citation
    REVIEW       in between -- Jev cannot tell, a human or the LLM must read it

Thresholds come from scratch/jev/STAGE2.md test 8, fitted on six briefs and
measured on five it had never seen: nothing cleared wrongly, nothing accused
wrongly, roughly a third of claims answered. Flag recall is only ~40%, so
REVIEW means "not checked", never "fine".
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import textwrap
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scratch" / "jev"))

from citation_verifier import jev_shadow  # noqa: E402

# --- thresholds (scratch/jev/test8_checker.py, 2026-09-22, 332 claims) -------
CLEAR_ABOVE = 0.580      # on "supported exactly as written"
FLAG_BELOW = 0.130       # on min(whose view, same issue)
LOW = {"as_written": 0.58, "coverage": 0.50, "whose_view": 0.50, "same_issue": 0.50}

RUBRIC = json.loads((REPO / "scratch/jev/bank/checker_v0.json").read_text(
    encoding="utf-8"))

# Which named answer means what, in words a reader can act on.
REASONS = {
    ("as_written", "not_fully"): "adds to or strengthens what the opinion says",
    ("coverage", "main_part_only"): "main point is there, but one clause or "
                                    "qualifier is not",
    ("coverage", "little_or_none"): "the opinion does not state the main point",
    ("whose_view", "party"): "this is a party's argument the court did not adopt",
    ("whose_view", "other_judge"): "this comes from a dissent or concurrence",
    ("whose_view", "not_discussed"): "the opinion does not discuss this idea",
    ("same_issue", "no"): "the opinion is about a different legal issue",
}


def green_value(ans, green):
    if ans["type"] == "noul":
        return ans["noul"] if green == "yes" else 1.0 - ans["noul"]
    opts = [green] if isinstance(green, str) else green
    return sum(ans["probabilities"].get(o, 0.0) for o in opts)


def resolve_opinion(workdir: Path, ref: str) -> Path | None:
    """Locate a claim's opinion file, tolerating the truncated repo-relative
    paths the oldest workdirs wrote."""
    ref = (ref or "").strip()
    if not ref:
        return None
    direct = workdir / ref
    if direct.is_file() and direct.suffix.lower() != ".pdf":
        return direct
    stem = ref.split("opinions/")[-1]
    stem = stem[:-4] if stem.endswith(".txt") else stem
    hits = [p for p in (workdir / "opinions").glob("*.txt")
            if stem and p.stem.startswith(stem)]
    return hits[0] if len(hits) == 1 else None


def deterministic(row: dict) -> tuple[str | None, str | None]:
    """What the free, code-only checks already know, before Jev is asked.
    -> (finding, blocker). A finding is reported as a problem; a blocker only
    prevents clearing.

    These run first because they catch what Jev is blind to. A fabricated
    quote attached to a proposition the case genuinely supports scores 0.99 on
    every Jev question -- correctly, because the *proposition* is supported.
    Only the quote matcher sees it.

    CLOSE is deliberately NOT a finding: the pipeline's own quote floor treats
    CLOSE as a transcription-noise band, and the quote matcher has a known
    ceiling bug that scores real verbatim quotes as CLOSE (scratch/TODO.md).
    POSSIBLE_MATCH likewise -- it means the citation resolved but the name did
    not match cleanly, which is worth a look, not an accusation.
    """
    status = (row.get("cl_status") or "").strip()
    q = (row.get("quote_check_worst") or "").strip()
    flags = (row.get("crosscheck_flags") or "").strip()

    if status in ("NOT_FOUND", "WRONG_CASE"):
        return f"the citation could not be verified ({status})", None
    if q == "FABRICATED":
        return "quoted text does not appear in the opinion", None

    if status and status not in ("VERIFIED", "VERIFIED_VIA_RECAP",
                                 "VERIFIED_PARTIAL"):
        return None, f"citation verified only loosely ({status})"
    if q == "CLOSE":
        return None, "quoted text is close but not exact"
    if q == "NO_OPINION":
        return None, "no opinion text was available"
    if flags not in ("", "[]"):
        return None, f"cross-check flag: {flags[:60]}"
    return None, None


def check_one(prop: str, raw: str, ask, row: dict) -> dict:
    passages = jev_shadow.split_passages(raw)
    loc = jev_shadow.locate(prop, passages, ask)
    excerpt = jev_shadow.join_passages(
        passages, jev_shadow.keep_indices(len(passages), loc["ranked"]))

    qs = {k: {a: b for a, b in q.items() if not a.startswith("_")}
          for k, q in RUBRIC["questions"].items()}
    a = ask({"proposition": prop, "opinion": excerpt}, qs)["answers"]

    vals = {q: green_value(a[q], RUBRIC["questions"][q]["_green"])
            for q in RUBRIC["questions"]}
    low = [q for q, v in vals.items() if v < LOW[q]]

    reasons = []
    for q in RUBRIC["questions"]:
        if q not in low:
            continue
        if a[q]["type"] == "choice":
            reasons.append(REASONS.get((q, a[q]["choice"]), f"{q}: {a[q]['choice']}"))
        else:
            reasons.append(REASONS.get((q, "no"), q))

    finding, blocker = deterministic(row)
    if finding:
        verdict = "PROBLEM"
        reasons.insert(0, finding)
    elif min(vals["whose_view"], vals["same_issue"]) < FLAG_BELOW:
        verdict = "PROBLEM"
    elif vals["as_written"] > CLEAR_ABOVE and not blocker:
        verdict = "LOOKS OK"
    else:
        verdict = "REVIEW"
        if blocker:
            reasons.insert(0, blocker)
    return {"verdict": verdict, "checks_low": len(low), "reasons": reasons,
            "scores": {k: round(v, 3) for k, v in vals.items()},
            "excerpt_chars": len(excerpt)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument("--truth", action="store_true",
                    help="also print the recorded LLM verdict, to compare")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    workdir = (REPO / args.workdir) if not Path(args.workdir).is_absolute() \
        else Path(args.workdir)
    claims = list(csv.DictReader((workdir / "claims.csv").open(encoding="utf-8")))
    if args.limit:
        claims = claims[:args.limit]

    ask = jev_shadow._default_ask()
    t0 = time.perf_counter()
    out, skipped = [], 0
    for i, c in enumerate(claims):
        prop = (c.get("cited_for") or c.get("proposition") or "").strip()
        path = resolve_opinion(workdir, c.get("opinion_file") or "")
        if not prop or path is None:
            skipped += 1
            continue
        try:
            r = check_one(prop, path.read_text(encoding="utf-8", errors="ignore"),
                          ask, c)
        except Exception as e:
            print(f"  [{i}] failed: {type(e).__name__}: {e}")
            skipped += 1
            continue
        r.update(claim_id=c.get("claim_id", str(i)),
                 cited_case=(c.get("cited_case") or "")[:60], proposition=prop,
                 truth=(c.get("badge_label") or c.get("assessment") or "")[:44])
        out.append(r)
    elapsed = time.perf_counter() - t0

    order = {"PROBLEM": 0, "REVIEW": 1, "LOOKS OK": 2}
    for r in sorted(out, key=lambda r: (order[r["verdict"]], -r["checks_low"])):
        marks = "#" * r["checks_low"] + "." * (4 - r["checks_low"])
        print(f"\n{r['verdict']:8s} [{marks}]  {r['cited_case']}")
        print(textwrap.fill(r["proposition"], 92, initial_indent="           ",
                            subsequent_indent="           "))
        for x in r["reasons"]:
            print(f"             - {x}")
        if args.truth and r["truth"]:
            print(f"             (LLM said: {r['truth']})")

    n = len(out)
    print(f"\n{'=' * 78}\n{workdir.name}: {n} claims checked"
          f"{f', {skipped} skipped (no opinion text)' if skipped else ''}"
          f"  --  {elapsed:.1f}s")
    for v in ("LOOKS OK", "REVIEW", "PROBLEM"):
        k = sum(r["verdict"] == v for r in out)
        print(f"  {v:9s} {k:3d} ({k / n:3.0%})" if n else "")
    print("\nREVIEW means Jev could not tell -- not that the citation is fine.")
    print("About 4 in 10 bad citations get caught; the rest land in REVIEW.")

    dest = workdir / "poor_mans_check.csv"
    with open(dest, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["claim_id", "verdict", "checks_low", "reasons",
                    "as_written", "coverage", "whose_view", "same_issue",
                    "cited_case", "proposition"])
        for r in out:
            w.writerow([r["claim_id"], r["verdict"], r["checks_low"],
                        " | ".join(r["reasons"]), *[r["scores"][k] for k in
                        ("as_written", "coverage", "whose_view", "same_issue")],
                        r["cited_case"], r["proposition"]])
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
