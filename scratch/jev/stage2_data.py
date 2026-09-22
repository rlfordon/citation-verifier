"""Stage-2 dataset: one row per claim, labelled by BADGE, not by colour.

Stage 1 (the auto-Green gate) clears supported claims and escalates the rest.
Stage 2 is the job that starts where stage 1 stops: among the claims that did
NOT clear, is this an *overstatement* (the case is on point, the brief claimed
more than it says) or is the case *not doing the work at all* (wrong subject,
or the holding runs the other way)? That distinction orders a review queue; it
never auto-clears anything, so it carries none of the gate's safety risk.

Why badge_label and not the colour: a Yellow can mean "quote is a paraphrase",
which is not a support failure at all. The badge names the KIND of failure, so
quote and citation-resolution problems can be dropped instead of poisoning the
partial class.

Sources: every workdir (matters/ + briefs/) whose claims.csv has badge_label
and readable opinion text. Split is by BRIEF -- a brief never straddles
search and lock -- and `lock2` briefs are ones no Jev tuning has ever touched.

    venv/Scripts/python.exe scratch/jev/stage2_data.py [--live]
"""
from __future__ import annotations

import csv
import difflib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import jev_common
from jev_common import REPO, HERE, Claim, save_cache
from test3_locator import TOP_K, focused_excerpt, pid
from jev_common import ask, split_passages

DATASET = HERE / "stage2_dataset.json"

# badge_label -> stage-2 class.  None = drop (not a support judgement).
BADGE = {
    "Supported": "supported",
    "Overstated -- case partially supports": "partial",
    "Not supported by cited case": "unsupported",
    "Case on unrelated subject": "unsupported",
    "Inverts the holding": "unsupported",
    # Dropped: these are quote or citation-resolution findings. The case may
    # support the proposition perfectly well; something else went wrong.
    "Reworded -- not a verbatim quote": None,
    "Paraphrase presented as direct quote": None,
    "Quote not found in opinion": None,
    "Citation resolves to different case": None,
    "Unable to verify": None,
    "Unable to verify -- case not in CourtListener": None,
    "Opinion text truncated -- cannot verify": None,
}

# A finer label kept alongside: WHY it is unsupported. This is the taxonomy
# stage 2 is really trying to recover.
KIND = {
    "Supported": "supported",
    "Overstated -- case partially supports": "overstated",
    "Not supported by cited case": "not_supported",
    "Case on unrelated subject": "wrong_subject",
    "Inverts the holding": "inverted",
}

# (workdir, group, split). `group` merges re-runs of the same brief so a brief
# never straddles a split. lock2 = never seen by any Jev tuning to date.
SOURCES = [
    (REPO / "matters/payne",                "payne",         "search"),
    (REPO / "matters/kettering-mtd",        "kettering-mtd", "search"),
    (REPO / "matters/sonnet-q3-protest",    "sonnet-q3",     "search"),
    (REPO / "matters/ohio-mailbox",         "ohio-mailbox",  "search"),
    (REPO / "matters/extrinsic-evidence",   "extrinsic",     "search"),
    # same brief as the withers corpus, which the loop searched over
    (REPO / "matters/withers-v2-demo",      "withers",       "search"),
    (REPO / "briefs/maxwell-v-michael",                    "maxwell",     "lock2"),
    (REPO / "briefs/2026-05-25-ohio-physical-control-system-b", "ohio-pc", "lock2"),
    (REPO / "briefs/gov.uscourts.lawd.207038.49.1",        "lawd207038",  "lock2"),
    (REPO / "briefs/protege-makewhole",                    "protege",     "lock2"),
    (REPO / "briefs/make-whole-bankruptcy-ny",             "makewhole",   "lock2"),
]

# Legacy /verify-brief runs: no badge_label, only a colour. Coarser and from an
# older assessment prompt, so they are a SECONDARY check (split "colour"),
# never mixed into the badge-labelled primary result. Claims with any quote
# problem are dropped, because a quote floor forces Yellow regardless of
# whether the case supports the proposition -- that contamination is exactly
# what made the old "partial" class noisy.
COLOUR = {"Green": "supported", "Yellow": "partial", "Red": "unsupported"}
COLOUR_KIND = {"supported": "supported", "partial": "overstated",
               "unsupported": "not_supported"}
COLOUR_SOURCES = [
    (REPO / "briefs/fletcher-v-experian", "fletcher",  "colour"),
    (REPO / "briefs/fivehouse-v-dod",     "fivehouse", "colour"),
    # same brief as matters/payne, so it joins that group and never lands on
    # the other side of a split
    (REPO / "briefs/payne-proposed",      "payne",     "search"),
]


# Jev's input cap is ~32K tokens. Corpus opinions all fit in one locator
# request; some briefs' opinions do not. For those we locate in batches that
# fit, then run one final locator pass over the pooled per-batch candidates --
# ordinary two-pass retrieval, and it collapses to the single-pass path
# (byte-identical request, so the cache still hits) whenever the opinion fits.
MAX_STATE_CHARS = 90_000


