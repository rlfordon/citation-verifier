"""Jev shadow logging: writes one JSONL, touches nothing else, never fails a run."""
import csv
import json

import pytest

from citation_verifier import jev_shadow as js

OPINION = (
    "<p>The court considered whether summary judgment was proper on this "
    "record, and reviewed the standard that governs such motions in detail "
    "before turning to the merits of the appeal that was before it, which "
    "arose from a contract dispute between two commercial parties.</p>"
    "<p>We hold that summary judgment will not lie if the dispute about a "
    "material fact is genuine, that is, if the evidence is such that a "
    "reasonable jury could return a verdict for the nonmoving party here.</p>"
)


def _workdir(tmp_path, rows):
    (tmp_path / "opinions").mkdir()
    (tmp_path / "opinions" / "a.html").write_text(OPINION, encoding="utf-8")
    (tmp_path / "opinions" / "b.pdf").write_bytes(b"%PDF-1.4")
    fields = ["claim_id", "proposition", "brief_sentence", "cl_status",
              "opinion_file", "quote_check_worst", "quote_floor",
              "crosscheck_flags", "support"]
    with open(tmp_path / "claims.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, restval="")
        w.writeheader()
        w.writerows(rows)
    return tmp_path


def _claim(cid, **kw):
    return {"claim_id": cid, "proposition": "Summary judgment is improper "
            "where a material fact is genuinely disputed.",
            "brief_sentence": "See the cited case.", "cl_status": "VERIFIED",
            "opinion_file": "opinions/a.html",
            "quote_check_worst": "NO_QUOTES", **kw}


class FakeAsk:
    """Answers every question by type; records what it was asked."""

    def __init__(self, fail_on=()):
        self.calls, self.fail_on = [], set(fail_on)

    def __call__(self, state, questions):
        self.calls.append((state, questions))
        if isinstance(state, dict) and state["proposition"] in self.fail_on:
            raise ConnectionError("boom")
        answers = {}
        for qid, q in questions.items():
            if q["type"] == "noul":
                answers[qid] = {"type": "noul", "noul": 0.9}
            else:
                opts = list(q["criteria"])
                answers[qid] = {"type": "choice", "choice": opts[0],
                                "confidence": 0.8, "probabilities": {
                                    o: (0.8 if i == 0 else 0.2 / (len(opts) - 1))
                                    for i, o in enumerate(opts)}}
        return {"answers": answers, "input_tokens": 100}


def _rows(workdir):
    p = workdir / "jobs" / js.RESULTS_NAME
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]


def test_logs_answers_and_leaves_claims_csv_untouched(tmp_path):
    wd = _workdir(tmp_path, [_claim("c-01"), _claim("c-02")])
    before = (wd / "claims.csv").read_bytes()
    stats = js.run_jev_shadow(wd, ask=FakeAsk())
    assert (stats.logged, stats.errors, stats.skipped) == (2, 0, 0)
    assert stats.input_tokens == 600  # locator + two rubric framings, x2
    assert (wd / "claims.csv").read_bytes() == before
    assert sorted(p.name for p in (wd / "jobs").iterdir()) == [js.RESULTS_NAME]
    row = _rows(wd)[0]
    assert row["claim_id"] == "c-01"
    assert row["rubric_version"] == js.RUBRIC_VERSION
    assert row["model"] == js.MODEL
    assert row["eligible"] is True
    assert row["gate_v0"] == pytest.approx(0.8)   # min(0.8, 0.9, 0.9)
    assert row["gate_v1"] == pytest.approx(0.8)   # mean of two `fully` probs
    assert set(row["answers"]) == set(js.RUBRIC_FULL_STATE)
    assert set(row["answers_lean"]) == set(js.RUBRIC_LEAN_STATE)


def test_three_requests_per_claim_with_the_documented_state_framings(tmp_path):
    wd = _workdir(tmp_path, [_claim("c-01")])
    ask = FakeAsk()
    js.run_jev_shadow(wd, ask=ask)
    (loc_state, loc_q), (full_state, _), (lean_state, _) = ask.calls
    assert loc_state.startswith("P000| ") and "P001| " in loc_state
    assert set(loc_q) == {"where", "exists"}
    assert set(full_state) == {"proposition", "brief_sentence", "opinion"}
    assert set(lean_state) == {"proposition", "opinion"}
    assert "reasonable jury" in full_state["opinion"]


def test_resume_skips_logged_claims(tmp_path):
    wd = _workdir(tmp_path, [_claim("c-01"), _claim("c-02")])
    js.run_jev_shadow(wd, ask=FakeAsk())
    ask = FakeAsk()
    stats = js.run_jev_shadow(wd, ask=ask)
    assert (stats.logged, stats.already) == (0, 2)
    assert ask.calls == []
    assert len(_rows(wd)) == 2


def test_nothing_to_do_never_needs_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    wd = _workdir(tmp_path, [_claim("c-01", opinion_file="")])
    stats = js.run_jev_shadow(wd)  # no ask injected, no key: must not raise
    assert stats.skipped == 1 and stats.logged == 0


def test_skips_claims_without_readable_opinion_text(tmp_path):
    wd = _workdir(tmp_path, [
        _claim("c-01", opinion_file=""),
        _claim("c-02", opinion_file="opinions/missing.html"),
        _claim("c-03", opinion_file="opinions/b.pdf"),
        _claim("c-04")])
    stats = js.run_jev_shadow(wd, ask=FakeAsk())
    assert (stats.logged, stats.skipped) == (1, 3)
    assert [r["claim_id"] for r in _rows(wd)] == ["c-04"]


def test_a_failing_claim_is_recorded_and_retried_next_run(tmp_path):
    wd = _workdir(tmp_path, [_claim("c-01", proposition="bad one"),
                             _claim("c-02")])
    stats = js.run_jev_shadow(wd, ask=FakeAsk(fail_on={"bad one"}))
    assert (stats.logged, stats.errors) == (1, 1)
    assert stats.error_claims == ["c-01"]
    assert "ConnectionError" in _rows(wd)[0]["error"]
    # error rows do not count as logged: the next run retries them
    stats = js.run_jev_shadow(wd, ask=FakeAsk())
    assert (stats.logged, stats.already) == (1, 1)


@pytest.mark.parametrize("claim,expected", [
    ({"cl_status": "VERIFIED", "quote_check_worst": "NO_QUOTES"}, True),
    ({"cl_status": "VERIFIED", "quote_check_worst": "VERBATIM",
      "crosscheck_flags": "[]"}, True),
    ({"cl_status": "VERIFIED_PARTIAL", "quote_check_worst": "NO_QUOTES"}, False),
    ({"cl_status": "VERIFIED", "quote_check_worst": "CLOSE"}, False),
    ({"cl_status": "VERIFIED", "quote_check_worst": "VERBATIM",
      "quote_floor": "Yellow"}, False),
    ({"cl_status": "VERIFIED", "quote_check_worst": "NO_QUOTES",
      "crosscheck_flags": '[{"kind": "pincite"}]'}, False),
])
def test_gate_eligibility_is_deterministic_signals_only(claim, expected):
    assert js.gate_eligible(claim) is expected


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("ON", True),
    ("", False), ("0", False), ("no", False)])
def test_enabled_is_opt_in(monkeypatch, value, expected):
    monkeypatch.setenv("JEV_SHADOW", value)
    assert js.enabled() is expected


def test_split_passages_fits_one_choice_question():
    raw = "".join(f"<p>{'word ' * 80}{i}.</p>" for i in range(400))
    passages = js.split_passages(raw)
    assert len(passages) <= 250
    assert "<p>" not in passages[0]
