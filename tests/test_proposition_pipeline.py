"""Tests for proposition_pipeline (pipeline redesign SS10 step 2).

Covers what is NEW relative to the old brief_pipeline: the
matched_case_name accessor (SS11 bug 1 source fix), slug-token opinion
linkage, verify/merge verbs, and (0.6.0) the removal of the deprecated
brief_pipeline alias. Legacy behavior stays covered by
test_brief_pipeline.py, which now imports proposition_pipeline directly.
"""
import csv
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from citation_verifier.models import (
    FinalIds,
    ResolutionPathEntry,
    StageName,
    StageVerdict,
    Status,
    VerificationResult,
)


def _entry(stage, summary, verdict=StageVerdict.resolved):
    return ResolutionPathEntry(
        stage=stage, query={}, raw_response_summary=summary,
        verdict=verdict, confidence=1.0, notes="", elapsed_ms=0,
    )


def _result(path_entries):
    return VerificationResult(
        citation_as_written="Test v. Case, 1 U.S. 1 (1800)",
        parsed_citation=None,
        status=Status.VERIFIED,
        final_ids=FinalIds(
            cluster_id=None, opinion_id=None, docket_id=None,
            recap_document_id=None, absolute_url=None, text_source=None,
        ),
        resolution_path=path_entries,
        warnings=[],
        gates_failed=[],
        timing={},
        cache_hit=False,
    )


class TestMatchedCaseNameAccessor:
    def test_citation_lookup_key(self):
        r = _result([_entry(StageName.citation_lookup,
                            {"matched_case_name": "Nix v. Whiteside"})])
        assert r.matched_case_name == "Nix v. Whiteside"

    def test_search_stage_key(self):
        r = _result([_entry(StageName.opinion_search,
                            {"best_case_name": "Donovan v. Carls Drug Co."})])
        assert r.matched_case_name == "Donovan v. Carls Drug Co."

    def test_caption_investigation_key_wins_as_latest(self):
        r = _result([
            _entry(StageName.citation_lookup,
                   {"matched_case_name": "Brief's Name"}),
            _entry(StageName.caption_investigation,
                   {"cl_case_name": "CL's Actual Caption"}),
        ])
        assert r.matched_case_name == "CL's Actual Caption"

    def test_sibling_swap_case_name_key(self):
        r = _result([_entry(StageName.citation_lookup,
                            {"matched_case_name": "Original",
                             "case_name": "Swapped Sibling"})])
        assert r.matched_case_name == "Swapped Sibling"

    def test_walks_back_past_summaryless_entries(self):
        r = _result([
            _entry(StageName.citation_lookup,
                   {"matched_case_name": "Found Here"}),
            _entry(StageName.opinion_search, {"candidate_count": 0},
                   verdict=StageVerdict.no_match),
        ])
        assert r.matched_case_name == "Found Here"

    def test_empty_path_returns_empty(self):
        assert _result([]).matched_case_name == ""


class TestBriefPipelineAliasRemoved:
    def test_alias_module_gone(self):
        """0.6.0: the deprecated brief_pipeline sys.modules alias was
        removed after one minor version (introduced 0.5). Importing it
        now fails; callers use proposition_pipeline directly."""
        import importlib
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("citation_verifier.brief_pipeline")


class TestMatchedNameInCsv:
    def test_write_verification_csv_uses_accessor(self, tmp_path):
        """SS11 bug 1 regression: batch-path results carry the caption under
        matched_case_name, which the old writer (reading only case_name)
        dropped -- matched_name came out blank in verification_results.csv."""
        from citation_verifier.proposition_pipeline import (
            _write_verification_csv)
        r = _result([_entry(StageName.citation_lookup,
                            {"matched_case_name": "Nix v. Whiteside",
                             "clusters_returned": 1})])
        _write_verification_csv(tmp_path, ["Nix v. Whiteside, 475 U.S. 157"],
                                [r])
        rows = list(csv.DictReader(
            (tmp_path / "verification_results.csv").open(encoding="utf-8")))
        assert rows[0]["matched_name"] == "Nix v. Whiteside"


def _mk_merge_workdir(tmp_path, opinion_name, vr_row):
    wd = tmp_path / "wd"
    (wd / "opinions").mkdir(parents=True)
    (wd / "opinions" / opinion_name).write_text("text", encoding="utf-8")
    with (wd / "claims.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "claim_id", "page", "proposition", "cited_for", "cited_case",
            "quoted_text", "brief_sentence"])
        w.writeheader()
        w.writerow({"claim_id": "t-01", "page": "1", "proposition": "P",
                    "cited_for": "", "cited_case": vr_row["citation"],
                    "quoted_text": "[]", "brief_sentence": ""})
    with (wd / "verification_results.csv").open("w", newline="",
                                                encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "citation", "status", "confidence", "cl_url", "matched_name",
            "diagnostics_cat", "diagnostics_msg", "syllabus"])
        w.writeheader()
        w.writerow(vr_row)
    return wd


_MIDWEST_VR = {
    "citation": "Midwest Employers Cas. Co. v. Williams, "
                "161 F.3d 877 (5th Cir. 1998)",
    "status": "VERIFIED", "confidence": "1.00", "cl_url": "",
    "matched_name": "", "diagnostics_cat": "", "diagnostics_msg": "",
    "syllabus": "",
}


