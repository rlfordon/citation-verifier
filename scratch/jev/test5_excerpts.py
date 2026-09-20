"""Test 5, step 1 -- write the locator excerpt files into the frozen corpora.

    venv/Scripts/python.exe scratch/jev/test5_excerpts.py

Uses the production locator (citation_verifier.locator_excerpts, which
imports jev_shadow's) through this folder's response cache, so the locator
answers are the ones test 3 measured (96% top-5) and reruns are free.
Writes tests/data/assessment_corpora/<corpus>/jobs/excerpts/*.txt and
jobs/locator_log.jsonl. Those are the fixed inputs of the assess-v3 run.
"""
from __future__ import annotations

import sys

from jev_common import CORPORA, CORPORA_ROOT, REPO, USD_PER_TOKEN, ask, save_cache

sys.path.insert(0, str(REPO / "src"))
from citation_verifier.locator_excerpts import write_locator_excerpts  # noqa: E402

live = 0


def cached_ask(state, questions) -> dict:
    global live
    r = ask(state, questions)
    live += not r.cached
    return {"answers": r.answers, "input_tokens": r.input_tokens}


def main() -> None:
    for corpus in CORPORA:
        s = write_locator_excerpts(CORPORA_ROOT / corpus, ask=cached_ask)
        save_cache()
        share = 100 * s.chars_excerpt / s.chars_full if s.chars_full else 0
        print(f"{corpus}: {s.opinions} opinions / {s.claims} claims written, "
              f"{s.already} already done, {s.skipped} skipped; excerpts are "
              f"{share:.0f}% of opinion text; Jev tokens {s.input_tokens:,} "
              f"(${s.input_tokens * USD_PER_TOKEN:.4f} if uncached)")
    print(f"live Jev calls this run: {live}")


if __name__ == "__main__":
    main()
