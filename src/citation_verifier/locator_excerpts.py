"""Locator excerpts: one excerpt file per opinion, for the assess-v3 test.

EXPERIMENT (docs/plans/2026-09-19-jev-locator-assess-test.md): does the LLM
reach the same verdicts reading only the passages Jev's locator picks? This
module writes the excerpt files; ``run_assess`` points assess-v3 jobs at them
instead of at the whole opinion.

The locator is ``jev_shadow``'s, imported, not copied: same passage split,
same two questions, same top-5-plus-neighbours rule. Per opinion the excerpt
is the UNION of every citing claim's kept passages, in document order, with
``[...]`` on its own line for each gap. Passage 0 (the caption) is always
kept so the reader can tell which case it is looking at.

Like jev-shadow, this sends propositions and opinion text to TypeSafe's API:
run it on public documents only. Writes ``jobs/excerpts/<opinion-stem>.txt``
and appends one row per claim to ``jobs/locator_log.jsonl``; touches nothing
else.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from . import jev_shadow as js

EXCERPTS_DIR = "excerpts"
LOG_NAME = "locator_log.jsonl"
CAPTION_PASSAGE = 0


def excerpt_relpath(opinion_file: str) -> str:
    """Workdir-relative excerpt path for an opinion file."""
    return f"jobs/{EXCERPTS_DIR}/{Path(opinion_file).stem}.txt"


@dataclass
class ExcerptStats:
    """Statistics from write_locator_excerpts."""
    opinions: int = 0
    claims: int = 0
    already: int = 0      # opinions whose excerpt + log rows already exist
    skipped: int = 0      # opinions with no readable text (PDF / empty)
    input_tokens: int = 0
    chars_full: int = 0
    chars_excerpt: int = 0


def load_locator_log(workdir: Path) -> dict[str, dict]:
    """claim_id -> locator log row (last write wins)."""
    path = Path(workdir) / "jobs" / LOG_NAME
    rows: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                rows[row["claim_id"]] = row
    return rows


def write_locator_excerpts(workdir: Path,
                           ask: js.AskFn | None = None) -> ExcerptStats:
    """Write one excerpt file per opinion cited by an assessable claim.

    Idempotent per opinion: skipped when its excerpt file exists and every
    citing claim is in the log. A Jev failure raises -- a silently thinner
    excerpt would read downstream as "the opinion does not address this".
    """
    from .proposition_pipeline import _assessable

    workdir = Path(workdir)
    with open(workdir / "claims.csv", newline="", encoding="utf-8") as f:
        claims = [c for c in csv.DictReader(f) if _assessable(c)]
    by_opinion: dict[str, list[dict]] = {}
    for c in claims:
        by_opinion.setdefault(c["opinion_file"], []).append(c)

    logged = load_locator_log(workdir)
    stats = ExcerptStats()
    log_path = workdir / "jobs" / LOG_NAME
    (workdir / "jobs" / EXCERPTS_DIR).mkdir(parents=True, exist_ok=True)

    for opinion, group in sorted(by_opinion.items()):
        out_path = workdir / excerpt_relpath(opinion)
        if out_path.exists() and all(c["claim_id"] in logged for c in group):
            stats.already += 1
            continue
        src = workdir / opinion
        raw = (src.read_text(encoding="utf-8", errors="ignore")
               if src.is_file() and src.suffix.lower() != ".pdf" else "")
        if not js._clean(raw):
            stats.skipped += 1
            continue
        if ask is None:
            ask = js._default_ask()

        passages = js.split_passages(raw)
        union: set[int] = {CAPTION_PASSAGE}
        rows = []
        for c in group:
            prop = c.get("cited_for") or c.get("proposition", "")
            loc = js.locate(prop, passages, ask)
            top = loc["ranked"][:js.TOP_K]
            kept = js.keep_indices(len(passages), loc["ranked"])
            union |= kept
            stats.input_tokens += loc["input_tokens"]
            rows.append({
                "claim_id": c["claim_id"], "opinion_file": opinion,
                "model": js.MODEL, "n_passages": len(passages),
                "top_passages": top,
                "top_probs": [round(loc["probs"].get(js._pid(i), 0.0), 4)
                              for i in top],
                "exists": loc["exists"], "kept": sorted(kept),
                "input_tokens": loc["input_tokens"]})

        text = js.join_passages(passages, union, sep="\n\n")
        out_path.write_text(text + "\n", encoding="utf-8")
        full = sum(map(len, passages))
        with open(log_path, "a", encoding="utf-8") as log:
            for row in rows:
                row.update(excerpt_file=excerpt_relpath(opinion),
                           union=sorted(union), chars_full=full,
                           chars_excerpt=len(text))
                log.write(json.dumps(row) + "\n")
        stats.opinions += 1
        stats.claims += len(group)
        stats.chars_full += full
        stats.chars_excerpt += len(text)
    return stats
