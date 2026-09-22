"""Stage-2 dataset: one row per labelled claim, for grading the claims the
auto-Green gate does NOT clear.

Stage 1 (the gate) clears supported claims and escalates the rest. Stage 2 is
the job that starts where stage 1 stops: among the escalated claims, is this an
*overstatement* (the case is on point, the brief claimed more than it says) or
is the case *not doing the work at all* (wrong subject, or the holding runs the
other way)? That ordering drives a review queue; it never auto-clears anything,
so it carries none of the gate's safety risk.

Two independent axes, deliberately kept apart:

  label_source  "detailed" -- badge_label names the KIND of failure, so quote and
                           citation-resolution findings can be DROPPED instead
                           of poisoning the partial class. The primary data.
                "coarse" -- legacy /verify-brief runs that only recorded
                           Green/Yellow/Red. Coarser and from an older
                           assessment prompt: a secondary check, never mixed
                           into the primary result.

  split         "tuning" -- briefs some Jev tuning has already touched.
                "held_out"  -- briefs no Jev tuning has ever touched.

A BRIEF never straddles a split: re-runs and companion filings of the same
matter share a `group`, and near-duplicate propositions are deduplicated
ACROSS the whole group (matters/payne and briefs/payne-proposed are the same
brief twice).

    venv/Scripts/python.exe scratch/jev/stage2_data.py [--live]
"""
from __future__ import annotations

import csv
import difflib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import jev_common
from jev_common import HERE, REPO, ask, save_cache, split_passages
from test3_locator import TOP_K, pid

DATASET = HERE / "stage2_dataset.json"

# --------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------

# badge_label -> stage-2 class.  None = drop (not a support judgement at all).
BADGE = {
    "Supported": "supported",
    "Overstated -- case partially supports": "partial",
    "Not supported by cited case": "unsupported",
    "Case on unrelated subject": "unsupported",
    "Inverts the holding": "unsupported",
    "Reworded -- not a verbatim quote": None,
    "Paraphrase presented as direct quote": None,
    "Quote not found in opinion": None,
    "Citation resolves to different case": None,
    "Unable to verify": None,
    "Unable to verify -- case not in CourtListener": None,
    "Opinion text truncated -- cannot verify": None,
}

# The finer label: WHY it is not supported. This is the taxonomy stage 2 is
# really trying to recover.
KIND = {
    "Supported": "supported",
    "Overstated -- case partially supports": "overstated",
    "Not supported by cited case": "not_supported",
    "Case on unrelated subject": "wrong_subject",
    "Inverts the holding": "inverted",
}

COLOUR = {"Green": "supported", "Yellow": "partial", "Red": "unsupported"}
COLOUR_KIND = {"supported": "supported", "partial": "overstated",
               "unsupported": "not_supported"}

# Legacy free-text assessments sometimes record a QUOTE complaint about a claim
# the case otherwise supports ("principle is supported but the exact phrase
# does not appear"). That is the same contamination the badge rule drops, so
# those rows are flagged `quote_suspect` and reported separately rather than
# silently counted as partial support.
_QUOTE_ONLY = re.compile(
    r"(exact (?:quoted )?(?:language|phrase|wording)|not a direct quote|"
    r"verbatim|paraphrase(?! presented)|does not appear verbatim|"
    r"pinpoint cite|pincite)", re.I)
_SUPPORT_OK = re.compile(
    r"(principle is (?:present|supported)|core holding accurately|"
    r"accurately represented|is stated in the opinion|strongly supports|"
    r"general principle supported|substantively supported)", re.I)
# Not a support judgement at all -- the run failed to get the right document.
_UNUSABLE = re.compile(
    r"(wrong opinion file|could not be found|cannot verify|"
    r"citation (?:appears to be|is) incorrect|belongs to a different case)",
    re.I)

