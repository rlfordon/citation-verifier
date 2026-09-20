"""Build the phrasing-loop dataset: one row per labelled claim, with the
located-passage excerpt frozen in (the locator is held fixed; only the rubric
is searched). Locator calls come from cache.json, so this is free to rerun.

    venv/Scripts/python.exe scratch/jev/loop_data.py
"""
from __future__ import annotations

import csv
import difflib
import json
from collections import Counter

from jev_common import HERE, REPO, Claim, load_claims, save_cache
from test3_locator import focused_excerpt

DATASET = HERE / "loop_dataset.json"

# Human labels (frozen corpora) and Opus labels (held-out run) on one scale.
_LABEL = {"green": "supported", "yellow": "partial", "red": "unsupported"}


def _row(claim: Claim, label: str, source: str, group: str, flags: bool) -> dict:
    raw = claim.opinion_path.read_text(encoding="utf-8", errors="ignore")
    r = claim.row
    return {
        "id": f"{source}:{claim.claim_id}", "group": group, "label": label,
        "label_source": source, "is_pos": label == "supported",
        "eligible": bool(claim.gate_eligible and not flags),
        "proposition": r.get("cited_for") or r["proposition"],
        "brief_sentence": r.get("brief_sentence", ""),
        "cited_case": r.get("cited_case", ""),
        "excerpt": focused_excerpt(claim, raw),
    }


def build() -> list[dict]:
    rows = []
    for c in load_claims():
        if c.opinion_path and c.expected in _LABEL:
            rows.append(_row(c, _LABEL[c.expected], "human", c.corpus, False))

    # Held-out prior run: Opus labels. Same brief as the payne corpus, so it
    # joins the "payne" group (never split across folds) and near-duplicate
    # propositions are dropped in favour of the human-labelled row.
    human_payne = [r["proposition"] for r in rows if r["group"] == "payne"]
    workdir = REPO / "matters" / "payne"
    dropped = 0
    for row in csv.DictReader((workdir / "claims.csv").open(encoding="utf-8")):
        f, support = row.get("opinion_file", ""), (row.get("support") or "").strip()
        if not f or not (workdir / f).is_file():
            continue
        if support not in ("supported", "partial", "unsupported"):
            continue
        prop = row.get("cited_for") or row["proposition"]
        if any(difflib.SequenceMatcher(None, prop, h).ratio() > 0.8
               for h in human_payne):
            dropped += 1
            continue
        claim = Claim("matters-payne", row["claim_id"], row, "", workdir / f)
        flags = (row.get("crosscheck_flags") or "").strip() not in ("", "[]")
        rows.append(_row(claim, support, "opus", "payne", flags))
    save_cache()
    print(f"dropped {dropped} matters/payne claims as near-duplicates of "
          f"human-labelled payne claims")
    return rows


def load_dataset() -> list[dict]:
    return json.loads(DATASET.read_text(encoding="utf-8"))


if __name__ == "__main__":
    data = build()
    DATASET.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"{len(data)} claims -> {DATASET.name}")
    for g in sorted({r["group"] for r in data}):
        sub = [r for r in data if r["group"] == g]
        print(f"  {g:11s} n={len(sub):3d} labels={dict(Counter(r['label'] for r in sub))} "
              f"eligible={sum(r['eligible'] for r in sub)} "
              f"(eligible negatives={sum(r['eligible'] and not r['is_pos'] for r in sub)})")
