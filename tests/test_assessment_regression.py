"""Offline regression: the frozen corpora + recorded cassettes must keep
reproducing the two committed acceptance baselines (design SS8).

  1. Withers: 14/19 yellows caught (8 exact), greens 9 exact / 2
     over-flagged, reds 1 Red + 2 Gray. History: the 2026-06-11
     measurement run scored 12/19 (cross-checked row-for-row against
     tests/data/withers_assessment_results.csv); the SS6.4 quote rules
     (>=2-word span extraction + quote_floor) added withers-09 (Am. Auto
     -- the 2-word "judicial admissions" quote) and withers-38
     (Anderson).
  2. A/B opus baseline (ab_opus-baseline_20260323-002228.jsonl) scored
     against the CURRENT ab_test_cases.json ledger: payne 24/27,
     wainwright 33/34 -> 57/61 (93.4%, >= the 85% target). Note: the
     recording's own `correct` flags say payne 21/27 -- two payne cases
     (ids 16, 75) had expected_assessment revised in the ledger after the
     recording, both to agree with the model's answer. The ledger is
     authoritative (design SS7: one scoring path).

RECALIBRATED 2026-09-22 (quote matcher structural buckets,
docs/plans/2026-09-22-quote-matcher-structural-buckets.md). Same totals,
different composition -- the four moves are each traceable to one row:
  * payne-58/-59/-12/-14/-15/-79: the payne corpus carried quote columns
    frozen from the source brief before `quote_floor` existed, so their
    FABRICATED quotes never floored. The builder now re-runs check_quotes
    on every corpus. payne 23 -> 24 and the pinned lenient error
    payne-58 (Yellow predicted Green) disappears.
  * wainwright-17 and withers-21 hold their old values. Both are CLOSE
    for exactly one function word -- "the" inserted, "or" for "and" --
    and `_quote_floor` exempts a lone altered word, which is the old
    [0.75, 0.85) band restated structurally instead of as a threshold
    that the matcher's 0.80 ceiling had made meaningless. A CLOSE
    touching two or more words floors.

Net vs. the pre-2026-09-22 numbers: A/B v1 56 -> 57/61 and the lenient
set halves; everything else holds.

No network, no LLM: RecordedExecutor replay only. A prompt-template change
bumps the version key and makes these tests fail loudly via
RecordedVerdictMiss -- that is the cassette policy working as intended:
re-record live, update the numbers deliberately.
"""
from pathlib import Path

from citation_verifier.scoring import score_workdir

CORPORA = Path(__file__).parent / "data" / "assessment_corpora"

_RANK = {"Green": 0, "Yellow": 1, "Red": 2}


class TestWithersBaseline:
    def test_reproduces_assessment_baseline(self):
        s = score_workdir(CORPORA / "withers")
        assert s.yellows_total == 19
        assert s.yellows_caught == 14   # 12 measured + SS6.4 floors (+2)
        assert s.yellows_exact == 8
        assert s.greens_total == 12
        assert s.greens_exact == 9
        assert s.greens_overflagged == 2
        assert s.reds_total == 3
        assert s.reds_caught == 3  # 1 Red via WRONG_CASE + 2 Gray

    def test_known_misses_are_stable(self):
        """The remaining 5: -12 has no quotation marks in its proposition
        (not mechanically catchable); the rest are the judgment-call /
        author-hedged band (design SS1)."""
        s = score_workdir(CORPORA / "withers")
        missed = sorted(r["claim_id"] for r in s.rows
                        if r["expected"] == "yellow" and not r["correct"])
        assert missed == ["withers-05", "withers-12", "withers-32",
                          "withers-44", "withers-49"]


class TestABOpusBaseline:
    def test_payne(self):
        s = score_workdir(CORPORA / "payne")
        assert (s.correct, s.total) == (24, 27)

    def test_wainwright(self):
        s = score_workdir(CORPORA / "wainwright")
        assert (s.correct, s.total) == (33, 34)

    def test_lenient_direction_errors_pinned(self):
        """SS8.2 target: no NEW lenient-direction (Red->Yellow->Green)
        errors vs. the recorded runs. The recorded opus baseline
        contained two; payne-58's FABRICATED quote now floors it (the
        payne corpus's quote columns were stale), leaving one."""
        lenient = []
        for name in ("payne", "wainwright"):
            s = score_workdir(CORPORA / name)
            for r in s.rows:
                if (not r["correct"]
                        and _RANK[r["predicted"]] < _RANK[r["expected"]]):
                    lenient.append(
                        (r["claim_id"], r["expected"], r["predicted"]))
        assert sorted(lenient) == [("payne-03", "Red", "Yellow")]


class TestAssessV2Baselines:
    """The 2026-06-12 assess-v2 re-record (Step 8, 9.3/9.4): two-axis +
    report-block verdicts, per-opinion packed jobs, all-Opus; color
    derived via derive_color with the floor-effective quote axis.

    SS8 scorecard vs targets: yellows 16/19 (>=15 PASS, was 14), reds
    3/3 (PASS), A/B 55/61 = 90% (>=85% PASS), lenient set {payne-03}
    (subset of the pinned v1 set, PASS) -- and green over-flags 4/12 vs
    the <=2 guardrail. **Guardrail miss ADJUDICATED AND ACCEPTED (user,
    2026-06-12):** reviewing all four agent rationales against the
    rows, the user agreed with the agent on every one -- -01 (Nix is a
    candor case, not conflicts), -20 (the cited case is an exception to
    the rule it's cited for), -26 (the exhibit author herself hedged
    it), and -30 (Rice: the proposition's lead clause is the brief's
    own rhetoric, not stated in the case; "partial" states that
    honestly). The exhibit greens on these rows are the disputed
    labels; the guardrail measures agreement with the exhibit, not
    correctness. v2 acceptance APPROVED.
    """

    def test_withers_v2(self):
        s = score_workdir(CORPORA / "withers",
                          prompt_version="assess-v2")
        assert s.yellows_total == 19
        assert s.yellows_caught == 16
        assert s.yellows_exact == 11
        assert s.greens_total == 12
        assert s.greens_exact == 7
        assert s.greens_overflagged == 4
        assert s.reds_total == 3
        assert s.reds_caught == 3

    def test_withers_v2_misses_pinned(self):
        s = score_workdir(CORPORA / "withers",
                          prompt_version="assess-v2")
        missed = sorted(r["claim_id"] for r in s.rows
                        if r["expected"] == "yellow" and not r["correct"])
        assert missed == ["withers-32", "withers-33", "withers-49"]

    def test_payne_v2(self):
        s = score_workdir(CORPORA / "payne", prompt_version="assess-v2")
        assert (s.correct, s.total) == (23, 27)

    def test_wainwright_v2(self):
        s = score_workdir(CORPORA / "wainwright",
                          prompt_version="assess-v2")
        assert (s.correct, s.total) == (32, 34)

    def test_v2_lenient_set_shrinks(self):
        """v2's only lenient-direction miss is payne-03 -- a strict
        subset of the pinned v1 set (payne-58 fixed). SS8.2 holds."""
        lenient = []
        for name in ("payne", "wainwright"):
            s = score_workdir(CORPORA / name, prompt_version="assess-v2")
            for r in s.rows:
                if (not r["correct"]
                        and _RANK[r["predicted"]] < _RANK[r["expected"]]):
                    lenient.append(
                        (r["claim_id"], r["expected"], r["predicted"]))
        assert sorted(lenient) == [("payne-03", "Red", "Yellow")]