# (workdir, group, split, label_source)
SOURCES = [
    # --- badge-labelled: the primary data ---
    (REPO / "matters/payne",                    "payne",         "tuning", "detailed"),
    (REPO / "matters/kettering-mtd",            "kettering",     "tuning", "detailed"),
    (REPO / "matters/sonnet-q3-protest",        "sonnet-q3",     "tuning", "detailed"),
    (REPO / "matters/ohio-mailbox",             "ohio-mailbox",  "tuning", "detailed"),
    (REPO / "matters/extrinsic-evidence",       "extrinsic",     "tuning", "detailed"),
    (REPO / "matters/withers-v2-demo",          "withers",       "tuning", "detailed"),
    (REPO / "briefs/maxwell-v-michael",         "maxwell",       "held_out",  "detailed"),
    (REPO / "briefs/2026-05-25-ohio-physical-control-system-b",
                                                "ohio-pc",       "held_out",  "detailed"),
    (REPO / "briefs/gov.uscourts.lawd.207038.49.1",
                                                "lawd207038",    "held_out",  "detailed"),
    (REPO / "briefs/protege-makewhole",         "protege",       "held_out",  "detailed"),
    (REPO / "briefs/make-whole-bankruptcy-ny",  "makewhole",     "held_out",  "detailed"),
    # --- colour-labelled: the secondary check ---
    (REPO / "briefs/payne-proposed",            "payne",         "tuning", "coarse"),
    (REPO / "briefs/kettering-v-collier",       "kettering",     "tuning", "coarse"),
    (REPO / "briefs/fletcher-v-experian",       "fletcher",      "held_out",  "coarse"),
    (REPO / "briefs/fivehouse-v-dod",           "fivehouse",     "held_out",  "coarse"),
    (REPO / "briefs/Valve v Rothschild",        "valve",         "held_out",  "coarse"),
]


def _norm_badge(b: str) -> str:
    return " ".join(b.replace("—", "--").replace("–", "--").split())


def _badge_label(row: dict) -> tuple[str, str, str] | None:
    badge = _norm_badge(row.get("badge_label") or "")
    label = BADGE.get(badge)
    if label is None:
        return None
    return label, KIND[badge], badge


def _colour_label(row: dict) -> tuple[str, str, str] | None:
    """(label, kind, badge) from a legacy colour, or None if unusable."""
    # A recorded quote problem forces Yellow regardless of whether the case
    # supports the proposition, so those rows cannot speak to support.
    if (row.get("quote_check_worst") or "") not in ("NO_QUOTES", "VERBATIM", ""):
        return None
    text = (row.get("assessment") or "").strip()
    m = re.match(r"^(Green|Yellow|Red)", text)
    if not m or _UNUSABLE.search(text):
        return None
    label = COLOUR[m.group(1)]
    suspect = bool(_QUOTE_ONLY.search(text) and _SUPPORT_OK.search(text))
    return label, COLOUR_KIND[label], "(quote-suspect)" if suspect else "(colour)"


# --------------------------------------------------------------------------
# Opinion text
# --------------------------------------------------------------------------

def resolve_opinion(workdir: Path, raw_ref: str) -> Path | None:
    """Locate a claim's opinion file.

    The oldest workdirs stored a REPO-relative path that the CSV writer then
    truncated ("briefs/Valve v Rothschild/opinions/moore-v-ashland"), so an
    exact lookup misses every row. Fall back to a unique stem-prefix match
    inside the workdir's opinions/ directory.
    """
    ref = (raw_ref or "").strip()
    if not ref:
        return None
    direct = workdir / ref
    if direct.is_file():
        return direct if direct.suffix.lower() != ".pdf" else None
    stem = ref.split("opinions/")[-1]
    stem = stem[:-4] if stem.endswith(".txt") else stem
    if not stem:
        return None
    hits = [p for p in (workdir / "opinions").glob("*.txt")
            if p.stem.startswith(stem)]
    return hits[0] if len(hits) == 1 else None


# Jev's input cap is ~32K tokens. Corpus opinions all fit in one locator
# request; some briefs' opinions do not. For those, locate in batches that fit
# and run one final locator pass over the pooled per-batch candidates --
# ordinary two-pass retrieval. It collapses to the single-pass path
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
    """-> (excerpt, was_batched)."""
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


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------

