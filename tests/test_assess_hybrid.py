import csv
import json

import pytest

from citation_verifier import proposition_pipeline as pp

# render_assess_v2_claim_block only hard-requires claim_id; the rest is
# .get() with defaults. render_assess_v2_prompt substitutes the opinion
# PATH (does not read the file), so no real opinion files are needed.
CLAIM_FIELDS = ["claim_id", "triage_track", "opinion_file", "cl_status",
                "cited_case", "proposition", "quote_check_worst"]


def _claim(**over):
    base = {k: "" for k in CLAIM_FIELDS}
    base.update(over)
    return base


def test_build_v2_jobs_packed_groups_by_opinion(tmp_path):
    claims = [
        _claim(claim_id="c1", opinion_file="opinions/a.txt"),
        _claim(claim_id="c2", opinion_file="opinions/a.txt"),
        _claim(claim_id="c3", opinion_file="opinions/b.txt"),
    ]
    jobs = pp._build_v2_jobs(claims, tmp_path, "assess-v2", packed=True)
    assert len(jobs) == 2
    by_ids = sorted(sorted(j.claim_ids) for j in jobs)
    assert by_ids == [["c1", "c2"], ["c3"]]


def test_build_v2_jobs_unpacked_one_per_claim(tmp_path):
    claims = [
        _claim(claim_id="c1", opinion_file="opinions/a.txt"),
        _claim(claim_id="c2", opinion_file="opinions/a.txt"),
    ]
    jobs = pp._build_v2_jobs(claims, tmp_path, "assess-v2", packed=False)
    assert len(jobs) == 2
    assert all(len(j.claim_ids) == 1 for j in jobs)
    assert {j.claim_ids[0] for j in jobs} == {"c1", "c2"}


from citation_verifier.executor import Verdict, append_verdict_jsonl


def _write_claims(workdir, rows):
    workdir.mkdir(parents=True, exist_ok=True)
    with (workdir / "claims.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CLAIM_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(_claim(**r))


class FakeExecutor:
    """Yields a Verdict per claim_id from support_by_id; claim_ids in
    fail_ids yield no verdict (recorded in .failures), mirroring a real
    executor's non-auth per-job failure handling."""

    def __init__(self, support_by_id, fail_ids=(), cost=0.02):
        self.support_by_id = support_by_id
        self.fail_ids = set(fail_ids)
        self.cost = cost
        self.failures = []
        self.model = "fake"
        self.seen = []

    def run(self, jobs):
        out = []
        for job in jobs:
            for cid in job.claim_ids:
                self.seen.append(cid)
                if cid in self.fail_ids:
                    self.failures.append((job.job_id, "sim fail"))
                    continue
                out.append(Verdict(
                    claim_id=cid,
                    fields={"support": self.support_by_id[cid],
                            "badge_label": "", "brief_block": "",
                            "opinion_block": "", "finding_analysis": ""},
                    model="fake-model", prompt_version=job.prompt_version,
                    cost_usd=self.cost))
        return out


def _persisted_ids(workdir):
    path = workdir / "jobs" / "assess_results.jsonl"
    if not path.exists():
        return []
    return [json.loads(ln)["claim_id"]
            for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_supported_fast_kept_and_full_routed(tmp_path):
    wd = tmp_path / "wd"
    _write_claims(wd, [
        dict(claim_id="f1", triage_track="fast",
             opinion_file="opinions/a.txt", cl_status="VERIFIED"),
        dict(claim_id="u1", triage_track="full",
             opinion_file="opinions/b.txt", cl_status="VERIFIED"),
    ])
    fast = FakeExecutor({"f1": "supported"})
    full = FakeExecutor({"u1": "unsupported"})
    stats = pp.run_assess_hybrid(wd, fast_executor=fast, full_executor=full,
                                 prompt_version="assess-v2")
    assert fast.seen == ["f1"]
    assert full.seen == ["u1"]
    assert stats.fast_kept == 1 and stats.escalated == 0
    assert sorted(_persisted_ids(wd)) == ["f1", "u1"]


def test_non_supported_fast_escalates_not_persisted(tmp_path):
    wd = tmp_path / "wd"
    _write_claims(wd, [dict(claim_id="f1", triage_track="fast",
                            opinion_file="opinions/a.txt", cl_status="VERIFIED")])
    fast = FakeExecutor({"f1": "partial"}, cost=0.05)
    full = FakeExecutor({"f1": "partial"})
    stats = pp.run_assess_hybrid(wd, fast_executor=fast, full_executor=full,
                                 prompt_version="assess-v2")
    assert "f1" in full.seen  # escalated to Opus
    assert stats.escalated == 1 and stats.fast_kept == 0
    assert abs(stats.escalated_cost_usd - 0.05) < 1e-9
    assert _persisted_ids(wd) == ["f1"]  # only the Opus verdict


def test_fast_failure_escalates(tmp_path):
    wd = tmp_path / "wd"
    _write_claims(wd, [dict(claim_id="f1", triage_track="fast",
                            opinion_file="opinions/a.txt", cl_status="VERIFIED")])
    fast = FakeExecutor({}, fail_ids=["f1"])
    full = FakeExecutor({"f1": "supported"})
    stats = pp.run_assess_hybrid(wd, fast_executor=fast, full_executor=full,
                                 prompt_version="assess-v2")
    assert "f1" in full.seen
    assert stats.escalated == 1
    assert stats.escalated_cost_usd == 0.0  # no verdict -> no cost captured
    assert _persisted_ids(wd) == ["f1"]


def test_legacy_missing_triage_all_full(tmp_path):
    wd = tmp_path / "wd"
    _write_claims(wd, [
        dict(claim_id="x1", triage_track="", opinion_file="opinions/a.txt",
             cl_status="VERIFIED"),
        dict(claim_id="x2", triage_track="", opinion_file="opinions/a.txt",
             cl_status="VERIFIED"),
    ])
    fast = FakeExecutor({})
    full = FakeExecutor({"x1": "supported", "x2": "unsupported"})
    stats = pp.run_assess_hybrid(wd, fast_executor=fast, full_executor=full,
                                 prompt_version="assess-v2")
    assert fast.seen == []
    assert sorted(full.seen) == ["x1", "x2"]
    assert stats.fast_kept == 0 and stats.escalated == 0


def test_resume_skips_persisted(tmp_path):
    wd = tmp_path / "wd"
    _write_claims(wd, [dict(claim_id="f1", triage_track="fast",
                            opinion_file="opinions/a.txt", cl_status="VERIFIED")])
    append_verdict_jsonl(wd / "jobs" / "assess_results.jsonl", Verdict(
        claim_id="f1", fields={"support": "supported"},
        model="prior", prompt_version="assess-v2"))
    fast = FakeExecutor({"f1": "supported"})
    full = FakeExecutor({})
    stats = pp.run_assess_hybrid(wd, fast_executor=fast, full_executor=full,
                                 prompt_version="assess-v2")
    assert fast.seen == [] and full.seen == []
    assert stats.done == 1 and stats.pending == 0


def test_v1_prompt_raises(tmp_path):
    wd = tmp_path / "wd"
    _write_claims(wd, [dict(claim_id="f1", triage_track="fast",
                            opinion_file="opinions/a.txt", cl_status="VERIFIED")])
    with pytest.raises(ValueError):
        pp.run_assess_hybrid(wd, fast_executor=FakeExecutor({}),
                             full_executor=FakeExecutor({}),
                             prompt_version="assess-v1")