def _locate_ranked(prop: str, passages: list[str]) -> list[int]:
    state = chr(10).join(f"{pid(i)}| {p}" for i, p in enumerate(passages))
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
    probs = ask(state, questions).answers["where"]["probabilities"]
    return sorted(range(len(passages)),
                  key=lambda i: probs.get(pid(i), 0.0), reverse=True)


def long_excerpt(prop: str, raw: str, k: int = TOP_K) -> tuple[str, bool]:
    """-> (excerpt, was_batched). Same output shape as focused_excerpt."""
    passages = split_passages(raw)
    total = sum(len(p) for p in passages) + 6 * len(passages)
    batched = total > MAX_STATE_CHARS
    if not batched:
        ranked = _locate_ranked(prop, passages)
    else:
        n_batches = total // MAX_STATE_CHARS + 1
        size = len(passages) // n_batches + 1
        cand: list[int] = []
        for start in range(0, len(passages), size):
            chunk = passages[start:start + size]
            cand += [start + i for i in _locate_ranked(prop, chunk)[:k]]
        cand = sorted(set(cand))
        final = _locate_ranked(prop, [passages[i] for i in cand])
        ranked = [cand[i] for i in final]
    keep: set[int] = set()
    for i in ranked[:k]:
        keep.update(j for j in (i - 1, i, i + 1) if 0 <= j < len(passages))
    out, prev = [], None
    for i in sorted(keep):
        if prev is not None and i != prev + 1:
            out.append("[...]")
        out.append(passages[i])
        prev = i
    return " ".join(out), batched


def _norm_badge(b: str) -> str:
    return " ".join(b.replace("\u2014", "--").replace("\u2013", "--").split())


def _colour_label(row: dict) -> tuple[str, str] | None:
    """(label, kind) from a legacy colour, or None if unusable."""
    if (row.get("quote_check_worst") or "") not in ("NO_QUOTES", "VERBATIM", ""):
        return None
    colour = (row.get("assessment") or "").strip()[:6].strip()
    label = COLOUR.get(colour)
    return (label, COLOUR_KIND[label]) if label else None


def build() -> list[dict]:
    rows: list[dict] = []
    dropped_badge = 0
    for workdir, group, split in SOURCES + COLOUR_SOURCES:
        colour_src = (workdir, group, split) in COLOUR_SOURCES
        cf = workdir / "claims.csv"
        if not cf.is_file():
            print(f"  skip (no claims.csv): {workdir}")
            continue
        seen_props: list[str] = []
        n = 0
        for row in csv.DictReader(cf.open(encoding="utf-8")):
            if colour_src:
                got = _colour_label(row)
                if got is None:
                    continue
                label, kind_, badge = got[0], got[1], "(colour)"
            else:
                badge = _norm_badge(row.get("badge_label") or "")
                label = BADGE.get(badge)
                if label is None:
                    dropped_badge += badge != ""
                    continue
                kind_ = KIND[badge]
            f = row.get("opinion_file") or ""
            path = workdir / f
            if not f or not path.is_file() or path.suffix.lower() == ".pdf":
                continue
            prop = row.get("cited_for") or row.get("proposition") or ""
            if not prop.strip():
                continue
            # a brief re-run twice contributes each proposition once
            if any(difflib.SequenceMatcher(None, prop, p).ratio() > 0.85
                   for p in seen_props):
                continue
            seen_props.append(prop)
            raw = path.read_text(encoding="utf-8", errors="ignore")
            try:
                excerpt, batched = long_excerpt(prop, raw)
            except Exception as e:
                print(f"    skip {row.get('claim_id', n)}: {type(e).__name__}")
                continue
            rows.append({
                "id": f"{group}:{row.get('claim_id', n)}",
                "group": group, "split": split,
                "label": label, "kind": kind_, "badge": badge,
                "label_source": "colour" if colour_src else "badge",
                "is_sup": label == "supported",
                "proposition": prop,
                "brief_sentence": row.get("brief_sentence", ""),
                "cited_case": row.get("cited_case", ""),
                "excerpt": excerpt, "batched_locator": batched,
                "why": " ".join((row.get("finding_analysis") or "").split())[:600],
            })
            n += 1
        print(f"  {group:14s} {split:6s} {n:3d} rows")
    save_cache()
    print(f"\ndropped {dropped_badge} claims whose badge is a quote or "
          f"citation-resolution finding, not a support judgement")
    return rows


def load_dataset(split: str | None = None) -> list[dict]:
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    return [r for r in data if split is None or r["split"] == split]


def main() -> None:
    if "--live" not in sys.argv:
        jev_common._get_client = lambda: (_ for _ in ()).throw(RuntimeError(
            "cache miss -- locator calls cost money; rerun with --live"))
    rows = build()
    DATASET.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    from collections import Counter
    for split in ("search", "lock2", "colour"):
        sub = [r for r in rows if r["split"] == split]
        print(f"\n{split}: {len(sub)} claims  {dict(Counter(r['label'] for r in sub))}")
        print(f"   kinds: {dict(Counter(r['kind'] for r in sub if not r['is_sup']))}")
    print(f"\nwrote {DATASET}")


if __name__ == "__main__":
    main()
