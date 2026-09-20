from citation_verifier.quote_matcher import (
    QuoteMatch,
    QuoteVerification,
    _best_match_with_passage,
    _normalize_ocr_confusions,
    verify_quote,
)


class TestNormalizeOcrConfusions:
    def test_rn_to_m_midword(self):
        assert _normalize_ocr_confusions("modern") == "modem"
        assert _normalize_ocr_confusions("concern") == "concem"

    def test_rn_not_word_initial(self):
        assert _normalize_ocr_confusions("rnage") == "rnage"

    def test_O_to_0_only_digit_adjacent(self):
        assert _normalize_ocr_confusions("O5") == "05"
        assert _normalize_ocr_confusions("5O") == "50"
        assert _normalize_ocr_confusions("Office") == "Office"

    def test_l_to_1_only_digit_adjacent(self):
        assert _normalize_ocr_confusions("l5") == "15"
        assert _normalize_ocr_confusions("5l") == "51"
        assert _normalize_ocr_confusions("liability") == "liability"

    def test_idempotent(self):
        for s in ["modern attorney", "O5 l5 5O", "no confusions here", "concern"]:
            once = _normalize_ocr_confusions(s)
            assert _normalize_ocr_confusions(once) == once

    def test_clean_text_with_no_gated_patterns_is_unchanged(self):
        s = "The court held that summary judgment was proper."
        assert _normalize_ocr_confusions(s) == s


class TestBestMatchOcr:
    def test_ocr_false_is_unchanged_default(self):
        # "modem" (true) vs an opinion that has the OCR'd "modern": no exact hit
        r_off, _ = _best_match_with_passage("modem device", "the modern device works")
        assert r_off < 1.0

    def test_ocr_true_collapses_to_verbatim(self):
        # Opinion text OCR'd "m" as "rn"; quote has the true "m"
        ratio, passage = _best_match_with_passage(
            "the modem device", "before the modern device after", ocr=True,
        )
        assert ratio == 1.0
        assert passage  # non-empty, sliced from the original haystack
        assert "device" in passage

    def test_ocr_true_clean_text_same_ratio_as_off(self):
        q = "summary judgment was proper"
        h = "the court held that summary judgment was proper here"
        on, _ = _best_match_with_passage(q, h, ocr=True)
        off, _ = _best_match_with_passage(q, h, ocr=False)
        assert on == off == 1.0

    def test_ocr_passage_in_bounds_with_collapses_before_match(self):
        # Many rn-words before the match must not throw / must stay in-bounds.
        prefix = "return concern attorney govern modern " * 20
        h = prefix + "the quoted phrase here"
        ratio, passage = _best_match_with_passage(
            "the quoted phrase here", h, ocr=True,
        )
        assert ratio == 1.0
        assert isinstance(passage, str) and passage != ""


class TestHaystackSmartQuotes:
    """The needle always had smart quotes straightened; the opinion text did
    not, so any quote with an apostrophe missed the exact-substring path
    against a curly-quote opinion (found 2026-09-19: 36 of 106 recorded
    opinion_block segments)."""

    def test_curly_apostrophe_in_opinion_is_exact(self):
        opinion = "We hold that counsel’s performance was not deficient here."
        qv = verify_quote("counsel's performance was not deficient", opinion)
        assert qv.similarity == 1.0
        assert qv.result is QuoteMatch.VERBATIM

    def test_curly_double_quotes_in_opinion_are_exact(self):
        opinion = "The phrase “in his presence” is construed broadly by courts."
        ratio, passage = _best_match_with_passage(
            'phrase "in his presence" is construed', opinion)
        assert ratio == 1.0
        # displayed passage is sliced from the ORIGINAL (curly) opinion text
        assert "“in his presence”" in passage

    def test_straightening_never_rescues_a_real_misquote(self):
        opinion = "We hold that counsel’s performance was not deficient here."
        qv = verify_quote("counsel's performance was plainly deficient", opinion)
        assert qv.similarity < 1.0


class TestVerifyQuoteContract:
    def test_returns_quoteverification_with_enum_result(self):
        qv = verify_quote("hello world", "well, hello world!")
        assert isinstance(qv, QuoteVerification)
        assert qv.result is QuoteMatch.VERBATIM
        assert isinstance(qv.result, QuoteMatch)

    def test_quotematch_is_str_enum(self):
        assert issubclass(QuoteMatch, str)
        assert QuoteMatch.VERBATIM.value == "VERBATIM"

    def test_echoes_raw_input_quote(self):
        raw = "[T]he  court “held”"
        qv = verify_quote(raw, "irrelevant text")
        assert qv.quote == raw  # raw, NOT normalized

    def test_was_ocrd_echoed_and_defaults_false(self):
        assert verify_quote("a phrase", "x").was_ocrd is False
        assert verify_quote("a phrase", "x", was_ocrd=True).was_ocrd is True

    def test_similarity_in_unit_range(self):
        qv = verify_quote("totally absent phrase zzz", "unrelated opinion text")
        assert 0.0 <= qv.similarity <= 1.0

    def test_no_verbatim_attribute(self):
        assert not hasattr(verify_quote("a", "a"), "verbatim")

    def test_buckets(self):
        assert verify_quote("hello world", "say hello world now").result is QuoteMatch.VERBATIM
        assert verify_quote("zzz qqq vvv", "nothing alike here").result is QuoteMatch.FABRICATED


class TestVerifyQuoteOcr:
    def test_ocr_fixes_false_negative(self):
        # opinion OCR'd "modem" as "modern"; quote has the true "modem".
        # The single rn->m collapse must drop the no-OCR ratio below the
        # VERBATIM cut (so off != VERBATIM) yet restore an exact match on.
        opinion = "The parties used the modern to connect."
        quote = "used the modem"
        off = verify_quote(quote, opinion, was_ocrd=False)
        on = verify_quote(quote, opinion, was_ocrd=True)
        assert on.result is QuoteMatch.VERBATIM
        assert off.result is not QuoteMatch.VERBATIM


def test_public_exports():
    import citation_verifier as cv
    assert hasattr(cv, "verify_quote")
    assert hasattr(cv, "QuoteVerification")
    assert hasattr(cv, "QuoteMatch")
    assert "verify_quote" in cv.__all__
    assert "QuoteVerification" in cv.__all__
    assert "QuoteMatch" in cv.__all__