def build() -> list[dict]:
    rows: list[dict] = []
    # deduplication is per GROUP, not per workdir: the same brief appears in
    # more than one workdir and must contribute each proposition once.
    seen: dict[str, list[str]] = defaultdict(list)
    drops: Counter = Counter()

    for workdir, group, split, source in SOURCES:
        cf = workdir / "claims.csv"
        if not cf.is_file():
            print(f"  skip (no claims.csv): {workdir}")
            continue
        kept = 0
        for i, row in enumerate(csv.DictReader(cf.open(encoding="utf-8"))):
            got = (_badge_label(row) if source == "detailed"
                   else _colour_label(row))
            if got is None:
                drops[f"{source}: unusable label"] += 1
                continue
            label, kind_, badge = got
            path = resolve_opinion(workdir, row.get("opinion_file") or "")
            if path is None:
                drops["no opinion text"] += 1
                continue
            prop = (row.get("cited_for") or row.get("proposition") or "").strip()
            if not prop:
                drops["no proposition"] += 1
                continue
            if any(difflib.SequenceMatcher(None, prop, p).ratio() > 0.85
                   for p in seen[group]):
                drops["duplicate of another run of the same brief"] += 1
                continue
            seen[group].append(prop)
            try:
                excerpt, batched = long_excerpt(
                    prop, path.read_text(encoding="utf-8", errors="ignore"))
            except Exception as e:
                drops[f"locator failed ({type(e).__name__})"] += 1
                continue
            rows.append({
                "id": f"{workdir.name}:{row.get('claim_id', i)}",
                "group": group, "split": split, "label_source": source,
                "label": label, "kind": kind_, "detailed": badge,
                "quote_suspect": badge == "(quote-suspect)",
                "is_sup": label == "supported",
                "proposition": prop,
                "brief_sentence": row.get("brief_sentence", ""),
                "cited_case": row.get("cited_case", ""),
                "excerpt": excerpt, "batched_locator": batched,
                "why": " ".join((row.get("finding_analysis")
                                 or row.get("assessment") or "").split())[:600],
            })
            kept += 1
        print(f"  {workdir.name[:38]:38s} {group:12s} {split:6s} {source:6s} "
              f"{kept:3d} rows")
    save_cache()
    print("\ndropped:")
    for k, v in drops.most_common():
        print(f"  {v:4d}  {k}")
    return rows


def load_dataset(split: str | None = None,
                 source: str | None = None,
                 drop_quote_suspect: bool = False) -> list[dict]:
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    return [r for r in data
            if (split is None or r["split"] == split)
            and (source is None or r["label_source"] == source)
            and not (drop_quote_suspect and r.get("quote_suspect"))]


def main() -> None:
    if "--live" not in sys.argv:
        jev_common._get_client = lambda: (_ for _ in ()).throw(RuntimeError(
            "cache miss -- locator calls cost money; rerun with --live"))
    rows = build()
    DATASET.write_text(json.dumps(rows, indent=1), encoding="utf-8")

    print()
    hdr = f"{'source':7s} {'split':7s} {'claims':>7s} {'sup':>5s} {'part':>5s} {'unsup':>6s}  groups"
    print(hdr)
    print("-" * len(hdr))
    for source in ("detailed", "coarse"):
        for split in ("tuning", "held_out"):
            sub = [r for r in rows
                   if r["label_source"] == source and r["split"] == split]
            if not sub:
                continue
            c = Counter(r["label"] for r in sub)
            groups = sorted({r["group"] for r in sub})
            print(f"{source:7s} {split:7s} {len(sub):7d} {c['supported']:5d} "
                  f"{c['partial']:5d} {c['unsupported']:6d}  {', '.join(groups)}")
    qs = sum(r["quote_suspect"] for r in rows)
    print(f"\n{len(rows)} claims total; {qs} colour rows flagged quote-suspect "
          f"(pass drop_quote_suspect=True to exclude)")
    print(f"wrote {DATASET}")


if __name__ == "__main__":
    main()