class TestSlugTokenLinkage:
    # The motivating fixture from the 2026-06-11 measurement run: CL's
    # caption is far longer than the cited name, so name-containment failed.
    OPINION = ("MIDWEST_EMPLOYERS_CASUALTY_CO_Plaintiff-Appellant-Appellee"
               "_v_Jo_Ann_WILLIAMS_Defendant-Appellee-Appellant.html")

    def test_links_via_cl_url_slug_when_matched_name_blank(self, tmp_path):
        from citation_verifier.proposition_pipeline import merge_claims
        vr = dict(_MIDWEST_VR)
        vr["cl_url"] = ("https://www.courtlistener.com/opinion/758697/"
                        "midwest-employers-casualty-co-v-jo-ann-williams/")
        wd = _mk_merge_workdir(tmp_path, self.OPINION, vr)
        stats = merge_claims(wd)
        claims = list(csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8")))
        assert claims[0]["opinion_file"] == f"opinions/{self.OPINION}"
        assert stats.opinion_count == 1

    def test_links_via_matched_name_tokens(self, tmp_path):
        from citation_verifier.proposition_pipeline import merge_claims
        vr = dict(_MIDWEST_VR)
        vr["matched_name"] = "Midwest Employers Casualty Co. v. Williams"
        wd = _mk_merge_workdir(tmp_path, self.OPINION, vr)
        merge_claims(wd)
        claims = list(csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8")))
        assert claims[0]["opinion_file"] == f"opinions/{self.OPINION}"

    def test_no_link_below_threshold(self, tmp_path):
        from citation_verifier.proposition_pipeline import merge_claims
        wd = _mk_merge_workdir(tmp_path, "Completely_Unrelated_v_Case.html",
                               dict(_MIDWEST_VR))
        merge_claims(wd)
        claims = list(csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8")))
        assert claims[0]["opinion_file"] == ""

    def test_claim_id_and_cited_for_survive_merge(self, tmp_path):
        from citation_verifier.proposition_pipeline import merge_claims
        vr = dict(_MIDWEST_VR)
        vr["matched_name"] = "Midwest Employers Casualty Co. v. Williams"
        wd = _mk_merge_workdir(tmp_path, self.OPINION, vr)
        merge_claims(wd)
        claims = list(csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8")))
        assert claims[0]["claim_id"] == "t-01"
        assert "cited_for" in claims[0]


class TestMergeLinkageGate:
    """Review finding #1: a NOT_FOUND / unmatched citation must NOT borrow
    another located case's opinion via the bare cited-name source. Without
    the located gate, a hallucinated 'United States v. <fake>' links to a
    real 'United States v. <X>' opinion on {united, states} token overlap
    (Jaccard >= 0.25), gets assessed against the wrong opinion, and can
    surface as verified instead of 'unable to verify'."""

    def test_not_found_does_not_borrow_opinion(self, tmp_path):
        from citation_verifier.proposition_pipeline import merge_claims
        wd = _mk_merge_workdir(
            tmp_path, "United_States_v_Jackson.html",
            {"citation": "United States v. Fakename, 999 F.3d 1 "
                         "(9th Cir. 2099)",
             "status": "NOT_FOUND", "confidence": "0.00", "cl_url": "",
             "matched_name": "", "diagnostics_cat": "",
             "diagnostics_msg": "", "syllabus": ""})
        merge_claims(wd)
        claims = list(csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8")))
        assert claims[0]["opinion_file"] == ""  # would be borrowed pre-fix

    def test_unmatched_does_not_borrow_opinion(self, tmp_path):
        from citation_verifier.proposition_pipeline import merge_claims
        # No vr row at all -> empty status, empty url/matched_name.
        wd = tmp_path / "wd"
        (wd / "opinions").mkdir(parents=True)
        (wd / "opinions" / "State_v_Smith.html").write_text(
            "t", encoding="utf-8")
        with (wd / "claims.csv").open("w", newline="",
                                      encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["claim_id", "cited_case",
                                              "quoted_text"])
            w.writeheader()
            w.writerow({"claim_id": "t-01",
                        "cited_case": "State v. Smithfake, 1 X.3d 1",
                        "quoted_text": "[]"})
        (wd / "verification_results.csv").write_text(
            "citation,status,cl_url,matched_name,diagnostics_msg,syllabus\n",
            encoding="utf-8")
        merge_claims(wd)
        claims = list(csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8")))
        assert claims[0]["opinion_file"] == ""

    def test_located_possible_match_still_links(self, tmp_path):
        """Positive control: POSSIBLE_MATCH/LIKELY_REAL are located (CL
        returned a match) -> they keep their opinion link. The gate keys
        on (url or matched_name), not a VERIFIED-only allowlist."""
        from citation_verifier.proposition_pipeline import merge_claims
        opinion = ("MIDWEST_EMPLOYERS_CASUALTY_CO_v_Jo_Ann_WILLIAMS.html")
        wd = _mk_merge_workdir(tmp_path, opinion, {
            "citation": "Midwest Employers Cas. Co. v. Williams, "
                        "161 F.3d 877 (5th Cir. 1998)",
            "status": "POSSIBLE_MATCH", "confidence": "0.70", "cl_url": "",
            "matched_name": "Midwest Employers Casualty Co. v. Williams",
            "diagnostics_cat": "", "diagnostics_msg": "", "syllabus": ""})
        merge_claims(wd)
        claims = list(csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8")))
        assert claims[0]["opinion_file"] == f"opinions/{opinion}"


class TestMergeColumnPreservation:
    """Review finding #2: a standalone merge rerun must not drop columns
    written by later verbs (quote_floor, crosscheck_flags, triage_track,
    prescreen_hint, support, assessed_by). The contract is 'every verb
    independently runnable; resume = rerun the verb'."""

    def test_downstream_columns_survive_rerun(self, tmp_path):
        from citation_verifier.proposition_pipeline import merge_claims
        wd = tmp_path / "wd"
        (wd / "opinions").mkdir(parents=True)
        fields = ["claim_id", "cited_case", "quoted_text", "cl_status",
                  "quote_floor", "crosscheck_flags", "triage_track",
                  "prescreen_hint", "support", "assessed_by", "assessment"]
        with (wd / "claims.csv").open("w", newline="",
                                      encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerow({"claim_id": "t-01", "cited_case": "A v. B, 1 U.S. 1",
                        "quoted_text": "[]", "cl_status": "VERIFIED",
                        "quote_floor": "Yellow",
                        "crosscheck_flags": '{"court_mismatch": {}}',
                        "triage_track": "full", "prescreen_hint": "hint",
                        "support": "partial", "assessed_by": "opus/assess-v2",
                        "assessment": "Yellow"})
        (wd / "verification_results.csv").write_text(
            "citation,status,cl_url,matched_name,diagnostics_msg,syllabus\n"
            "\"A v. B, 1 U.S. 1\",VERIFIED,,A v. B,,\n", encoding="utf-8")
        merge_claims(wd)
        (row,) = list(csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8")))
        assert row["quote_floor"] == "Yellow"
        assert row["crosscheck_flags"] == '{"court_mismatch": {}}'
        assert row["triage_track"] == "full"
        assert row["prescreen_hint"] == "hint"
        assert row["support"] == "partial"
        assert row["assessed_by"] == "opus/assess-v2"
        assert row["assessment"] == "Yellow"  # not clobbered to ""


class TestWithersCorpusLinkage:
    """The committed frozen corpus IS the bug's regression data: its
    verification_results.csv has blank matched_name on batch rows, and its
    claims.csv carries the slug-workaround links the new merge must
    reproduce from scratch."""

    def test_reproduces_frozen_corpus_links(self, tmp_path):
        import shutil
        from citation_verifier.proposition_pipeline import merge_claims
        src = Path(__file__).parent / "data" / "assessment_corpora" / "withers"
        wd = tmp_path / "withers"
        shutil.copytree(src, wd)
        expected = {c["claim_id"]: c["opinion_file"] for c in
                    csv.DictReader((src / "claims.csv").open(encoding="utf-8"))}
        merge_claims(wd)
        got = {c["claim_id"]: c["opinion_file"] for c in
               csv.DictReader((wd / "claims.csv").open(encoding="utf-8"))}
        assert got == expected


def _claims_only_workdir(tmp_path):
    wd = tmp_path / "wd"
    wd.mkdir()
    with (wd / "claims.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "claim_id", "page", "proposition", "cited_for",
            "cited_case", "quoted_text", "brief_sentence"])
        w.writeheader()
        w.writerow({"claim_id": "t-01", "page": "1", "proposition": "P",
                    "cited_for": "", "cited_case": "A v. B, 1 U.S. 1",
                    "quoted_text": "[]", "brief_sentence": ""})
        w.writerow({"claim_id": "t-02", "page": "2", "proposition": "Q",
                    "cited_for": "", "cited_case": "A v. B, 1 U.S. 1",
                    "quoted_text": "[]", "brief_sentence": ""})
    return wd


class TestVerbs:
    def test_citations_from_claims_dedup(self, tmp_path):
        from citation_verifier.proposition_pipeline import (
            citations_from_workdir)
        wd = _claims_only_workdir(tmp_path)
        assert citations_from_workdir(wd) == ["A v. B, 1 U.S. 1"]

    def test_citations_union_extract_lists(self, tmp_path):
        from citation_verifier.proposition_pipeline import (
            citations_from_workdir)
        wd = _claims_only_workdir(tmp_path)
        (wd / "citations_toa.txt").write_text(
            "A v. B, 1 U.S. 1\nC v. D, 2 U.S. 2\n", encoding="utf-8")
        assert citations_from_workdir(wd) == [
            "A v. B, 1 U.S. 1", "C v. D, 2 U.S. 2"]

    @patch("citation_verifier.proposition_pipeline."
           "wave2_fallback_and_download")
    @patch("citation_verifier.proposition_pipeline."
           "wave1_verify_and_download")
    def test_verify_verb_chains_waves_and_writes_run_json(
            self, mock_w1, mock_w2, tmp_path):
        import asyncio
        from citation_verifier.proposition_pipeline import (
            Wave1Result, Wave2Result, run_verify)
        wd = _claims_only_workdir(tmp_path)

        async def w1(*a, **k):
            (wd / "verification_results.csv").write_text(
                "citation,status\n", encoding="utf-8")
            return Wave1Result(results=[], miss_indices=[0])

        async def w2(*a, **k):
            return Wave2Result(results=[])

        mock_w1.side_effect = w1
        mock_w2.side_effect = w2
        asyncio.run(run_verify(wd))
        assert mock_w1.called and mock_w2.called
        run = json.loads((wd / "run.json").read_text(encoding="utf-8"))
        assert "verify" in run["verbs"]
        assert run["git_hash"]

    @patch("citation_verifier.proposition_pipeline."
           "wave1_verify_and_download")
    def test_verify_verb_noops_when_results_exist(self, mock_w1, tmp_path):
        import asyncio
        from citation_verifier.proposition_pipeline import run_verify
        wd = _claims_only_workdir(tmp_path)
        (wd / "verification_results.csv").write_text(
            "citation,status\n", encoding="utf-8")
        assert asyncio.run(run_verify(wd)) is None
        assert not mock_w1.called

    def test_merge_verb_requires_results(self, tmp_path):
        from citation_verifier.proposition_pipeline import run_merge
        wd = _claims_only_workdir(tmp_path)
        with pytest.raises(FileNotFoundError):
            run_merge(wd)

    def test_merge_verb_runs_and_stamps_run_json(self, tmp_path):
        from citation_verifier.proposition_pipeline import run_merge
        wd = _claims_only_workdir(tmp_path)
        with (wd / "verification_results.csv").open(
                "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=[
                "citation", "status", "confidence", "cl_url",
                "matched_name", "diagnostics_cat", "diagnostics_msg",
                "syllabus"])
            w.writeheader()
        stats = run_merge(wd)
        assert stats.unmatched == 2
        run = json.loads((wd / "run.json").read_text(encoding="utf-8"))
        assert "merge" in run["verbs"]

    def test_run_json_stamps_schema_version(self, tmp_path):
        # claims.csv is an external interface (the us-legal-research
        # memo-import skill consumes it); run.json pins the schema
        # version so consumers can detect a breaking change.
        from citation_verifier.proposition_pipeline import run_merge
        wd = _claims_only_workdir(tmp_path)
        with (wd / "verification_results.csv").open(
                "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=["citation", "status"]).writeheader()
        run_merge(wd)
        run = json.loads((wd / "run.json").read_text(encoding="utf-8"))
        assert run["schema_version"] == 1


class TestExtractQuotedSpans:
    def test_two_word_span_extracted(self):
        from citation_verifier.proposition_pipeline import (
            extract_quoted_spans)
        text = ('The court treated stipulations as "judicial admissions" '
                'binding on the parties.')
        assert extract_quoted_spans(text) == ["judicial admissions"]

    def test_smart_quotes(self):
        from citation_verifier.proposition_pipeline import (
            extract_quoted_spans)
        text = "Held that “good cause shown” is required."
        assert extract_quoted_spans(text) == ["good cause shown"]

    def test_single_word_skipped(self):
        from citation_verifier.proposition_pipeline import (
            extract_quoted_spans)
        assert extract_quoted_spans('The "factors" test applies.') == []

    def test_single_quoted_spans_skipped(self):
        from citation_verifier.proposition_pipeline import (
            extract_quoted_spans)
        assert extract_quoted_spans("It's 'two words' here.") == []

    def test_multiple_spans_in_order(self):
        from citation_verifier.proposition_pipeline import (
            extract_quoted_spans)
        text = '"first span here" and then "second span" follows'
        assert extract_quoted_spans(text) == ["first span here",
                                              "second span"]

    def test_empty_and_none_safe(self):
        from citation_verifier.proposition_pipeline import (
            extract_quoted_spans)
        assert extract_quoted_spans("") == []
        assert extract_quoted_spans(None) == []


class TestCheckQuotesExtensions:
    def _wd(self, tmp_path, proposition, opinion_text, quoted_text="[]"):
        wd = tmp_path / "wd"
        (wd / "opinions").mkdir(parents=True)
        (wd / "opinions" / "A.html").write_text(opinion_text,
                                                encoding="utf-8")
        with (wd / "claims.csv").open("w", newline="",
                                      encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=[
                "claim_id", "proposition", "cited_case", "quoted_text",
                "opinion_file", "cl_status"])
            w.writeheader()
            w.writerow({"claim_id": "t-01", "proposition": proposition,
                        "cited_case": "A v. B", "quoted_text": quoted_text,
                        "opinion_file": "opinions/A.html",
                        "cl_status": "VERIFIED"})
        return wd

    def test_derives_quotes_from_proposition(self, tmp_path):
        from citation_verifier.proposition_pipeline import check_quotes
        wd = self._wd(tmp_path,
                      'Stipulations are "judicial admissions" here.',
                      "nothing relevant in this opinion text at all")
        stats = check_quotes(wd)
        claims = list(csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8")))
        assert json.loads(claims[0]["quoted_text"]) == [
            "judicial admissions"]
        assert claims[0]["quote_check_worst"] == "FABRICATED"
        assert claims[0]["quote_floor"] == "Yellow"
        assert stats.derived_quotes == 1

    def test_verbatim_quote_no_floor(self, tmp_path):
        from citation_verifier.proposition_pipeline import check_quotes
        wd = self._wd(tmp_path,
                      'The court said "exact words match" plainly.',
                      "Indeed the court said exact words match in text.")
        check_quotes(wd)
        claims = list(csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8")))
        assert claims[0]["quote_check_worst"] == "VERBATIM"
        assert claims[0]["quote_floor"] == ""

    def test_existing_quoted_text_not_overwritten(self, tmp_path):
        from citation_verifier.proposition_pipeline import check_quotes
        wd = self._wd(tmp_path,
                      'Also has "another quote" inside.',
                      "supplied span appears right here in the opinion",
                      quoted_text=json.dumps(["supplied span appears"]))
        check_quotes(wd)
        claims = list(csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8")))
        assert json.loads(claims[0]["quoted_text"]) == [
            "supplied span appears"]

    def test_quote_floor_exempts_a_lone_altered_word(self):
        """The exemption is structural, not a similarity band (2026-09-22).

        The old [0.75, 0.85) band was an artifact of the matcher's 0.80
        ceiling, which made "verbatim plus a star-pagination marker" and "one
        word changed" score the same. CLOSE now means a word really differs,
        at any similarity; what is exempt is a CLOSE touching ONE word, which
        is where the immaterial case ("or" for "and") cannot be told from the
        material one ("shall" for "may").
        """
        from citation_verifier.proposition_pipeline import _quote_floor
        fab = {"result": "FABRICATED", "similarity": 0.2}
        one_word = {"result": "CLOSE", "similarity": 0.98,
                    "altered_words": 1}
        two_words = {"result": "CLOSE", "similarity": 0.98,
                     "altered_words": 2}
        low_one_word = {"result": "CLOSE", "similarity": 0.64,
                        "altered_words": 1}
        verbatim = {"result": "VERBATIM", "similarity": 1.0}
        assert _quote_floor([fab]) == "Yellow"
        assert _quote_floor([two_words]) == "Yellow"
        assert _quote_floor([one_word]) == ""
        # similarity is not consulted at all -- only the word count
        assert _quote_floor([low_one_word]) == ""
        assert _quote_floor([verbatim]) == ""
        assert _quote_floor([verbatim, one_word, fab]) == "Yellow"
        assert _quote_floor([]) == ""

    def test_quote_floor_is_conservative_for_legacy_rows(self):
        """A claims.csv written before `altered_words` existed has no count;
        those CLOSEs floor rather than being silently exempted."""
        from citation_verifier.proposition_pipeline import _quote_floor
        assert _quote_floor([{"result": "CLOSE", "similarity": 0.8}]) == "Yellow"

    def test_no_quotes_anywhere_still_no_quotes(self, tmp_path):
        from citation_verifier.proposition_pipeline import check_quotes
        wd = self._wd(tmp_path, "No quotation marks at all.", "text")
        check_quotes(wd)
        claims = list(csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8")))
        assert claims[0]["quote_check_worst"] == "NO_QUOTES"
        assert claims[0]["quote_floor"] == ""


class TestPromptTemplate:
    def test_renders_identical_to_established_prompt(self):
        """assess-v1 fidelity: the template must render byte-identical to
        the prompt every recorded cassette was produced with."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        import measure_withers_assessment as mwa
        from citation_verifier.proposition_pipeline import (
            render_assess_prompt)
        opinion = Path("C:/some/workdir/opinions/Nix_v_Whiteside.html")
        expected = mwa.build_prompt(
            opinion, "Nix v. Whiteside, 475 U.S. 157 (1986)",
            'The rules are there to protect "the integrity" of the process.',
            "CLOSE")
        got = render_assess_prompt(
            "assess-v1", str(opinion),
            "Nix v. Whiteside, 475 U.S. 157 (1986)",
            'The rules are there to protect "the integrity" of the process.',
            "CLOSE")
        assert got == expected

    def test_version_header_mismatch_raises(self, tmp_path, monkeypatch):
        import citation_verifier.proposition_pipeline as pp
        bad = tmp_path / "assess_v9.md"
        bad.write_text("<!-- prompt_version: assess-v1 -->\nbody",
                       encoding="utf-8")
        monkeypatch.setattr(pp, "_PROMPTS_DIR", tmp_path)
        with pytest.raises(ValueError):
            pp.load_prompt_template("assess-v9")


def _copy_withers(tmp_path):
    import shutil
    src = Path(__file__).parent / "data" / "assessment_corpora" / "withers"
    wd = tmp_path / "withers"
    shutil.copytree(src, wd)
    return wd


WITHERS_CASSETTE = (Path(__file__).parent / "data" / "assessment_corpora"
                    / "withers" / "jobs" / "assess_results.jsonl")


class TestAssessVerb:
    def test_jobs_mode_writes_jobs_and_reports_pending(self, tmp_path):
        from citation_verifier.proposition_pipeline import run_assess
        wd = _copy_withers(tmp_path)
        (wd / "jobs" / "assess_results.jsonl").unlink()
        stats = run_assess(wd)
        assert stats.eligible == 29
        assert stats.done == 0
        assert stats.pending == 29
        assert stats.skipped_deterministic == 5
        jobs = json.loads((wd / "jobs" / "assess.json").read_text(
            encoding="utf-8"))
        assert len(jobs) == 29
        # prompts are fully rendered: cite + absolute opinion path inside
        sample = jobs[0]
        assert sample["prompt_version"] == "assess-v1"
        assert "Read the opinion file at:" in sample["prompt"]
        assert str(wd) in sample["prompt"]

    def test_resume_ingests_partial_verdicts(self, tmp_path):
        from citation_verifier.executor import (
            Verdict, append_verdict_jsonl)
        from citation_verifier.proposition_pipeline import run_assess
        wd = _copy_withers(tmp_path)
        (wd / "jobs" / "assess_results.jsonl").unlink()
        run_assess(wd)
        append_verdict_jsonl(
            wd / "jobs" / "assess_results.jsonl",
            Verdict(claim_id="withers-01",
                    fields={"assessment": "Yellow", "rationale": "r"},
                    model="opus", prompt_version="assess-v1"))
        stats = run_assess(wd)
        assert stats.done == 1
        assert stats.pending == 28
        jobs = json.loads((wd / "jobs" / "assess.json").read_text(
            encoding="utf-8"))
        assert len(jobs) == 28
        assert not any("withers-01" in j["claim_ids"] for j in jobs)

    def test_recorded_executor_completes_offline(self, tmp_path):
        from citation_verifier.executor import RecordedExecutor
        from citation_verifier.proposition_pipeline import run_assess
        wd = _copy_withers(tmp_path)
        (wd / "jobs" / "assess_results.jsonl").unlink()
        ex = RecordedExecutor(WITHERS_CASSETTE)
        stats = run_assess(wd, executor=ex)
        assert stats.done == 29
        assert stats.pending == 0
        from citation_verifier.executor import load_verdicts_jsonl
        assert len(load_verdicts_jsonl(
            wd / "jobs" / "assess_results.jsonl")) == 29

    def test_idempotent_when_complete(self, tmp_path):
        from citation_verifier.proposition_pipeline import run_assess
        wd = _copy_withers(tmp_path)  # cassette already complete
        stats = run_assess(wd)
        assert stats.done == 29
        assert stats.pending == 0
        assert not (wd / "jobs" / "assess.json").exists()


class TestApplyAssessments:
    def test_applies_verdicts_with_floor(self, tmp_path):
        from citation_verifier.proposition_pipeline import (
            run_apply_assessments)
        wd = _copy_withers(tmp_path)
        stats = run_apply_assessments(wd)
        assert stats.applied == 29
        assert stats.invalid == 0
        claims = {c["claim_id"]: c for c in csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8"))}
        # withers-09: recorded verdict Green, quote_floor Yellow -> floored
        assert claims["withers-09"]["quote_floor"] == "Yellow"
        assert claims["withers-09"]["assessment"] == "Yellow"
        assert claims["withers-09"]["assessed_by"] == "opus/assess-v1"
        # support column exists, empty for v1 (single-color schema)
        assert claims["withers-09"]["support"] == ""
        # rationale lands in finding_analysis when it was empty
        assert claims["withers-09"]["finding_analysis"]

    def test_matches_scoring_predictions(self, tmp_path):
        """The two floor implementations (apply-assessments and offline
        scoring) must agree on every agent-assessed claim."""
        from citation_verifier.proposition_pipeline import (
            run_apply_assessments)
        from citation_verifier.scoring import (
            RecordedExecutor, predict_workdir)
        wd = _copy_withers(tmp_path)
        run_apply_assessments(wd)
        claims = {c["claim_id"]: c for c in csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8"))}
        ex = RecordedExecutor(WITHERS_CASSETTE)
        for p in predict_workdir(wd, ex, "assess-v1"):
            if p.mode == "agent":
                assert claims[p.claim_id]["assessment"] == p.predicted, \
                    p.claim_id

    def test_invalid_verdict_reported_not_applied(self, tmp_path):
        from citation_verifier.executor import (
            Verdict, append_verdict_jsonl)
        from citation_verifier.proposition_pipeline import (
            run_apply_assessments)
        wd = _copy_withers(tmp_path)
        (wd / "jobs" / "assess_results.jsonl").unlink()
        append_verdict_jsonl(
            wd / "jobs" / "assess_results.jsonl",
            Verdict(claim_id="withers-01",
                    fields={"assessment": "Purple", "rationale": "r"},
                    model="opus", prompt_version="assess-v1"))
        stats = run_apply_assessments(wd)
        assert stats.invalid == 1
        assert stats.applied == 0
        claims = {c["claim_id"]: c for c in csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8"))}
        assert claims["withers-01"].get("assessment", "") == ""

    def test_requires_results_jsonl(self, tmp_path):
        from citation_verifier.proposition_pipeline import (
            run_apply_assessments)
        wd = _copy_withers(tmp_path)
        (wd / "jobs" / "assess_results.jsonl").unlink()
        with pytest.raises(FileNotFoundError):
            run_apply_assessments(wd)


class TestCli:
    def test_merge_verb_dispatch(self, tmp_path, monkeypatch, capsys):
        from citation_verifier.__main__ import verify_propositions_main
        from citation_verifier.proposition_pipeline import MergeStats
        called = {}

        def fake_merge(wd):
            called["wd"] = Path(wd)
            return MergeStats(matched=2, opinion_count=1)

        monkeypatch.setattr(
            "citation_verifier.proposition_pipeline.run_merge", fake_merge)
        wd = tmp_path / "wd"
        wd.mkdir()
        rc = verify_propositions_main([str(wd), "merge"])
        assert rc == 0
        assert called["wd"] == wd
        assert "[OK]" in capsys.readouterr().out

    def test_verify_verb_noop_message(self, tmp_path, capsys):
        from citation_verifier.__main__ import verify_propositions_main
        wd = _claims_only_workdir(tmp_path)
        (wd / "verification_results.csv").write_text(
            "citation,status\n", encoding="utf-8")
        rc = verify_propositions_main([str(wd), "verify"])
        assert rc == 0
        assert "already done" in capsys.readouterr().out

    def test_unknown_verb_errors(self, tmp_path):
        from citation_verifier.__main__ import verify_propositions_main
        wd = tmp_path / "wd"
        wd.mkdir()
        with pytest.raises(SystemExit):
            verify_propositions_main([str(wd), "frobnicate"])

    def test_missing_workdir_errors(self, tmp_path):
        from citation_verifier.__main__ import verify_propositions_main
        rc = verify_propositions_main([str(tmp_path / "nope"), "merge"])
        assert rc == 1

    def test_assess_replay_then_apply(self, tmp_path, capsys):
        """Offline end-to-end through the CLI: assess --replay ingests the
        recorded cassette, apply-assessments writes the colors."""
        from citation_verifier.__main__ import verify_propositions_main
        wd = _copy_withers(tmp_path)
        (wd / "jobs" / "assess_results.jsonl").unlink()
        rc = verify_propositions_main(
            [str(wd), "assess", "--replay", str(WITHERS_CASSETTE)])
        assert rc == 0
        assert "29 done, 0 pending" in capsys.readouterr().out
        rc = verify_propositions_main([str(wd), "apply-assessments"])
        assert rc == 0
        assert "29 applied" in capsys.readouterr().out
        claims = {c["claim_id"]: c for c in csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8"))}
        assert claims["withers-09"]["assessment"] == "Yellow"

    def test_assess_jobs_mode_reports_pending(self, tmp_path, capsys):
        from citation_verifier.__main__ import verify_propositions_main
        wd = _copy_withers(tmp_path)
        (wd / "jobs" / "assess_results.jsonl").unlink()
        rc = verify_propositions_main([str(wd), "assess"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "29 pending" in out
        assert "PENDING" in out

    def test_extract_verb_dispatch(self, tmp_path, monkeypatch, capsys):
        from citation_verifier.__main__ import verify_propositions_main
        from citation_verifier.proposition_pipeline import ExtractStats
        called = {}

        def fake_extract(wd, document, executor=None,
                         prompt_version="extract-v1", force=False):
            called["wd"] = Path(wd)
            called["doc"] = str(document)
            called["executor"] = executor
            return ExtractStats(claims=2, toa=1, body=2)

        monkeypatch.setattr(
            "citation_verifier.proposition_pipeline.run_extract",
            fake_extract)
        wd = tmp_path / "wd"
        wd.mkdir()
        doc = tmp_path / "brief.pdf"
        doc.write_bytes(b"%PDF")
        rc = verify_propositions_main(
            [str(wd), "extract", "--document", str(doc)])
        assert rc == 0
        assert called["doc"] == str(doc)
        assert called["executor"] is None  # jobs mode default
        assert "[OK] extract" in capsys.readouterr().out

    def test_extract_verb_requires_document(self, tmp_path, capsys):
        from citation_verifier.__main__ import verify_propositions_main
        wd = tmp_path / "wd"
        wd.mkdir()
        rc = verify_propositions_main([str(wd), "extract"])
        assert rc == 1
        assert "--document" in capsys.readouterr().err

    def test_extract_pending_message(self, tmp_path, monkeypatch, capsys):
        from citation_verifier.__main__ import verify_propositions_main
        from citation_verifier.proposition_pipeline import ExtractStats
        monkeypatch.setattr(
            "citation_verifier.proposition_pipeline.run_extract",
            lambda *a, **k: ExtractStats(pending=True))
        wd = tmp_path / "wd"
        wd.mkdir()
        doc = tmp_path / "b.pdf"
        doc.write_bytes(b"%PDF")
        rc = verify_propositions_main(
            [str(wd), "extract", "--document", str(doc)])
        assert rc == 0
        assert "PENDING" in capsys.readouterr().out

    def test_assess_executor_sdk_flag(self, tmp_path, monkeypatch, capsys):
        from citation_verifier.__main__ import verify_propositions_main
        from citation_verifier.executor import AgentSDKExecutor
        from citation_verifier.proposition_pipeline import AssessStats
        captured = {}

        def fake_assess(wd, executor=None, prompt_version="assess-v1"):
            captured["executor"] = executor
            return AssessStats(eligible=1, done=1)

        monkeypatch.setattr(
            "citation_verifier.proposition_pipeline.run_assess",
            fake_assess)
        wd = tmp_path / "wd"
        wd.mkdir()
        rc = verify_propositions_main(
            [str(wd), "assess", "--executor", "sdk", "--model", "haiku"])
        assert rc == 0
        assert isinstance(captured["executor"], AgentSDKExecutor)
        assert captured["executor"].model == "haiku"

    def test_assess_defaults_to_v2_prompt(self, tmp_path, monkeypatch):
        """Shakedown 2026-06-13 fix: the propositions CLI defaults the
        assess/apply prompt to assess-v2 (the product default), so a
        naive `full --document` run gets the two-axis + report-block
        output -- not the thin v1 cards. The library constant
        DEFAULT_PROMPT_VERSION stays assess-v1 (frozen-cassette tests)."""
        from citation_verifier.__main__ import verify_propositions_main
        from citation_verifier.proposition_pipeline import AssessStats
        captured = {}

        def fake_assess(wd, executor=None, prompt_version="assess-v1"):
            captured["assess"] = prompt_version
            return AssessStats(eligible=1, done=1)

        monkeypatch.setattr(
            "citation_verifier.proposition_pipeline.run_assess",
            fake_assess)
        wd = tmp_path / "wd"
        wd.mkdir()
        rc = verify_propositions_main([str(wd), "assess"])
        assert rc == 0
        assert captured["assess"] == "assess-v2"

    def test_apply_defaults_to_v2_prompt(self, tmp_path, monkeypatch):
        from citation_verifier.__main__ import verify_propositions_main
        from citation_verifier.proposition_pipeline import ApplyStats
        captured = {}

        def fake_apply(wd, prompt_version="assess-v1"):
            captured["apply"] = prompt_version
            return ApplyStats(applied=1)

        monkeypatch.setattr(
            "citation_verifier.proposition_pipeline.run_apply_assessments",
            fake_apply)
        wd = tmp_path / "wd"
        wd.mkdir()
        rc = verify_propositions_main([str(wd), "apply-assessments"])
        assert rc == 0
        assert captured["apply"] == "assess-v2"

    def test_explicit_v1_still_overrides_default(self, tmp_path,
                                                 monkeypatch):
        from citation_verifier.__main__ import verify_propositions_main
        from citation_verifier.proposition_pipeline import AssessStats
        captured = {}
        monkeypatch.setattr(
            "citation_verifier.proposition_pipeline.run_assess",
            lambda wd, executor=None, prompt_version="assess-v1": (
                captured.update(v=prompt_version) or AssessStats()))
        wd = tmp_path / "wd"
        wd.mkdir()
        verify_propositions_main(
            [str(wd), "assess", "--prompt-version", "assess-v1"])
        assert captured["v"] == "assess-v1"

    def test_crosscheck_verb_dispatch(self, tmp_path, monkeypatch, capsys):
        from citation_verifier.__main__ import verify_propositions_main
        from citation_verifier.proposition_pipeline import CrosscheckStats
        monkeypatch.setattr(
            "citation_verifier.proposition_pipeline.run_crosscheck",
            lambda wd: CrosscheckStats(total=3, court_mismatches=1))
        wd = tmp_path / "wd"
        wd.mkdir()
        rc = verify_propositions_main([str(wd), "crosscheck"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "[OK] crosscheck" in out
        assert "1 court" in out

    def test_triage_verb_dispatch(
            self, tmp_path, monkeypatch, capsys):
        from citation_verifier.__main__ import verify_propositions_main
        from citation_verifier.proposition_pipeline import TriageStats
        captured = {}

        def fake_triage(wd):
            captured["called"] = True
            return TriageStats(full=2, fast=1, skipped=1)

        monkeypatch.setattr(
            "citation_verifier.proposition_pipeline.run_triage",
            fake_triage)
        wd = tmp_path / "wd"
        wd.mkdir()
        rc = verify_propositions_main([str(wd), "triage"])
        assert rc == 0
        assert captured["called"] is True
        assert "[OK] triage" in capsys.readouterr().out

    def test_prescreen_flag_removed(self, tmp_path):
        """F4: the prescreen path is deleted; --prescreen is no longer a
        valid flag (argparse exits 2)."""
        from citation_verifier.__main__ import verify_propositions_main
        wd = tmp_path / "wd"
        wd.mkdir()
        with pytest.raises(SystemExit):
            verify_propositions_main([str(wd), "triage", "--prescreen"])

    def test_full_chain_runs_new_verbs_in_order(
            self, tmp_path, monkeypatch):
        """full = verify -> merge -> check-quotes -> crosscheck ->
        triage -> assess (-> apply)."""
        from citation_verifier.__main__ import verify_propositions_main
        import citation_verifier.proposition_pipeline as pp
        order = []
        monkeypatch.setattr(pp, "run_merge",
                            lambda wd: order.append("merge") or
                            pp.MergeStats())
        monkeypatch.setattr(pp, "run_check_quotes",
                            lambda wd: order.append("check-quotes") or
                            pp.QuoteCheckStats())
        monkeypatch.setattr(pp, "run_crosscheck",
                            lambda wd: order.append("crosscheck") or
                            pp.CrosscheckStats())
        monkeypatch.setattr(
            pp, "run_triage",
            lambda wd: order.append("triage") or
            pp.TriageStats())
        monkeypatch.setattr(
            pp, "run_assess",
            lambda wd, executor=None, prompt_version="assess-v1":
            order.append("assess") or pp.AssessStats(pending=1))
        wd = tmp_path / "wd"
        wd.mkdir()
        (wd / "verification_results.csv").write_text(
            "citation,status\n", encoding="utf-8")  # verify no-ops
        rc = verify_propositions_main([str(wd), "full"])
        assert rc == 0
        assert order == ["merge", "check-quotes", "crosscheck",
                         "triage", "assess"]

    def test_report_verb_dispatch(self, tmp_path, monkeypatch, capsys):
        from citation_verifier.__main__ import verify_propositions_main
        from citation_verifier.proposition_pipeline import ReportStats
        monkeypatch.setattr(
            "citation_verifier.proposition_pipeline.run_report",
            lambda wd: ReportStats(path=Path(wd) / "report.html",
                                   findings=2, check_cite=1,
                                   verified=3, unable=1))
        wd = tmp_path / "wd"
        wd.mkdir()
        rc = verify_propositions_main([str(wd), "report"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "[OK] report" in out
        assert "1 check-cite" in out

    def test_full_chain_reaches_report_when_verdicts_complete(
            self, tmp_path, monkeypatch):
        from citation_verifier.__main__ import verify_propositions_main
        import citation_verifier.proposition_pipeline as pp
        order = []
        monkeypatch.setattr(pp, "run_merge",
                            lambda wd: order.append("merge") or
                            pp.MergeStats())
        monkeypatch.setattr(pp, "run_check_quotes",
                            lambda wd: order.append("check-quotes") or
                            pp.QuoteCheckStats())
        monkeypatch.setattr(pp, "run_crosscheck",
                            lambda wd: order.append("crosscheck") or
                            pp.CrosscheckStats())
        monkeypatch.setattr(
            pp, "run_triage",
            lambda wd: order.append("triage") or
            pp.TriageStats())
        monkeypatch.setattr(
            pp, "run_assess",
            lambda wd, executor=None, prompt_version="assess-v1":
            order.append("assess") or pp.AssessStats(eligible=1, done=1))
        monkeypatch.setattr(
            pp, "run_apply_assessments",
            lambda wd, prompt_version="assess-v1":
            order.append("apply") or pp.ApplyStats(applied=1))
        monkeypatch.setattr(
            pp, "run_report",
            lambda wd: order.append("report") or
            pp.ReportStats(path=Path(wd) / "report.html"))
        wd = tmp_path / "wd"
        wd.mkdir()
        (wd / "verification_results.csv").write_text(
            "citation,status\n", encoding="utf-8")  # verify no-ops
        rc = verify_propositions_main([str(wd), "full"])
        assert rc == 0
        assert order == ["merge", "check-quotes", "crosscheck",
                         "triage", "assess", "apply", "report"]

    def test_replay_beats_executor_flag(self, tmp_path, monkeypatch):
        """--replay wins over --executor (offline determinism first)."""
        from citation_verifier.__main__ import verify_propositions_main
        from citation_verifier.executor import (
            RecordedExecutor, Verdict, append_verdict_jsonl)
        from citation_verifier.proposition_pipeline import AssessStats
        captured = {}
        monkeypatch.setattr(
            "citation_verifier.proposition_pipeline.run_assess",
            lambda wd, executor=None, prompt_version="assess-v1": (
                captured.update(executor=executor) or AssessStats()))
        cassette = tmp_path / "rec.jsonl"
        append_verdict_jsonl(cassette, Verdict(
            claim_id="x", fields={}, prompt_version="assess-v1"))
        wd = tmp_path / "wd"
        wd.mkdir()
        verify_propositions_main(
            [str(wd), "assess", "--replay", str(cassette),
             "--executor", "sdk"])
        assert isinstance(captured["executor"], RecordedExecutor)


def _extract_verdict_fields():
    return {
        "claims": [
            {"page": "3",
             "proposition": "Settlement evidence is irrelevant.",
             "cited_for": "",
             "cited_case": "Tompkins v. Cyr, 202 F.3d 770 (5th Cir. 2000)",
             "quoted_text": ["consequential fact"],
             "brief_sentence": "See Tompkins v. Cyr, 202 F.3d 770, 787 "
                               "(5th Cir. 2000) (evidence must be relevant "
                               "to a 'consequential fact')."},
            {"page": "5",
             "proposition": "Bad faith is required for spoliation.",
             "cited_for": "adverse-inference standard",
             "cited_case": "King v. Ill. Cent. R.R., 337 F.3d 550 "
                           "(5th Cir. 2003)",
             "quoted_text": [],
             "brief_sentence": "King v. Ill. Cent. R.R., 337 F.3d 550, 556 "
                               "(5th Cir. 2003)."},
        ],
        "citations_toa": ["Tompkins v. Cyr, 202 F.3d 770 (5th Cir. 2000)"],
        "citations_body": [
            "Tompkins v. Cyr, 202 F.3d 770 (5th Cir. 2000)",
            "King v. Ill. Cent. R.R., 337 F.3d 550 (5th Cir. 2003)",
        ],
    }


def _extract_workdir(tmp_path, name="matter"):
    wd = tmp_path / name
    wd.mkdir()
    doc = wd / "brief.pdf"
    doc.write_bytes(b"%PDF-1.4 fake")
    return wd, doc


def _extract_cassette(path, fields=None):
    from citation_verifier.executor import Verdict, append_verdict_jsonl
    append_verdict_jsonl(path, Verdict(
        claim_id="extract", fields=fields or _extract_verdict_fields(),
        model="opus", prompt_version="extract-v1"))


class TestRunExtract:
    def test_jobs_mode_writes_jobs_file_and_pends(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        wd, doc = _extract_workdir(tmp_path)
        stats = pp.run_extract(wd, doc)
        assert stats.pending is True
        assert stats.claims == 0
        assert not (wd / "claims.csv").exists()
        jobs = json.loads((wd / "jobs" / "extract.json")
                          .read_text(encoding="utf-8"))
        assert len(jobs) == 1
        assert jobs[0]["claim_ids"] == ["extract"]
        assert jobs[0]["prompt_version"] == "extract-v1"
        assert str(doc) in jobs[0]["prompt"]
        assert jobs[0]["files"] == [str(doc)]

    def test_replay_writes_claims_and_citation_lists(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        from citation_verifier.executor import RecordedExecutor
        wd, doc = _extract_workdir(tmp_path)
        cassette = tmp_path / "rec.jsonl"
        _extract_cassette(cassette)
        stats = pp.run_extract(wd, doc, executor=RecordedExecutor(cassette))
        assert stats.pending is False
        assert (stats.claims, stats.toa, stats.body) == (2, 1, 2)
        with open(wd / "claims.csv", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert [r["claim_id"] for r in rows] == ["matter-01", "matter-02"]
        assert rows[0]["cited_case"].startswith("Tompkins v. Cyr")
        assert json.loads(rows[0]["quoted_text"]) == ["consequential fact"]
        assert rows[1]["cited_for"] == "adverse-inference standard"
        toa = (wd / "citations_toa.txt").read_text(encoding="utf-8")
        assert toa.strip().splitlines() == [
            "Tompkins v. Cyr, 202 F.3d 770 (5th Cir. 2000)"]
        body = (wd / "citations_body.txt").read_text(encoding="utf-8")
        assert len(body.strip().splitlines()) == 2
        # the verdict was persisted for resume
        assert (wd / "jobs" / "extract_results.jsonl").exists()

    def test_rerun_ingests_appended_verdict(self, tmp_path):
        """Jobs-mode round trip: pend, agent appends, rerun ingests."""
        import citation_verifier.proposition_pipeline as pp
        wd, doc = _extract_workdir(tmp_path)
        assert pp.run_extract(wd, doc).pending is True
        _extract_cassette(wd / "jobs" / "extract_results.jsonl")
        stats = pp.run_extract(wd, doc)
        assert stats.pending is False
        assert stats.claims == 2
        assert (wd / "claims.csv").exists()

    def test_noop_when_claims_exist(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        wd, doc = _extract_workdir(tmp_path)
        (wd / "claims.csv").write_text("claim_id\nx-01\n", encoding="utf-8")
        assert pp.run_extract(wd, doc) is None
        assert (wd / "claims.csv").read_text(
            encoding="utf-8") == "claim_id\nx-01\n"

    def test_malformed_verdict_raises(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        from citation_verifier.executor import RecordedExecutor
        wd, doc = _extract_workdir(tmp_path)
        cassette = tmp_path / "rec.jsonl"
        _extract_cassette(cassette, fields={"claims": "not-a-list"})
        with pytest.raises(ValueError, match="extract verdict"):
            pp.run_extract(wd, doc, executor=RecordedExecutor(cassette))
        assert not (wd / "claims.csv").exists()

    def test_claim_missing_required_field_raises(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        from citation_verifier.executor import RecordedExecutor
        wd, doc = _extract_workdir(tmp_path)
        fields = _extract_verdict_fields()
        fields["claims"][1]["cited_case"] = ""
        cassette = tmp_path / "rec.jsonl"
        _extract_cassette(cassette, fields=fields)
        with pytest.raises(ValueError, match="cited_case"):
            pp.run_extract(wd, doc, executor=RecordedExecutor(cassette))

    def test_run_json_stamped(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        from citation_verifier.executor import RecordedExecutor
        wd, doc = _extract_workdir(tmp_path)
        cassette = tmp_path / "rec.jsonl"
        _extract_cassette(cassette)
        pp.run_extract(wd, doc, executor=RecordedExecutor(cassette))
        run = json.loads((wd / "run.json").read_text(encoding="utf-8"))
        assert run["verbs"]["extract"]["prompt_version"] == "extract-v1"
        assert run["verbs"]["extract"]["claims"] == 2


def _crosscheck_workdir(tmp_path):
    """Synthetic workdir: 2 claims, vr CSV with matched court, one
    opinion file with star pagination + footnotes, TOA/body lists with
    one volume discrepancy (the Bryant class)."""
    wd = tmp_path / "xc"
    wd.mkdir()
    (wd / "opinions").mkdir()
    (wd / "opinions" / "tompkins.txt").write_text(
        "*770 Start of opinion. The court held things. *775 More text "
        "here including footnote n.3 discussion. *787 The evidence must "
        "be relevant to a consequential fact. *790 End.",
        encoding="utf-8")
    with open(wd / "claims.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "claim_id", "page", "proposition", "cited_for", "cited_case",
            "quoted_text", "brief_sentence", "cl_status", "cl_url",
            "opinion_file"])
        w.writeheader()
        w.writerow({
            "claim_id": "xc-01", "page": "3",
            "proposition": "Settlement evidence is irrelevant.",
            "cited_case": "Tompkins v. Cyr, 202 F.3d 770, 787 "
                          "(5th Cir. 2000)",
            "quoted_text": "[]", "cl_status": "VERIFIED",
            "opinion_file": "opinions/tompkins.txt"})
        w.writerow({
            "claim_id": "xc-02", "page": "5",
            "proposition": "Out-of-range pinpoint and bad footnote.",
            "cited_case": "Tompkins v. Cyr, 202 F.3d 770, 999 "
                          "(6th Cir. 2000) n.42",
            "quoted_text": "[]", "cl_status": "VERIFIED",
            "opinion_file": "opinions/tompkins.txt"})
    with open(wd / "verification_results.csv", "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "citation", "status", "confidence", "cl_url", "matched_name",
            "matched_court", "matched_court_id", "diagnostics_cat",
            "diagnostics_msg", "syllabus"])
        w.writeheader()
        for cite in ("Tompkins v. Cyr, 202 F.3d 770, 787 (5th Cir. 2000)",
                     "Tompkins v. Cyr, 202 F.3d 770, 999 (6th Cir. 2000) "
                     "n.42"):
            w.writerow({"citation": cite, "status": "VERIFIED",
                        "confidence": "1.00",
                        "matched_name": "Tompkins v. Cyr",
                        "matched_court": "Fifth Circuit",
                        "matched_court_id": "ca5"})
    (wd / "citations_toa.txt").write_text(
        "Tompkins v. Cyr, 202 F.3d 770 (5th Cir. 2000)\n"
        "Bryant v. Jones, 597 F.3d 1320 (11th Cir. 2010)\n",
        encoding="utf-8")
    (wd / "citations_body.txt").write_text(
        "Tompkins v. Cyr, 202 F.3d 770 (5th Cir. 2000)\n"
        "Bryant v. Jones, 97 F.3d 1320 (11th Cir. 2010)\n",
        encoding="utf-8")
    return wd


class TestRunCrosscheck:
    def test_clean_claim_gets_empty_flags(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        wd = _crosscheck_workdir(tmp_path)
        stats = pp.run_crosscheck(wd)
        rows = {r["claim_id"]: r for r in csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8"))}
        assert rows["xc-01"]["crosscheck_flags"] == ""
        assert stats.total == 2

    def test_court_mismatch_flagged(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        wd = _crosscheck_workdir(tmp_path)
        pp.run_crosscheck(wd)
        rows = {r["claim_id"]: r for r in csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8"))}
        flags = json.loads(rows["xc-02"]["crosscheck_flags"])
        assert flags["court_mismatch"]["cited_id"] == "ca6"
        assert flags["court_mismatch"]["matched_id"] == "ca5"

    def test_pincite_out_of_star_range_flagged(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        wd = _crosscheck_workdir(tmp_path)
        pp.run_crosscheck(wd)
        rows = {r["claim_id"]: r for r in csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8"))}
        flags = json.loads(rows["xc-02"]["crosscheck_flags"])
        assert flags["pincite_flag"]["pinpoint"] == "999"
        assert flags["pincite_flag"]["star_range"] == [770, 790]

    def test_footnote_missing_flagged_and_present_not(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        wd = _crosscheck_workdir(tmp_path)
        pp.run_crosscheck(wd)
        rows = {r["claim_id"]: r for r in csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8"))}
        flags2 = json.loads(rows["xc-02"]["crosscheck_flags"])
        assert flags2["pincite_flag"]["footnote_missing"] == "42"
        # xc-01 has no footnote pincite -> no flag at all
        assert rows["xc-01"]["crosscheck_flags"] == ""

    def test_toa_body_mismatch_flagged_on_matching_claims(self, tmp_path):
        """Bryant 597-vs-97 class: the mismatch is recorded on claims
        citing Bryant; Tompkins (consistent) claims stay clean."""
        import citation_verifier.proposition_pipeline as pp
        wd = _crosscheck_workdir(tmp_path)
        # add a Bryant claim
        with open(wd / "claims.csv", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
            fields = rows[0].keys()
        bryant = dict.fromkeys(fields, "")
        bryant.update({"claim_id": "xc-03",
                       "proposition": "Something about Bryant.",
                       "cited_case": "Bryant v. Jones, 597 F.3d 1320 "
                                     "(11th Cir. 2010)",
                       "quoted_text": "[]", "cl_status": "VERIFIED"})
        rows.append(bryant)
        with open(wd / "claims.csv", "w", newline="",
                  encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(fields))
            w.writeheader()
            w.writerows(rows)
        stats = pp.run_crosscheck(wd)
        out = {r["claim_id"]: r for r in csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8"))}
        flags = json.loads(out["xc-03"]["crosscheck_flags"])
        variants = flags["toa_mismatch"]["variants"]
        assert any("597 F.3d" in v for v in variants)
        assert any("97 F.3d" in v for v in variants)
        assert "toa_mismatch" not in (
            json.loads(out["xc-01"]["crosscheck_flags"])
            if out["xc-01"]["crosscheck_flags"] else {})
        assert stats.toa_mismatches >= 1

    def test_tolerates_missing_inputs(self, tmp_path):
        """Prepared-pairs workdir: no TOA/body lists, legacy vr CSV
        without matched_court columns -> runs clean, flags empty."""
        import citation_verifier.proposition_pipeline as pp
        wd = tmp_path / "bare"
        wd.mkdir()
        with open(wd / "claims.csv", "w", newline="",
                  encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=[
                "claim_id", "proposition", "cited_case", "quoted_text"])
            w.writeheader()
            w.writerow({"claim_id": "b-01", "proposition": "P.",
                        "cited_case": "A v. B, 1 F.3d 1 (1st Cir. 1990)",
                        "quoted_text": "[]"})
        stats = pp.run_crosscheck(wd)
        assert stats.total == 1
        rows = list(csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8")))
        assert rows[0]["crosscheck_flags"] == ""

    def test_runs_on_withers_corpus_copy(self, tmp_path):
        """Corpus tolerance: the frozen Withers workdir (no TOA lists,
        pre-court vr CSV) crosschecks without error and every claim
        gets a crosscheck_flags cell (possibly empty)."""
        import citation_verifier.proposition_pipeline as pp
        wd = _copy_withers(tmp_path)
        stats = pp.run_crosscheck(wd)
        rows = list(csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8")))
        assert stats.total == len(rows)
        assert all("crosscheck_flags" in r for r in rows)


class TestRunCheckQuotes:
    def test_wrapper_stamps_run_json(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        wd = _copy_withers(tmp_path)
        stats = pp.run_check_quotes(wd)
        assert stats.total_claims > 0
        run = json.loads((wd / "run.json").read_text(encoding="utf-8"))
        assert "check-quotes" in run["verbs"]

    def test_cli_check_quotes_verb(self, tmp_path, monkeypatch, capsys):
        from citation_verifier.__main__ import verify_propositions_main
        from citation_verifier.proposition_pipeline import QuoteCheckStats
        monkeypatch.setattr(
            "citation_verifier.proposition_pipeline.run_check_quotes",
            lambda wd: QuoteCheckStats(total_claims=3, verbatim=2))
        wd = tmp_path / "wd"
        wd.mkdir()
        rc = verify_propositions_main([str(wd), "check-quotes"])
        assert rc == 0
        assert "[OK] check-quotes" in capsys.readouterr().out


def _triage_claims(wd, rows):
    fields = ["claim_id", "proposition", "cited_case", "quoted_text",
              "cl_status", "opinion_file", "quote_check_worst",
              "quote_floor", "crosscheck_flags"]
    with open(wd / "claims.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            base = dict.fromkeys(fields, "")
            base.update(r)
            w.writerow(base)


class TestFootnotemarkPincite:
    def test_footnotemark_markup_counts_as_footnote_present(self, tmp_path):
        """Step 8 9.6 finding: CL harvard-XML opinions mark footnotes as
        <footnotemark>N</footnotemark>; the plain tag-strip turned them
        into bare numbers, so every n.N pincite false-flagged as
        footnote_missing (the one Withers pincite flag, withers-36, was
        exactly this artifact)."""
        import citation_verifier.proposition_pipeline as pp
        wd = tmp_path / "fp"
        (wd / "opinions").mkdir(parents=True)
        (wd / "opinions" / "j.html").write_text(
            "<p>*274 Text here.<footnotemark>10</footnotemark> More "
            "*280 text *290 end.</p>", encoding="utf-8")
        text = pp._read_clean_opinion(wd, "opinions/j.html")
        flag = pp._pincite_flag(
            "Missouri v. Jenkins, 491 U.S. 274, 288 n.10 (1989)", text)
        assert flag is None  # footnote 10 IS present; pin 288 in range

    def test_genuinely_missing_footnote_still_flags(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        wd = tmp_path / "fp"
        (wd / "opinions").mkdir(parents=True)
        (wd / "opinions" / "j.html").write_text(
            "<p>*274 Text.<footnotemark>3</footnotemark> *280 t *290 e</p>",
            encoding="utf-8")
        text = pp._read_clean_opinion(wd, "opinions/j.html")
        flag = pp._pincite_flag(
            "Missouri v. Jenkins, 491 U.S. 274, 288 n.10 (1989)", text)
        assert flag == {"footnote_missing": "10"}


class TestRunTriage:
    def _wd(self, tmp_path):
        wd = tmp_path / "tr"
        wd.mkdir()
        (wd / "opinions").mkdir()
        (wd / "opinions" / "a.txt").write_text("short opinion",
                                               encoding="utf-8")
        return wd

    def test_tracks_assigned_deterministically(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        wd = self._wd(tmp_path)
        _triage_claims(wd, [
            # clean verified, no quotes -> fast
            {"claim_id": "t-01", "cl_status": "VERIFIED",
             "opinion_file": "opinions/a.txt", "quoted_text": "[]",
             "quote_check_worst": "NO_QUOTES"},
            # CLOSE quote -> full
            {"claim_id": "t-02", "cl_status": "VERIFIED",
             "opinion_file": "opinions/a.txt", "quoted_text": "[]",
             "quote_check_worst": "CLOSE"},
            # has quoted text -> full
            {"claim_id": "t-03", "cl_status": "VERIFIED",
             "opinion_file": "opinions/a.txt",
             "quoted_text": '["some quote"]',
             "quote_check_worst": "VERBATIM"},
            # crosscheck flag -> full
            {"claim_id": "t-04", "cl_status": "VERIFIED",
             "opinion_file": "opinions/a.txt", "quoted_text": "[]",
             "quote_check_worst": "NO_QUOTES",
             "crosscheck_flags": '{"court_mismatch": {}}'},
            # CITE_UNCONFIRMED (not clean-verified) -> full
            {"claim_id": "t-05", "cl_status": "CITE_UNCONFIRMED",
             "opinion_file": "opinions/a.txt", "quoted_text": "[]",
             "quote_check_worst": "NO_QUOTES"},
            # not assessable (no opinion) -> "" (deterministic lane)
            {"claim_id": "t-06", "cl_status": "NOT_FOUND",
             "quoted_text": "[]"},
        ])
        stats = pp.run_triage(wd)
        rows = {r["claim_id"]: r for r in csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8"))}
        assert rows["t-01"]["triage_track"] == "fast"
        assert rows["t-02"]["triage_track"] == "full"
        assert rows["t-03"]["triage_track"] == "full"
        assert rows["t-04"]["triage_track"] == "full"
        assert rows["t-05"]["triage_track"] == "full"
        assert rows["t-06"]["triage_track"] == ""
        assert (stats.full, stats.fast, stats.skipped) == (4, 1, 1)

    def test_triage_preserves_legacy_prescreen_hint_column(self, tmp_path):
        """F4: the prescreen path is deleted, but a legacy claims.csv that
        already carries a prescreen_hint column round-trips it unchanged
        (tolerated legacy column)."""
        import citation_verifier.proposition_pipeline as pp
        wd = self._wd(tmp_path)
        fields = ["claim_id", "proposition", "cited_case", "quoted_text",
                  "cl_status", "opinion_file", "quote_check_worst",
                  "prescreen_hint"]
        with open(wd / "claims.csv", "w", newline="",
                  encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerow({"claim_id": "t-01", "proposition": "p",
                        "cited_case": "c", "quoted_text": "[]",
                        "cl_status": "VERIFIED",
                        "opinion_file": "opinions/a.txt",
                        "quote_check_worst": "NO_QUOTES",
                        "prescreen_hint": "legacy hint text"})
        pp.run_triage(wd)
        rows = {r["claim_id"]: r for r in csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8"))}
        assert rows["t-01"]["prescreen_hint"] == "legacy hint text"
        assert rows["t-01"]["triage_track"] == "fast"

    def test_triage_no_prescreen_kwarg(self, tmp_path):
        """F4: run_triage no longer accepts a prescreen keyword."""
        import citation_verifier.proposition_pipeline as pp
        wd = self._wd(tmp_path)
        _triage_claims(wd, [
            {"claim_id": "t-01", "cl_status": "VERIFIED",
             "opinion_file": "opinions/a.txt", "quoted_text": "[]",
             "quote_check_worst": "NO_QUOTES"},
        ])
        with pytest.raises(TypeError):
            pp.run_triage(wd, prescreen=True)

    def test_triage_on_withers_corpus_copy(self, tmp_path):
        """Corpus tolerance + sanity: every assessable claim gets a
        track; deterministic-lane rows get ''."""
        import citation_verifier.proposition_pipeline as pp
        wd = _copy_withers(tmp_path)
        stats = pp.run_triage(wd)
        rows = list(csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8")))
        tracked = [r for r in rows if r["triage_track"]]
        assert len(tracked) == stats.full + stats.fast
        assert stats.skipped == len(rows) - len(tracked)
        assert stats.full >= 1  # withers has CLOSE/FABRICATED rows


class TestMatchedCourtAccessors:
    def test_matched_court_from_download_stash(self):
        r = _result([_entry(StageName.citation_lookup,
                            {"matched_case_name": "Doe v. Memphis",
                             "matched_court": "United States Court of "
                                              "Appeals for the Sixth Circuit",
                             "matched_court_id": "ca6"})])
        assert r.matched_court.endswith("Sixth Circuit")
        assert r.matched_court_id == "ca6"

    def test_later_stage_supersedes(self):
        r = _result([
            _entry(StageName.citation_lookup, {"matched_court_id": "ca5"}),
            _entry(StageName.opinion_search, {"matched_court_id": "ca6"}),
        ])
        assert r.matched_court_id == "ca6"

    def test_empty_when_no_stage_recorded_court(self):
        r = _result([_entry(StageName.citation_lookup,
                            {"matched_case_name": "X v. Y"})])
        assert r.matched_court == ""
        assert r.matched_court_id == ""


class TestVerificationCsvCourtColumns:
    def test_matched_court_columns_written(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        r = _result([_entry(StageName.citation_lookup,
                            {"matched_case_name": "Doe v. Memphis",
                             "matched_court": "Sixth Circuit",
                             "matched_court_id": "ca6"})])
        pp._write_verification_csv(tmp_path, ["Doe v. Memphis, 1 F.3d 1"],
                                   [r])
        with open(tmp_path / "verification_results.csv", newline="",
                  encoding="utf-8") as f:
            (row,) = list(csv.DictReader(f))
        assert row["matched_court"] == "Sixth Circuit"
        assert row["matched_court_id"] == "ca6"


class TestExtractPrompt:
    def test_template_loads_and_declares_version(self):
        import citation_verifier.proposition_pipeline as pp
        body = pp.load_prompt_template("extract-v1")
        assert "{document_path}" in body
        assert "prompt_version" not in body  # header comments stripped

    def test_render_substitutes_document_path(self):
        import citation_verifier.proposition_pipeline as pp
        prompt = pp.render_extract_prompt("extract-v1", r"C:\briefs\b.pdf")
        assert r"C:\briefs\b.pdf" in prompt
        assert "{document_path}" not in prompt

    def test_render_mentions_contract_columns(self):
        import citation_verifier.proposition_pipeline as pp
        prompt = pp.render_extract_prompt("extract-v1", "doc.pdf")
        for col in ("cited_case", "proposition", "cited_for",
                    "quoted_text", "brief_sentence", "page",
                    "citations_toa", "citations_body"):
            assert col in prompt


# ---------------------------------------------------------------------------
# Step 7: report lanes (SS6.9) + card flags (SS6.5) + report verb
# ---------------------------------------------------------------------------


class TestCrosscheckFlagLines:
    def test_all_three_flag_types(self):
        import citation_verifier.proposition_pipeline as pp
        claim = {"crosscheck_flags": json.dumps({
            "toa_mismatch": {"variants": [
                "Bryant v. Jones, 597 F.3d 1320 (11th Cir. 2010)",
                "Bryant v. Jones, 97 F.3d 1320 (11th Cir. 2010)"]},
            "court_mismatch": {"cited": "ca6", "cited_id": "ca6",
                               "matched_id": "ca5",
                               "matched": "Fifth Circuit"},
            "pincite_flag": {"pinpoint": "999", "star_range": [770, 790],
                             "footnote_missing": "42"},
        })}
        lines = pp._crosscheck_flag_lines(claim)
        assert any("597 F.3d" in ln and "97 F.3d" in ln for ln in lines)
        assert any("ca6" in ln and "ca5" in ln for ln in lines)
        assert any("999" in ln and "770-790" in ln for ln in lines)
        assert any("n.42" in ln for ln in lines)
        assert len(lines) == 4  # pincite_flag yields two lines here

    def test_empty_and_missing_and_malformed(self):
        import citation_verifier.proposition_pipeline as pp
        assert pp._crosscheck_flag_lines({}) == []
        assert pp._crosscheck_flag_lines({"crosscheck_flags": ""}) == []
        assert pp._crosscheck_flag_lines(
            {"crosscheck_flags": "not json"}) == []
        assert pp._crosscheck_flag_lines(
            {"crosscheck_flags": "[1, 2]"}) == []


def _report_workdir(tmp_path, rows, fieldnames=None):
    """claims.csv-only workdir for generate_report lane tests."""
    wd = tmp_path / "rep"
    (wd / "opinions").mkdir(parents=True)
    (wd / "opinions" / "a.html").write_text("opinion", encoding="utf-8")
    fields = fieldnames or [
        "claim_id", "page", "proposition", "cited_for", "cited_case",
        "quoted_text", "brief_sentence", "cl_status", "cl_url",
        "retrieved_case", "supporting_language", "opinion_file",
        "quote_check", "quote_check_worst", "quote_floor",
        "crosscheck_flags", "triage_track", "prescreen_hint",
        "assessment", "support", "assessed_by", "finding_analysis",
        "badge_label", "brief_block", "opinion_block", "diagnostics",
        "syllabus",
    ]
    with (wd / "claims.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            base = dict.fromkeys(fields, "")
            base.update(r)
            w.writerow(base)
    return wd


def _report_row(claim_id, **kw):
    base = {"claim_id": claim_id, "page": "1",
            "proposition": f"Proposition {claim_id}.",
            "cited_case": f"Case {claim_id} v. Other, 1 F.3d 1 "
                          f"(1st Cir. 1990)",
            "quoted_text": "[]"}
    base.update(kw)
    return base


class TestReportLanesRendering:
    def test_cite_unconfirmed_is_check_cite_lane_never_red(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        wd = _report_workdir(tmp_path, [_report_row(
            "r-01", cl_status="CITE_UNCONFIRMED",
            opinion_file="opinions/a.html", assessment="Red",
            finding_analysis="Agent thought this was unsupported.",
            badge_label="Not supported by cited case")])
        pp.generate_report(wd, title="T")
        html = (wd / "report.html").read_text(encoding="utf-8")
        assert 'class="sev-orange"' in html  # dashboard issue row
        assert "Check cite -- case found by name" in html  # forced badge
        assert "Not supported by cited case" not in html  # agent badge overridden
        assert "Agent thought this was unsupported." in html  # content kept
        # red stat is zero; check-cite stat is one
        assert ('<div class="stat stat-red">'
                '<div class="stat-num">0</div>') in html
        assert ('<div class="stat stat-orange">'
                '<div class="stat-num">1</div>') in html

    def test_wrong_case_unassessed_is_red(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        wd = _report_workdir(tmp_path, [_report_row(
            "r-01", cl_status="WRONG_CASE", assessment="")])
        pp.generate_report(wd, title="T")
        html = (wd / "report.html").read_text(encoding="utf-8")
        assert 'class="sev-red"' in html
        assert "Case mismatch -- cite resolves to a different case" in html

    @pytest.mark.parametrize("status,marker", [
        ("INSUFFICIENT_DATA", "lacks the court and year"),
        ("VERIFICATION_INCOMPLETE", "could not complete"),
    ])
    def test_other_unlocatable_statuses_go_gray(self, tmp_path, status,
                                                marker):
        import citation_verifier.proposition_pipeline as pp
        wd = _report_workdir(tmp_path, [_report_row(
            "r-01", cl_status=status, assessment="")])
        pp.generate_report(wd, title="T")
        html = (wd / "report.html").read_text(encoding="utf-8")
        assert "Unable to verify" in html
        assert marker in html

    def test_flags_render_on_finding_card(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        flags = json.dumps({"court_mismatch": {
            "cited_id": "ca6", "matched_id": "ca5",
            "matched": "Fifth Circuit"}})
        wd = _report_workdir(tmp_path, [_report_row(
            "r-01", cl_status="VERIFIED", opinion_file="opinions/a.html",
            assessment="Yellow", finding_analysis="Analysis.",
            crosscheck_flags=flags)])
        pp.generate_report(wd, title="T")
        html = (wd / "report.html").read_text(encoding="utf-8")
        assert 'class="flag-chip"' in html
        assert "brief cites ca6, CL match is ca5" in html

    def test_flags_render_on_green_verified_item(self, tmp_path):
        """SS6.5: the flag shows even when support is otherwise fine."""
        import citation_verifier.proposition_pipeline as pp
        flags = json.dumps({"pincite_flag": {
            "pinpoint": "999", "star_range": [770, 790]}})
        wd = _report_workdir(tmp_path, [_report_row(
            "r-01", cl_status="VERIFIED", opinion_file="opinions/a.html",
            assessment="Green", crosscheck_flags=flags)])
        pp.generate_report(wd, title="T")
        html = (wd / "report.html").read_text(encoding="utf-8")
        assert "Verified Citations" in html
        assert 'class="flag-chip"' in html
        assert "Pincite 999" in html

    def test_no_all_clear_banner_when_check_cite_present(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        wd = _report_workdir(tmp_path, [
            _report_row("r-01", cl_status="CITE_UNCONFIRMED",
                        opinion_file="opinions/a.html", assessment="Green"),
            _report_row("r-02", cl_status="VERIFIED",
                        opinion_file="opinions/a.html", assessment="Green"),
        ])
        pp.generate_report(wd, title="T")
        html = (wd / "report.html").read_text(encoding="utf-8")
        assert "No serious issues found" not in html

    def test_legacy_rows_without_new_columns_render_as_before(
            self, tmp_path):
        """Pre-two-axis claims.csv (payne-style header) -> existing
        fallbacks: green->verified, yellow->finding, NOT_FOUND->gray;
        no flag chips."""
        import citation_verifier.proposition_pipeline as pp
        legacy_fields = [
            "page", "proposition", "cited_case", "retrieved_case",
            "supporting_language", "assessment", "cl_url", "cl_status",
            "diagnostics", "opinion_file", "quoted_text", "quote_check",
            "quote_check_worst"]
        wd = _report_workdir(tmp_path, [
            {"page": "1", "proposition": "P1.",
             "cited_case": "A v. B, 1 F.3d 1", "assessment": "Green",
             "cl_status": "VERIFIED", "opinion_file": "opinions/a.html",
             "quoted_text": "[]", "quote_check": "[]",
             "quote_check_worst": "NO_QUOTES",
             "retrieved_case": "A v. B"},
            {"page": "2", "proposition": "P2.",
             "cited_case": "C v. D, 2 F.3d 2", "assessment": "Yellow",
             "cl_status": "VERIFIED", "opinion_file": "opinions/a.html",
             "quoted_text": "[]", "quote_check": "[]",
             "quote_check_worst": "NO_QUOTES"},
            {"page": "3", "proposition": "P3.",
             "cited_case": "E v. F, 3 F.3d 3", "assessment": "",
             "cl_status": "NOT_FOUND", "opinion_file": "",
             "quoted_text": "[]", "quote_check": "[]",
             "quote_check_worst": ""},
        ], fieldnames=legacy_fields)
        pp.generate_report(wd, title="T")
        html = (wd / "report.html").read_text(encoding="utf-8")
        assert "Verified Citations" in html
        assert 'class="sev-yellow"' in html
        assert "Unable to verify" in html
        assert 'class="flag-chip"' not in html


class TestAssessV2Template:
    def _claim(self, **kw):
        base = {"claim_id": "w-01",
                "cited_case": "Nix v. Whiteside, 475 U.S. 157 (1986)",
                "proposition": "Counsel must not assist perjury.",
                "cited_for": "", "brief_sentence": "", "quoted_text": "[]",
                "quote_check": "[]", "quote_check_worst": "NO_QUOTES",
                "prescreen_hint": ""}
        base.update(kw)
        return base

    def test_template_loads_and_mentions_contract(self):
        import citation_verifier.proposition_pipeline as pp
        body = pp.load_prompt_template("assess-v2")
        for marker in ("{opinion_path}", "{claims_block}", "verdicts",
                       "supported", "partial", "unsupported",
                       "unverifiable", "badge_label", "brief_block",
                       "opinion_block", "finding_analysis"):
            assert marker in body
        assert "Do not use web search" in body  # SS6.8 prohibition

    def test_claim_block_minimal(self):
        import citation_verifier.proposition_pipeline as pp
        block = pp.render_assess_v2_claim_block(self._claim())
        assert "w-01" in block and "Nix v. Whiteside" in block
        assert "NO_QUOTES" in block
        assert "Cited for" not in block        # empty -> omitted
        assert "Preliminary review hint" not in block

    def test_claim_block_full(self):
        import citation_verifier.proposition_pipeline as pp
        block = pp.render_assess_v2_claim_block(self._claim(
            cited_for="the adverse-inference standard",
            brief_sentence="See Nix, 475 U.S. at 160 (standard).",
            quoted_text='["obvious reasons to doubt"]',
            quote_check='[{"quote": "obvious reasons to doubt", '
                        '"result": "CLOSE", "similarity": 0.72, '
                        '"matched_passage": "reasons to doubt the '
                        'veracity"}]',
            quote_check_worst="CLOSE",
            prescreen_hint="Case is about perjury, not conflicts."))
        assert "Cited for" in block and "adverse-inference" in block
        assert "obvious reasons to doubt" in block
        assert "reasons to doubt the veracity" in block  # passage hint
        assert "sim=0.72" in block
        assert "Preliminary review hint" in block

    def test_low_sim_passage_hint_omitted(self):
        import citation_verifier.proposition_pipeline as pp
        block = pp.render_assess_v2_claim_block(self._claim(
            quote_check='[{"quote": "x y", "result": "FABRICATED", '
                        '"similarity": 0.4, "matched_passage": "junk"}]',
            quote_check_worst="FABRICATED"))
        assert "junk" not in block  # below the 0.65 hint floor

    def test_render_assess_v2_prompt(self):
        import citation_verifier.proposition_pipeline as pp
        prompt = pp.render_assess_v2_prompt(
            "assess-v2", "opinions/nix.html",
            [self._claim(), self._claim(claim_id="w-02")])
        assert "opinions/nix.html" in prompt
        assert prompt.count("Claim w-0") == 2
        assert "{claims_block}" not in prompt


class TestRunAssessV2:
    def test_jobs_packed_per_opinion(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        wd = _copy_withers(tmp_path)
        (wd / "jobs" / "assess_results.jsonl").unlink()
        stats = pp.run_assess(wd, prompt_version="assess-v2")
        jobs = json.loads((wd / "jobs" / "assess.json")
                          .read_text(encoding="utf-8"))
        with open(wd / "claims.csv", newline="", encoding="utf-8") as f:
            claims = [c for c in csv.DictReader(f) if pp._assessable(c)]
        opinions = {c["opinion_file"] for c in claims}
        assert len(jobs) == len(opinions)          # one job per opinion
        all_ids = [cid for j in jobs for cid in j["claim_ids"]]
        assert sorted(all_ids) == sorted(c["claim_id"] for c in claims)
        assert all(j["prompt_version"] == "assess-v2" for j in jobs)
        # multi-claim job exists (withers has shared opinions) and its
        # prompt carries every claim's id
        multi = next(j for j in jobs if len(j["claim_ids"]) > 1)
        for cid in multi["claim_ids"]:
            assert f"Claim {cid}" in multi["prompt"]
        assert stats.pending == stats.eligible

    def test_v2_replay_resume_roundtrip(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        from citation_verifier.executor import (
            RecordedExecutor, Verdict, append_verdict_jsonl,
            load_verdicts_jsonl)
        wd = _copy_withers(tmp_path)
        cassette = tmp_path / "v2.jsonl"
        with open(wd / "claims.csv", newline="", encoding="utf-8") as f:
            claims = [c for c in csv.DictReader(f) if pp._assessable(c)]
        for c in claims:
            append_verdict_jsonl(cassette, Verdict(
                claim_id=c["claim_id"],
                fields={"support": "supported", "badge_label": "Supported",
                        "brief_block": "", "opinion_block": "",
                        "finding_analysis": "fine"},
                model="opus", prompt_version="assess-v2"))
        stats = pp.run_assess(wd, executor=RecordedExecutor(cassette),
                              prompt_version="assess-v2")
        assert stats.done == stats.eligible and stats.pending == 0
        # v1 + v2 lines coexist in the workdir results file
        versions = {v.prompt_version for v in load_verdicts_jsonl(
            wd / "jobs" / "assess_results.jsonl")}
        assert versions == {"assess-v1", "assess-v2"}

    def test_v1_path_unchanged(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        wd = _copy_withers(tmp_path)
        (wd / "jobs" / "assess_results.jsonl").unlink()
        pp.run_assess(wd)  # default assess-v1
        jobs = json.loads((wd / "jobs" / "assess.json")
                          .read_text(encoding="utf-8"))
        assert all(len(j["claim_ids"]) == 1 for j in jobs)


class TestApplyAssessmentsV2:
    def _wd_with_v2(self, tmp_path, support, qcw="NO_QUOTES",
                    cl_status="VERIFIED", floor=""):
        from citation_verifier.executor import Verdict, append_verdict_jsonl
        wd = _report_workdir(tmp_path, [_report_row(
            "r-01", cl_status=cl_status, opinion_file="opinions/a.html",
            quote_check_worst=qcw, quote_floor=floor)])
        (wd / "jobs").mkdir()
        append_verdict_jsonl(
            wd / "jobs" / "assess_results.jsonl",
            Verdict(claim_id="r-01",
                    fields={"support": support, "badge_label": "B",
                            "brief_block": "bb", "opinion_block": "ob",
                            "finding_analysis": "fa"},
                    model="opus", prompt_version="assess-v2"))
        return wd

    @pytest.mark.parametrize("support,qcw,color", [
        ("supported", "NO_QUOTES", "Green"),
        # CLOSE with quote_floor unset = SS6.4 noise band -> no yellow
        # (the floor-effective verdict is the quote-axis input)
        ("supported", "CLOSE", "Green"),
        ("partial", "VERBATIM", "Yellow"),
        ("unsupported", "VERBATIM", "Red"),
        ("unverifiable", "NO_QUOTES", "Gray"),
    ])
    def test_color_derived_from_axes(self, tmp_path, support, qcw, color):
        import citation_verifier.proposition_pipeline as pp
        wd = self._wd_with_v2(tmp_path, support, qcw=qcw)
        stats = pp.run_apply_assessments(wd, prompt_version="assess-v2")
        assert stats.applied == 1
        (row,) = list(csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8")))
        assert row["assessment"] == color
        assert row["support"] == support
        assert row["badge_label"] == "B"
        assert row["brief_block"] == "bb"
        assert row["opinion_block"] == "ob"
        assert row["finding_analysis"] == "fa"
        assert row["assessed_by"] == "opus/assess-v2"

    def test_brief_block_defaults_from_brief_sentence_when_empty(
            self, tmp_path):
        """F3: an empty verdict brief_block defaults to the claim's
        brief_sentence (deterministic copy is more reliable than asking
        the agent to transcribe the brief's own language)."""
        import citation_verifier.proposition_pipeline as pp
        from citation_verifier.executor import Verdict, append_verdict_jsonl
        wd = _report_workdir(tmp_path, [_report_row(
            "r-01", cl_status="VERIFIED", opinion_file="opinions/a.html",
            quote_check_worst="NO_QUOTES",
            brief_sentence="See Nix, 475 U.S. at 160 (the standard).")])
        (wd / "jobs").mkdir()
        append_verdict_jsonl(
            wd / "jobs" / "assess_results.jsonl",
            Verdict(claim_id="r-01",
                    fields={"support": "supported", "badge_label": "",
                            "brief_block": "", "opinion_block": "",
                            "finding_analysis": "fa"},
                    model="opus", prompt_version="assess-v2"))
        pp.run_apply_assessments(wd, prompt_version="assess-v2")
        (row,) = list(csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8")))
        assert row["brief_block"] == "See Nix, 475 U.S. at 160 (the standard)."

    def test_nonempty_brief_block_not_overridden(self, tmp_path):
        """F3: a non-empty verdict brief_block is kept verbatim -- the
        brief_sentence default only fills an empty one."""
        import citation_verifier.proposition_pipeline as pp
        from citation_verifier.executor import Verdict, append_verdict_jsonl
        wd = _report_workdir(tmp_path, [_report_row(
            "r-01", cl_status="VERIFIED", opinion_file="opinions/a.html",
            quote_check_worst="NO_QUOTES",
            brief_sentence="fallback sentence")])
        (wd / "jobs").mkdir()
        append_verdict_jsonl(
            wd / "jobs" / "assess_results.jsonl",
            Verdict(claim_id="r-01",
                    fields={"support": "unsupported", "badge_label": "",
                            "brief_block": "agent-authored block",
                            "opinion_block": "", "finding_analysis": "fa"},
                    model="opus", prompt_version="assess-v2"))
        pp.run_apply_assessments(wd, prompt_version="assess-v2")
        (row,) = list(csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8")))
        assert row["brief_block"] == "agent-authored block"

    def test_quote_floor_still_guards(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        wd = self._wd_with_v2(tmp_path, "supported", qcw="FABRICATED",
                              floor="Yellow")
        pp.run_apply_assessments(wd, prompt_version="assess-v2")
        (row,) = list(csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8")))
        assert row["assessment"] == "Yellow"

    def test_close_in_noise_band_stays_green(self, tmp_path):
        """SS6.4 banded calibration (step-3): CLOSE in [0.75, 0.85) is
        transcription noise -- quote_floor is empty, so the quote axis
        must NOT yellow a supported claim (the withers-21 double-floor
        found at Step 8 acceptance). The axis input is the
        floor-effective verdict, not raw quote_check_worst."""
        import citation_verifier.proposition_pipeline as pp
        wd = self._wd_with_v2(tmp_path, "supported", qcw="CLOSE",
                              floor="")
        pp.run_apply_assessments(wd, prompt_version="assess-v2")
        (row,) = list(csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8")))
        assert row["assessment"] == "Green"

    def test_close_below_band_floors_to_yellow(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        wd = self._wd_with_v2(tmp_path, "supported", qcw="CLOSE",
                              floor="Yellow")
        pp.run_apply_assessments(wd, prompt_version="assess-v2")
        (row,) = list(csv.DictReader(
            (wd / "claims.csv").open(encoding="utf-8")))
        assert row["assessment"] == "Yellow"

    def test_invalid_support_rejected(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        wd = self._wd_with_v2(tmp_path, "kinda")
        stats = pp.run_apply_assessments(wd, prompt_version="assess-v2")
        assert stats.invalid == 1 and stats.applied == 0


class TestRunReport:
    def test_writes_report_and_stamps_run_json(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        wd = _report_workdir(tmp_path, [
            _report_row("r-01", cl_status="VERIFIED",
                        opinion_file="opinions/a.html", assessment="Green"),
            _report_row("r-02", cl_status="CITE_UNCONFIRMED",
                        opinion_file="opinions/a.html", assessment="Yellow"),
            _report_row("r-03", cl_status="NOT_FOUND"),
            _report_row("r-04", cl_status="VERIFIED",
                        opinion_file="opinions/a.html", assessment="Red",
                        finding_analysis="Bad."),
        ])
        (wd / "brief_metadata.json").write_text(
            json.dumps({"title": "My Test Brief",
                        "case_name": "Smith v. Jones"}),
            encoding="utf-8")
        stats = pp.run_report(wd)
        assert stats.path == wd / "report.html"
        assert (stats.findings, stats.check_cite,
                stats.verified, stats.unable) == (1, 1, 1, 1)
        html = stats.path.read_text(encoding="utf-8")
        assert "My Test Brief" in html
        run = json.loads((wd / "run.json").read_text(encoding="utf-8"))
        assert run["verbs"]["report"]["check_cite"] == 1

    def test_tolerates_missing_metadata(self, tmp_path):
        import citation_verifier.proposition_pipeline as pp
        wd = _report_workdir(tmp_path, [_report_row(
            "r-01", cl_status="VERIFIED",
            opinion_file="opinions/a.html", assessment="Green")])
        stats = pp.run_report(wd)
        assert stats.path.exists()
        assert stats.verified == 1


def test_resolver_captures_extracted_by_ocr(monkeypatch):
    from citation_verifier.client import CourtListenerClient

    client = CourtListenerClient(api_token="x")

    def fake_request(method, url, **kwargs):
        class R:
            def __init__(self, payload):
                self._p = payload
            def json(self):
                return self._p
        if "/clusters/" in url:
            return R({"sub_opinions": ["https://cl/api/opinions/9/"],
                      "case_name": "Demo v. Test", "citations": [], "docket": ""})
        if "/opinions/" in url:
            return R({"plain_text": "Some opinion body text.",
                      "extracted_by_ocr": True})
        return R({})

    monkeypatch.setattr(client, "_request_with_retry", fake_request)
    data = client.get_opinion_text_with_metadata(
        "https://www.courtlistener.com/opinion/123/demo-v-test/",
    )
    assert data is not None
    assert data["extracted_by_ocr"] is True


def test_ocr_manifest_roundtrip(tmp_path):
    from citation_verifier.proposition_pipeline import (
        _read_ocr_manifest, _write_ocr_manifest,
    )
    assert _read_ocr_manifest(tmp_path) == {}
    _write_ocr_manifest(tmp_path, {"A.html": True})
    _write_ocr_manifest(tmp_path, {"B.txt": None})  # merge, don't clobber
    m = _read_ocr_manifest(tmp_path)
    assert m["A.html"] is True
    assert "B.txt" in m
    assert (tmp_path / "opinions" / "ocr_status.json").exists()


def test_check_quotes_applies_ocr_gate(tmp_path):
    import csv as _csv
    import json as _json
    from citation_verifier.proposition_pipeline import (
        check_quotes, _write_ocr_manifest,
    )
    wd = tmp_path
    (wd / "opinions").mkdir()
    # Opinion text OCR'd "modem" as "modern":
    (wd / "opinions" / "Op.txt").write_text(
        "The court found the modern was defective and unusable here.",
        encoding="utf-8",
    )
    rows = [{
        "claim_id": "c-1",
        "proposition": "p",
        "brief_sentence": "",
        "quoted_text": _json.dumps(["the modem was defective"]),
        "opinion_file": "opinions/Op.txt",
    }]
    cols = list(rows[0].keys())
    with open(wd / "claims.csv", "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=cols)
        w.writeheader(); w.writerows(rows)

    # Without the gate -> not verbatim
    check_quotes(wd)
    with open(wd / "claims.csv", newline="", encoding="utf-8") as f:
        worst_off = list(_csv.DictReader(f))[0]["quote_check_worst"]
    assert worst_off != "VERBATIM"

    # Mark the opinion OCR'd, re-check -> verbatim
    _write_ocr_manifest(wd, {"Op.txt": True})
    check_quotes(wd)
    with open(wd / "claims.csv", newline="", encoding="utf-8") as f:
        worst_on = list(_csv.DictReader(f))[0]["quote_check_worst"]
    assert worst_on == "VERBATIM"
