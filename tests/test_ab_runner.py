"""Offline tests for the re-pointed A/B harness (design SS9).

The harness's executor seam is exercised with RecordedExecutor over the
frozen corpora -- no LLM, no network. Frozen corpora are read-only here
(live mode copies them to tmp_path first).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import ab_test_runner as ab  # noqa: E402


class TestReplayMode:
    def test_scores_frozen_cassettes(self, capsys):
        scores = ab.run_ab_config("baseline", {}, replay=True)
        assert (scores["payne"].correct, scores["payne"].total) == (24, 27)
        assert (scores["wainwright"].correct,
                scores["wainwright"].total) == (33, 34)
        assert "baseline/payne" in capsys.readouterr().out


class TestLiveModeOfflineSeam:
    def test_injected_executor_runs_assess_and_scores(self, tmp_path):
        from citation_verifier.executor import RecordedExecutor

        def factory(config, wd, phase):
            # replay the ORIGINAL frozen cassette as if it were live
            return RecordedExecutor(
                ab.CORPORA / wd.name / "jobs" / "assess_results.jsonl")

        scores = ab.run_ab_config(
            "test", {"model": "opus"}, corpora=("payne",),
            run_root=tmp_path, executor_factory=factory)
        assert (scores["payne"].correct, scores["payne"].total) == (24, 27)
        # the copy got a fresh cassette written through run_assess
        copy_cassette = tmp_path / "payne" / "jobs" / "assess_results.jsonl"
        assert copy_cassette.exists()
        # frozen corpus untouched
        assert (ab.CORPORA / "payne" / "jobs" /
                "assess_results.jsonl").exists()

    def test_missing_verdicts_score_the_rest_and_report_drop(
            self, tmp_path, capsys):
        """TODO Priority-1: a live run with transient job failures (gaps
        in the results JSONL) must score the completed claims and report
        the drop -- not die on RecordedVerdictMiss (the 2026-06-13
        sonnet-v2 arm lost two corpora that way)."""
        import json as json_mod

        from citation_verifier.executor import (RecordedExecutor,
                                                load_verdicts_jsonl)

        src_cassette = (ab.CORPORA / "payne" / "jobs" /
                        "assess_results.jsonl")
        verdicts = [v for v in load_verdicts_jsonl(src_cassette)
                    if v.prompt_version == "assess-v1"]
        dropped = {verdicts[0].claim_id, verdicts[1].claim_id}
        partial = tmp_path / "partial.jsonl"
        with open(partial, "w", encoding="utf-8") as f:
            for v in verdicts:
                if v.claim_id in dropped:
                    continue  # simulate two transient job failures
                from citation_verifier.executor import verdict_to_json
                f.write(json_mod.dumps(verdict_to_json(v)) + "\n")

        def factory(config, wd, phase):
            return RecordedExecutor(partial, missing="skip")

        run_root = tmp_path / "run"
        run_root.mkdir()
        scores = ab.run_ab_config(
            "gap-test", {"model": "opus"}, corpora=("payne",),
            run_root=run_root, executor_factory=factory)
        out = capsys.readouterr().out
        assert "dropped from scoring" in out
        # scored rows exclude only the dropped claims
        scored_ids = {r["claim_id"] for r in scores["payne"].rows}
        assert dropped.isdisjoint(scored_ids)
        assert scores["payne"].total == 27 - len(dropped)

    def test_api_transport_config_builds_messages_executor(self, tmp_path):
        from citation_verifier.executor import MessagesAPIExecutor
        ex = ab.make_executor({"executor": "api",
                               "model": "claude-opus-4-8"}, tmp_path)
        assert isinstance(ex, MessagesAPIExecutor)
        assert ex.model == "claude-opus-4-8"
        assert ex.batch is False

    def test_live_requires_run_root(self):
        with pytest.raises(ValueError, match="run_root"):
            ab.run_ab_config("x", {}, corpora=("payne",))


class TestSaveAndCompare:
    def test_roundtrip(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(ab, "RESULTS_DIR", tmp_path)
        scores = ab.run_ab_config("baseline", {}, corpora=("payne",),
                                  replay=True)
        out = ab.save_results("baseline", scores)
        assert out.exists()
        capsys.readouterr()
        ab.compare_results(str(out), str(out))
        printed = capsys.readouterr().out
        assert "24/27 correct" in printed
        assert "Disagreements: 0" in printed


class TestWithersCorpusViaRunner:
    def test_replay_scores_exhibit_scale(self, capsys):
        scores = ab.run_ab_config("baseline", {}, corpora=("withers",),
                                  replay=True)
        s = scores["withers"]
        assert (s.yellows_caught, s.yellows_total) == (14, 19)
        assert (s.reds_caught, s.reds_total) == (3, 3)
        assert "yellows caught 14/19" in capsys.readouterr().out


class TestDryRun:
    def test_prints_job_counts(self, capsys):
        ab.dry_run_config("opus-baseline", {"model": "opus"},
                          corpora=("payne",))
        out = capsys.readouterr().out
        assert "assess jobs" in out
