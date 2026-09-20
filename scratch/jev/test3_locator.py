"""Test 3 -- passage locator (TypeSafe "line-by-line search" pattern).

    venv/Scripts/python.exe scratch/jev/test3_locator.py

Reference answer = the passage(s) holding the recorded Opus opinion_block
quote, found by exact string match (no new labelling).
"""
from __future__ import annotations

import csv
import re
import statistics
import sys
from pathlib import Path

from jev_common import (REPO, RESULTS, ask, load_claims, load_verdicts, pct,
                        save_cache, split_passages)

sys.path.insert(0, str(REPO / "src"))
from citation_verifier.quote_matcher import (  # noqa: E402
    _normalize_quote_text, _straighten_quotes)

TOP_K = 5
_ELLIPSIS = re.compile(r"\s*(?:\[?\s*(?:\.\s*){3,}\]?|\[?…\]?)\s*")


def pid(i: int) -> str:
    return f"P{i:03d}"


def locate(claim, raw: str):
    """-> (passages, ranked passage indices, per-passage prob, exists, result)"""
    passages = split_passages(raw)
    prop = claim.row.get("cited_for") or claim.row["proposition"]
    state = "\n".join(f"{pid(i)}| {p}" for i, p in enumerate(passages))
    questions = {
        "where": {
            "type": "choice",
            "instructions": "Which passage of the court opinion best supports "
                            f'or addresses this legal statement: "{prop}"?',
            "criteria": {pid(i): None for i in range(len(passages))},
        },
        "exists": {
            "type": "noul",
            "instructions": "Does any passage of the court opinion state or "
                            f'directly imply this legal statement: "{prop}"?',
            "criteria": {
                "true": "At least one passage states or directly implies it",
                "false": "No passage addresses it",
            },
        },
    }
    r = ask(state, questions)
    probs = r.answers["where"]["probabilities"]
    p = [probs.get(pid(i), 0.0) for i in range(len(passages))]
    ranked = sorted(range(len(passages)), key=lambda i: p[i], reverse=True)
    return passages, ranked, p, r.answers["exists"]["noul"], r


def focused_excerpt(claim, raw: str, k: int = TOP_K) -> str:
    """Top-k located passages plus one neighbour each side, document order."""
    passages, ranked, *_ = locate(claim, raw)
    keep = set()
    for i in ranked[:k]:
        keep.update(j for j in (i - 1, i, i + 1) if 0 <= j < len(passages))
    out, prev = [], None
    for i in sorted(keep):
        if prev is not None and i != prev + 1:
            out.append("[...]")
        out.append(passages[i])
        prev = i
    return " ".join(out)


def reference_passages(block: str, passages: list[str]) -> set[int]:
    """Indices of passages that contain an opinion_block segment verbatim."""
    norm = [_straighten_quotes(p).lower() for p in passages]
    joined, starts, pos = "", [], 0
    for n in norm:
        starts.append(pos)
        joined += n + " "
        pos = len(joined)
    refs: set[int] = set()
    for para in re.split(r"\n\s*\n", block):
        for seg in _ELLIPSIS.split(para):
            seg = _normalize_quote_text(seg).lower()
            if len(seg.split()) < 6:
                continue
            at = joined.find(seg)
            if at < 0:
                continue
            for i, s in enumerate(starts):
                e = starts[i + 1] if i + 1 < len(starts) else len(joined)
                if s < at + len(seg) and at < e:
                    refs.add(i)
    return refs


def main() -> None:
    claims = [c for c in load_claims() if c.opinion_path and c.expected]
    verdicts = {}
    for c in claims:
        verdicts.setdefault(c.corpus, load_verdicts(c.corpus))
    rows = []
    for n, c in enumerate(claims, 1):
        raw = c.opinion_path.read_text(encoding="utf-8", errors="ignore")
        passages, ranked, p, exists, r = locate(c, raw)
        block = ((verdicts[c.corpus].get(c.claim_id) or {})
                 .get("fields", {}).get("opinion_block", "") or "")
        refs = reference_passages(block, passages) if block.strip() else set()
        rank = min((ranked.index(i) for i in refs), default=None)
        excerpt = focused_excerpt(c, raw)
        rows.append({
            "corpus": c.corpus, "claim_id": c.claim_id,
            "expected": c.expected, "n_passages": len(passages),
            "exists": round(exists, 4), "top1_prob": round(p[ranked[0]], 4),
            "has_ref": int(bool(refs)),
            "ref_rank": "" if rank is None else rank + 1,
            "chars_full": sum(map(len, passages)), "chars_top5": len(excerpt),
            "input_tokens": r.input_tokens,
            "latency_s": round(r.latency_s, 3),
            "cost_usd": round(r.cost_usd, 6)})
        if n % 20 == 0:
            save_cache()
            print(f"  {n}/{len(claims)}")
    save_cache()

    RESULTS.mkdir(exist_ok=True)
    with (RESULTS / "locator.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    n = len(rows)
    lat = [r["latency_s"] for r in rows]
    cost = sum(r["cost_usd"] for r in rows)
    print(f"\nlocator: {n} claims; passages/opinion median "
          f"{statistics.median(r['n_passages'] for r in rows):.0f}, "
          f"max {max(r['n_passages'] for r in rows)}")
    print(f"  latency/claim: median {statistics.median(lat):.2f}s  "
          f"max {max(lat):.2f}s  total {sum(lat):.0f}s sequential")
    print(f"  cost: ${cost:.4f} total = ${30 * cost / n:.4f} per 30-claim brief")
    ref = [r for r in rows if r["has_ref"]]
    print(f"  claims with a usable Opus reference passage: {len(ref)}")
    for k in (1, 3, 5, 10):
        print(f"    hit@{k}: " + pct(sum(r["ref_rank"] <= k for r in ref), len(ref)))
    full = sum(r["chars_full"] for r in rows)
    top = sum(r["chars_top5"] for r in rows)
    print(f"  prompt size, top-{TOP_K} passages (+neighbours) vs whole opinion: "
          f"{top // 4:,} vs {full // 4:,} est. tokens = {100 * top / full:.0f}%")
    green = [r["exists"] for r in rows if r["expected"] == "green"]
    bad = [r["exists"] for r in rows if r["expected"] != "green"]
    print(f"  `exists` Noul mean: green {statistics.mean(green):.2f} | "
          f"yellow/red {statistics.mean(bad):.2f}")


if __name__ == "__main__":
    main()
