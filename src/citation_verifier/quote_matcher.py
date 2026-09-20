"""Quote-fidelity matching primitives.

Public surface: `verify_quote`, `QuoteVerification`, `QuoteMatch` (added in
later tasks). The module also houses the legal-quote normalization and the
fuzzy best-match helpers, previously private to proposition_pipeline.
"""
from __future__ import annotations

import difflib
import enum
import re
from dataclasses import dataclass

# --- Quote text normalization (moved verbatim from proposition_pipeline) ---


def _straighten_quotes(text: str) -> str:
    """Smart quotes -> straight. 1:1 per character, so offsets are preserved."""
    s = text.replace("“", '"').replace("”", '"')
    return s.replace("‘", "'").replace("’", "'")


def _normalize_quote_text(text: str) -> str:
    """Normalize quoted text for fuzzy matching.

    Strips bracketed alterations, ellipses, smart quotes, and excess whitespace.
    """
    # Smart quotes to straight
    s = _straighten_quotes(text)
    # Strip bracketed alterations: [T] -> t (lowercase), [word] -> ""
    s = re.sub(r"\[([A-Z])\]", lambda m: m.group(1).lower(), s)
    s = re.sub(r"\[[^\]]*\]", "", s)
    # Strip ellipses
    s = s.replace("…", " ")  # unicode ellipsis
    s = re.sub(r"\.{3,}", " ", s)  # three+ dots
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


# --- OCR-confusion normalization (borrowed from lq-ai, conservative) ---
# One-directional substitutions applied to BOTH the quote and the opinion text
# when the opinion was OCR'd, so faithful quotes against OCR'd serif PDFs stop
# false-negativing. Case-sensitive O/l rules => must run BEFORE any .lower().
_OCR_RN_RE = re.compile(r"(?<=\w)rn")
_OCR_O_RE = re.compile(r"(?<=\d)O|O(?=\d)")
_OCR_L_RE = re.compile(r"(?<=\d)l|l(?=\d)")


def _normalize_ocr_confusions(text: str) -> str:
    """Collapse the three canonical OCR misreads. Idempotent; clean-text no-op."""
    out = _OCR_RN_RE.sub("m", text)
    out = _OCR_O_RE.sub("0", out)
    out = _OCR_L_RE.sub("1", out)
    return out


def _best_match_with_passage(
    needle: str, haystack: str, context_chars: int = 80, *, ocr: bool = False,
) -> tuple[float, str]:
    """Find the best fuzzy match ratio and extract the matching passage.

    When ``ocr`` is True, both the needle and a COPY of the haystack are
    OCR-normalized (before lowercasing) for the comparison; the displayed
    passage is still sliced from the ORIGINAL haystack. Because ``rn``->``m``
    shortens text, the sliced position can drift by the number of collapses
    before the match — bounded, cosmetic, and never affects the returned ratio
    (the verdict). When ``ocr`` is False this is byte-for-byte the old path.
    """
    if not needle or not haystack:
        return 0.0, ""

    needle_norm = _normalize_quote_text(needle)
    if ocr:
        needle_norm = _normalize_ocr_confusions(needle_norm)
    needle_norm = needle_norm.lower()

    # The haystack gets the same smart-quote straightening as the needle
    # (length-preserving, so the passage slice below stays aligned). Without
    # it a quote with an apostrophe never exact-matches a curly-quote opinion.
    haystack_cmp = _straighten_quotes(haystack)
    if ocr:
        haystack_cmp = _normalize_ocr_confusions(haystack_cmp).lower()
    else:
        haystack_cmp = haystack_cmp.lower()

    if not needle_norm:
        return 0.0, ""

    # Exact substring = verbatim
    if needle_norm in haystack_cmp:
        pos = haystack_cmp.index(needle_norm)
        pos = min(pos, len(haystack))  # guard against length drift
        return 1.0, _extract_passage(haystack, pos, len(needle_norm), context_chars)

    # Sliding window with fine step
    best = 0.0
    best_start = 0
    window = len(needle_norm)
    step = max(1, window // 8)
    for start in range(0, max(1, len(haystack_cmp) - window + 1), step):
        chunk = haystack_cmp[start:start + window + window // 2]
        ratio = difflib.SequenceMatcher(
            None, needle_norm, chunk, autojunk=False,
        ).ratio()
        if ratio > best:
            best = ratio
            best_start = start
            if best > 0.95:
                break

    passage = ""
    if best >= 0.4:
        best_start = min(best_start, len(haystack))  # guard against drift
        passage = _extract_passage(
            haystack, best_start, window + window // 2, context_chars,
        )
    return best, passage


# --- Public quote primitive ---

# Bucketing thresholds (unchanged from check_quotes). VERBATIM is strictly
# above _VERBATIM_MIN; CLOSE is at or above _CLOSE_MIN; else FABRICATED.
_VERBATIM_MIN = 0.85
_CLOSE_MIN = 0.6


class QuoteMatch(str, enum.Enum):
    """How well a quote matched the opinion text."""
    VERBATIM = "VERBATIM"
    CLOSE = "CLOSE"
    FABRICATED = "FABRICATED"


@dataclass(frozen=True)
class QuoteVerification:
    """Result of verifying one quote against one opinion's text."""
    quote: str              # the RAW input quote, echoed verbatim
    result: QuoteMatch
    similarity: float       # 0.0-1.0 (difflib ratio; 1.0 = exact substring)
    matched_passage: str    # best-matching span from opinion_text ("" if none)
    was_ocrd: bool          # whether OCR-confusion rules were applied


def verify_quote(
    quote: str, opinion_text: str, *, was_ocrd: bool = False,
) -> QuoteVerification:
    """Verify a quote against opinion text. Public primitive.

    Applies CV's legal-quote normalization always, and the conservative
    OCR-confusion rules only when ``was_ocrd`` is True. Buckets the fuzzy ratio
    into VERBATIM/CLOSE/FABRICATED on the existing thresholds.
    """
    ratio, passage = _best_match_with_passage(quote, opinion_text, ocr=was_ocrd)
    if ratio > _VERBATIM_MIN:
        result = QuoteMatch.VERBATIM
    elif ratio >= _CLOSE_MIN:
        result = QuoteMatch.CLOSE
    else:
        result = QuoteMatch.FABRICATED
    return QuoteVerification(
        quote=quote,
        result=result,
        similarity=round(ratio, 2),
        matched_passage=passage,
        was_ocrd=was_ocrd,
    )


def _extract_passage(
    text: str, match_start: int, match_len: int, context: int,
) -> str:
    """Extract a passage from text around a match, trimmed to sentences."""
    start = max(0, match_start - context)
    end = min(len(text), match_start + match_len + context)
    passage = text[start:end].strip()
    # Trim leading partial sentence
    if start > 0:
        dot = passage.find(". ")
        if 0 < dot < context:
            passage = passage[dot + 2:]
    # Trim trailing partial sentence
    if end < len(text):
        dot = passage.rfind(". ")
        if dot > len(passage) - context and dot > 0:
            passage = passage[:dot + 1]
    return passage.strip()
