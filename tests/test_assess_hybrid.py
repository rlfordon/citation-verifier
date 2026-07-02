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
