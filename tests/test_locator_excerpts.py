"""Locator excerpts (assess-v3 experiment): one excerpt file per opinion, the
union of each citing claim's located passages; run_assess points v3 jobs at it."""
import csv
import json
from pathlib import Path

import pytest

from citation_verifier import jev_shadow as js
from citation_verifier import locator_excerpts as le
from citation_verifier import proposition_pipeline as pp

# 12 passages of ~300 chars: long enough that split_passages keeps them apart.
PASSAGES = [f"Passage number {i} of the opinion. " + ("filler words " * 22)
            for i in range(12)]
OPINION = "".join(f"<p>{p}</p>" for p in PASSAGES)


def _workdir(tmp_path, rows):
    (tmp_path / "opinions").mkdir()
    (tmp_path / "opinions" / "a.html").write_text(OPINION, encoding="utf-8")
    (tmp_path / "opinions" / "b.pdf").write_bytes(b"%PDF-1.4")
    fields = ["claim_id", "cited_case", "proposition", "brief_sentence",
              "cl_status", "opinion_file", "quote_check_worst"]
    with open(tmp_path / "claims.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, restval="")
        w.writeheader()
        w.writerows(rows)
    return tmp_path


def _claim(cid, prop, opinion="opinions/a.html", **kw):
    return {"claim_id": cid, "cited_case": "A v. B", "proposition": prop,
            "cl_status": "VERIFIED", "opinion_file": opinion,
            "quote_check_worst": "NO_QUOTES", **kw}


class FakeLocator:
    """Puts all the probability on the passage named in the proposition
    ("... wants 5" -> P005)."""

    def __init__(self):
        self.calls = 0

    def __call__(self, state, questions):
        self.calls += 1
        want = int(questions["where"]["instructions"].rsplit("wants ", 1)[1]
                   .split('"')[0])
        ids = list(questions["where"]["criteria"])
        probs = {p: (0.9 if p == js._pid(want) else 0.0) for p in ids}
        return {"answers": {"where": {"probabilities": probs},
                            "exists": {"noul": 0.25}},
                "input_tokens": 100}


def test_union_of_claims_in_document_order_with_gap_markers(tmp_path):
    wd = _workdir(tmp_path, [_claim("c-01", "wants 5"),
                             _claim("c-02", "wants 9")])
    stats = le.write_locator_excerpts(wd, ask=FakeLocator())
    assert (stats.opinions, stats.claims) == (1, 2)

    text = (wd / "jobs" / "excerpts" / "a.txt").read_text(encoding="utf-8")
    blocks = text.strip().split("\n\n")
    nums = [b.split()[2] if b != "[...]" else b for b in blocks]
    # caption (0), then each claim's top passage with one neighbour each side
    # (the fake's zero-probability ties fill the rest of the top 5 from the
    # start of the document: 0-3, so 0..6 is contiguous).
    assert nums == ["0", "1", "2", "3", "4", "5", "6", "[...]",
                    "8", "9", "10"]
    assert stats.chars_excerpt < stats.chars_full


def test_log_has_one_row_per_claim_with_exists_and_passages(tmp_path):
    wd = _workdir(tmp_path, [_claim("c-01", "wants 5"),
                             _claim("c-02", "wants 9")])
    le.write_locator_excerpts(wd, ask=FakeLocator())
    log = le.load_locator_log(wd)
    assert set(log) == {"c-01", "c-02"}
    assert log["c-02"]["top_passages"][0] == 9
    assert log["c-02"]["exists"] == 0.25
    assert {8, 9, 10} <= set(log["c-02"]["kept"])
    assert log["c-01"]["excerpt_file"] == "jobs/excerpts/a.txt"
    assert le.CAPTION_PASSAGE in log["c-01"]["union"]


def test_idempotent_and_skips_unreadable_opinions(tmp_path):
    wd = _workdir(tmp_path, [_claim("c-01", "wants 5"),
                             _claim("c-02", "wants 2", "opinions/b.pdf")])
    ask = FakeLocator()
    first = le.write_locator_excerpts(wd, ask=ask)
    assert (first.opinions, first.skipped, ask.calls) == (1, 1, 1)
    again = le.write_locator_excerpts(wd, ask=ask)
    assert (again.opinions, again.already, ask.calls) == (0, 1, 1)


def test_locator_failure_raises_rather_than_thinning_the_excerpt(tmp_path):
    wd = _workdir(tmp_path, [_claim("c-01", "wants 5")])

    def boom(state, questions):
        raise ConnectionError("boom")

    with pytest.raises(ConnectionError):
        le.write_locator_excerpts(wd, ask=boom)
    assert not (wd / "jobs" / "excerpts" / "a.txt").exists()


class Capture:
    def __init__(self):
        self.jobs = []

    def run(self, jobs):
        self.jobs = jobs
        return iter(())


def test_v3_jobs_read_the_excerpt_and_v2_jobs_still_read_the_opinion(tmp_path):
    wd = _workdir(tmp_path, [_claim("c-01", "wants 5")])
    le.write_locator_excerpts(wd, ask=FakeLocator())

    v3, v2 = Capture(), Capture()
    pp.run_assess(wd, executor=v3, prompt_version="assess-v3")
    pp.run_assess(wd, executor=v2, prompt_version="assess-v2")

    assert v3.jobs[0].files == ["jobs/excerpts/a.txt"]
    assert str(Path("jobs/excerpts/a.txt")) in v3.jobs[0].prompt
    assert "EXCERPTS" in v3.jobs[0].prompt
    assert v2.jobs[0].files == ["opinions/a.html"]
    assert "EXCERPTS" not in v2.jobs[0].prompt


def test_v3_without_excerpts_fails_loudly(tmp_path):
    wd = _workdir(tmp_path, [_claim("c-01", "wants 5")])
    with pytest.raises(FileNotFoundError, match="write_locator_excerpts"):
        pp.run_assess(wd, executor=Capture(), prompt_version="assess-v3")


def test_v3_prompt_differs_from_v2_only_in_framing():
    """Every criterion, badge label, and output field is v2's: the only
    removed v2 lines are the three that say "opinion" where v3 must say
    "excerpts" (v3 also ADDS the excerpt-framing and excerpt-rule text)."""
    v2 = pp.load_prompt_template("assess-v2").splitlines()
    v3 = pp.load_prompt_template("assess-v3").splitlines()
    dropped = [ln for ln in v2 if ln not in v3]
    assert len(dropped) == 3
    assert all("opinion" in ln for ln in dropped)
    for keep in ('- "supported"', '- "partial"', '- "unsupported"',
                 '- "unverifiable"', "**badge_label**", '{"verdicts":'):
        assert [ln for ln in v2 if ln.startswith(keep)] == \
               [ln for ln in v3 if ln.startswith(keep)]


def test_frozen_corpora_have_an_excerpt_for_every_assessable_claim():
    root = Path(__file__).parent / "data" / "assessment_corpora"
    for name in ("withers", "payne", "wainwright"):
        log = le.load_locator_log(root / name)
        with open(root / name / "claims.csv", newline="",
                  encoding="utf-8") as f:
            for c in csv.DictReader(f):
                if pp._assessable(c):
                    assert c["claim_id"] in log, c["claim_id"]
                    assert (root / name / le.excerpt_relpath(
                        c["opinion_file"])).is_file()
