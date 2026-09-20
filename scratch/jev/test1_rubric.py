"""Test 1 / 1b -- shadow rubric. Records answers; decides nothing.

    venv/Scripts/python.exe scratch/jev/test1_rubric.py            # whole opinion
    venv/Scripts/python.exe scratch/jev/test1_rubric.py --focused  # located passages
"""
from __future__ import annotations

import csv
import statistics
import sys

from jev_common import (RESULTS, ask, clean_text, load_claims, pct,
                        save_cache)

# Literal, atomic questions (Jev reads instructions at face value). Every
# question names the state fields it is about.
RUBRIC = {
    "relation": {
        "type": "choice",
        "instructions": "How does the court opinion in `opinion` relate to "
                        "the legal statement in `proposition`?",
        "criteria": {
            "supports": "The opinion states the proposition, or directly "
                        "implies it is true, as the court's own view",
            "partly": "The opinion addresses the same point, but the "
                      "proposition claims more than the opinion says or "
                      "omits a condition the opinion requires",
            "contradicts": "The opinion states the opposite of the "
                           "proposition or implies it is false",
            "says_nothing": "The opinion does not address what the "
                            "proposition asserts, either way",
        },
    },
    "same_issue": {
        "type": "noul",
        "instructions": "Does `opinion` discuss the same legal issue that "
                        "`proposition` is about?",
    },
    "states_it": {
        "type": "noul",
        "instructions": "Does `opinion` contain a passage that states "
                        "`proposition` or directly implies it is true?",
    },
    "opposite": {
        "type": "noul",
        "instructions": "Does `opinion` state or hold the opposite of "
                        "`proposition`?",
    },
    "overstated": {
        "type": "noul",
        "instructions": "Does `proposition` claim more than `opinion` "
                        "actually says -- broader, more absolute, or with "
                        "fewer conditions?",
    },
    "whose_view": {
        "type": "choice",
        "instructions": "Where `opinion` discusses the idea in "
                        "`proposition`, whose view is it?",
        "criteria": {
            "court": "The court's own holding or reasoning",
            "party": "An argument by a party that the court rejects or "
                     "does not adopt",
            "other_judge": "A dissent or concurrence, not the majority",
            "not_discussed": "The opinion does not discuss the idea",
        },
    },
    "support_level": {
        "type": "score",
        "instructions": "How well does `opinion` support `proposition`?",
        "criteria": [
            "The opinion does not address the proposition",
            "The opinion touches the topic but does not establish the "
            "proposition",
            "The opinion partly supports it; the proposition overstates",
            "The opinion directly and fully supports the proposition",
        ],
    },
}


def build_state(claim, focused: bool):
    row = claim.row
    raw = claim.opinion_path.read_text(encoding="utf-8", errors="ignore")
    if focused:
        from test3_locator import focused_excerpt
        opinion = focused_excerpt(claim, raw)
    else:
        opinion = clean_text(raw)
    return {"proposition": row.get("cited_for") or row["proposition"],
            "brief_sentence": row.get("brief_sentence", ""),
            "opinion": opinion}


def flatten(ans: dict) -> dict:
    rel, who, lvl = ans["relation"], ans["whose_view"], ans["support_level"]
    out = {"relation": rel["choice"], "relation_conf": rel["confidence"]}
    out.update({f"p_{k}": v for k, v in rel["probabilities"].items()})
    for k in ("same_issue", "states_it", "opposite", "overstated"):
        out[k] = ans[k]["noul"]
    out.update({"whose_view": who["choice"], "whose_conf": who["confidence"],
                "p_court": who["probabilities"].get("court", 0.0),
                "support_score": lvl["score"],
                "support_conf": lvl["confidence"]})
    return {k: (round(v, 4) if isinstance(v, float) else v)
            for k, v in out.items()}


def main() -> None:
    focused = "--focused" in sys.argv
    name = "rubric_focused" if focused else "rubric_full"
    claims = [c for c in load_claims() if c.opinion_path and c.expected]
    rows, lat, toks, live = [], [], [], 0
    for i, c in enumerate(claims, 1):
        r = ask(build_state(c, focused), RUBRIC)
        live += not r.cached
        lat.append(r.latency_s)
        toks.append(r.input_tokens)
        rows.append({"corpus": c.corpus, "claim_id": c.claim_id,
                     "expected": c.expected, "eligible": int(c.gate_eligible),
                     "cl_status": c.row.get("cl_status", ""),
                     "quote_worst": c.row.get("quote_check_worst", ""),
                     "input_tokens": r.input_tokens,
                     "latency_s": round(r.latency_s, 3),
                     "cost_usd": round(r.cost_usd, 6), **flatten(r.answers)})
        if i % 20 == 0:
            save_cache()
            print(f"  {i}/{len(claims)}")
    save_cache()

    RESULTS.mkdir(exist_ok=True)
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with (RESULTS / f"{name}.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, restval="")
        w.writeheader()
        w.writerows(rows)

    n = len(rows)
    cost = sum(r["cost_usd"] for r in rows)
    print(f"\n{name}: {n} claims ({live} live calls, {n - live} from cache)")
    print(f"  latency/claim: median {statistics.median(lat):.2f}s  "
          f"mean {statistics.mean(lat):.2f}s  max {max(lat):.2f}s  "
          f"total {sum(lat):.0f}s sequential")
    print(f"  tokens/claim:  median {statistics.median(toks):.0f}  "
          f"max {max(toks)}")
    print(f"  cost: ${cost:.4f} total = ${cost / n:.6f}/claim "
          f"= ${30 * cost / n:.4f} per 30-claim brief")
    green = [r for r in rows if r["expected"] == "green"]
    bad = [r for r in rows if r["expected"] != "green"]
    print(f"  human labels: {len(green)} green, {len(bad)} yellow/red")
    print("  relation=supports:  green " +
          pct(sum(r["relation"] == "supports" for r in green), len(green)) +
          " | yellow/red " +
          pct(sum(r["relation"] == "supports" for r in bad), len(bad)))
    for k in ("same_issue", "states_it", "opposite", "overstated",
              "p_supports", "p_court", "support_score"):
        g = statistics.mean(r[k] for r in green)
        b = statistics.mean(r[k] for r in bad)
        print(f"  mean {k:14s} green {g:.2f} | yellow/red {b:.2f}")


if __name__ == "__main__":
    main()
