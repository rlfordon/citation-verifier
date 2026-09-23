"""Quote-fidelity matching primitives.

Public surface: `verify_quote`, `QuoteVerification`, `QuoteMatch`. The module
also houses the legal-quote normalization and the fuzzy best-match helpers,
previously private to proposition_pipeline.

Bucketing is **structural**, not a ratio cut (2026-09-22). The matcher aligns
the quote to its best-matching span of the opinion, then asks *what differs*:
if only typographic junk differs (star pagination, footnote markers,
punctuation, quote marks, hyphenation, and material the quoter openly elided
with an ellipsis or replaced with a bracketed alteration) the quote is
VERBATIM; if a word is substituted, added or dropped it is CLOSE; if the
alignment is poor it is FABRICATED. See `scratch/TODO.md` (2026-09-19) for the
0.80-ceiling bug this replaces and
`docs/plans/2026-09-22-quote-matcher-structural-buckets.md` for the
calibration.
"""
from __future__ import annotations

import difflib
import enum
import re
from dataclasses import dataclass, field

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
    # [T]he -> the, [t]rial -> trial. The letter must be glued to the word it
    # recapitalizes; a standalone [a] is a word substitution, stripped below.
    s = re.sub(r"\[([A-Za-z])\](?=\w)", lambda m: m.group(1).lower(), s)
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


# --- Alignment ---------------------------------------------------------------
# The old implementation compared a length-w quote against a 1.5w chunk, so
# SequenceMatcher.ratio() could not exceed 2w/2.5w = 0.80 and VERBATIM was
# reachable only by exact substring. The oversized chunk is still the right
# tool for *locating* a candidate (it tolerates insertions), but the ratio has
# to be recomputed against the span the quote actually aligns to.

_LOCATE_CANDIDATES = 6
_MIN_ANCHOR_BLOCK = 4


def _locate(needle: str, haystack: str) -> list[int]:
    """Coarse scan: the most promising window starts, best first."""
    w = len(needle)
    step = max(1, w // 8)
    scored: list[tuple[float, int]] = []
    for start in range(0, max(1, len(haystack) - w + 1), step):
        chunk = haystack[start:start + w + w // 2]
        ratio = difflib.SequenceMatcher(
            None, needle, chunk, autojunk=False,
        ).ratio()
        scored.append((ratio, -start))
    scored.sort(reverse=True)
    return [-s for _, s in scored[:_LOCATE_CANDIDATES]]


def _aligned_span(
    needle: str, haystack: str, start: int,
) -> tuple[float, int, int]:
    """Align `needle` inside a generous chunk at `start`; return the span.

    The span is trimmed to the outermost anchor blocks and then extended by
    however much of the needle hangs off each end, so a quote whose first or
    last words are absent is compared against real opinion text rather than
    against nothing. Returns (ratio, span_start, span_end) in haystack coords.
    """
    w = len(needle)
    lo = max(0, start - w // 4)
    chunk = haystack[lo:start + w + w // 2]
    if not chunk:
        return 0.0, 0, 0
    sm = difflib.SequenceMatcher(None, needle, chunk, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks()
              if b.size >= _MIN_ANCHOR_BLOCK]
    if not blocks:
        blocks = [b for b in sm.get_matching_blocks() if b.size > 0]
    if not blocks:
        return 0.0, 0, 0
    head, tail = blocks[0], blocks[-1]
    b0 = max(0, head.b - head.a)
    b1 = min(len(chunk), tail.b + tail.size + (w - tail.a - tail.size))
    span = chunk[b0:b1]
    ratio = difflib.SequenceMatcher(None, needle, span, autojunk=False).ratio()
    return ratio, lo + b0, lo + b1


_MAX_BRACKET = 40


def _snap_to_words(text: str, start: int, end: int) -> tuple[int, int]:
    """Widen [start, end) to whole words and whole bracket groups.

    A span cut mid-word turns an opinion word into a nonsense fragment, which
    the word-level diff then reports as a substitution; a span cut through the
    opinion's own bracketed alteration ("[A] plaintiff's obligation") hides the
    bracket that licenses it. Only a *split* token is completed -- an edge
    already sitting on a boundary never reaches out and takes the neighbour.
    """
    while 0 < start < len(text) and text[start - 1].isalnum() \
            and text[start].isalnum():
        start -= 1
    while 0 < end < len(text) and text[end - 1].isalnum() \
            and text[end].isalnum():
        end += 1
    # A "]" ahead of any "[" means the start edge split a bracket group.
    close = text.find("]", start, min(end, start + _MAX_BRACKET))
    if close >= 0 and text.find("[", start, close) < 0:
        opened = text.rfind("[", max(0, start - _MAX_BRACKET), start)
        if opened >= 0:
            start = opened
    # ...and a "[" after the last "]" means the end edge did.
    opened = text.rfind("[", max(start, end - _MAX_BRACKET), end)
    if opened >= 0 and text.find("]", opened, end) < 0:
        close = text.find("]", end, end + _MAX_BRACKET)
        if close >= 0:
            end = close + 1
    return start, end


def _best_alignment(
    needle: str, haystack: str, *, ocr: bool = False,
) -> tuple[float, int, int, str, str]:
    """Best alignment of `needle` in `haystack`.

    Returns (char_ratio, span_start, span_end, normalized_needle, span_text).
    The offsets are in ORIGINAL-haystack coordinates, for slicing the passage
    to display; `span_text` is the same span in COMPARISON coordinates, which
    is what any further comparison must use -- OCR normalization shortens the
    text, so the two drift apart. `char_ratio` is the uncapped SequenceMatcher
    ratio of the normalized needle against the aligned span.
    """
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

    if not needle_norm or not haystack_cmp:
        return 0.0, 0, 0, needle_norm, ""

    # Exact substring = verbatim
    if needle_norm in haystack_cmp:
        pos = haystack_cmp.index(needle_norm)
        a, b = _snap_to_words(haystack_cmp, pos, pos + len(needle_norm))
        return (1.0, min(a, len(haystack)), min(b, len(haystack)),
                needle_norm, haystack_cmp[a:b])

    best = (0.0, 0, 0)
    for start in _locate(needle_norm, haystack_cmp):
        ratio, a, b = _aligned_span(needle_norm, haystack_cmp, start)
        if ratio > best[0]:
            best = (ratio, a, b)
    _, a, b = best
    # Snap after ranking, against the whole haystack: the per-candidate chunk
    # can end mid-word, so snapping inside _aligned_span cannot reach past it.
    a, b = _snap_to_words(haystack_cmp, a, b)
    span = haystack_cmp[a:b]
    ratio = difflib.SequenceMatcher(
        None, needle_norm, span, autojunk=False,
    ).ratio() if b > a else 0.0
    # Guard against OCR length drift between haystack_cmp and haystack.
    return (ratio, min(a, len(haystack)), min(b, len(haystack)),
            needle_norm, span)


def _best_match_with_passage(
    needle: str, haystack: str, context_chars: int = 80, *, ocr: bool = False,
) -> tuple[float, str]:
    """Find the best fuzzy match ratio and extract the matching passage.

    Kept for compatibility (re-exported from `proposition_pipeline`). The ratio
    is the uncapped alignment ratio; `verify_quote` is the bucketing entry
    point.

    When ``ocr`` is True, both the needle and a COPY of the haystack are
    OCR-normalized (before lowercasing) for the comparison; the displayed
    passage is still sliced from the ORIGINAL haystack. Because ``rn``->``m``
    shortens text, the sliced position can drift by the number of collapses
    before the match -- bounded, cosmetic, and never affects the returned ratio.
    """
    if not needle or not haystack:
        return 0.0, ""
    ratio, a, b, _, _ = _best_alignment(needle, haystack, ocr=ocr)
    passage = ""
    if ratio >= 0.4 and b > a:
        passage = _extract_passage(haystack, a, b - a, context_chars)
    return ratio, passage


# --- Structural diff: what differs after alignment ---------------------------
# A "gap" marks material the quoter openly declared missing or altered: an
# ellipsis, or a bracketed alteration. Span text that lands at a gap is the
# quoter's disclosed edit, not a misquote. Everything else stripped here is
# typographic junk that neither side means to carry meaning.
#
# NB: this runs on the RAW quote, not on `_normalize_quote_text`'s output --
# that function deletes the ellipses and brackets, which are exactly the
# markers the licensing depends on.

_GAP_ELLIPSIS = "\x00"
_GAP_BRACKET = "\x01"
_GAPS = (_GAP_ELLIPSIS, _GAP_BRACKET)

# How much opinion text a disclosed gap may cover. An ellipsis stands for a
# passage, so it is generous; a bracketed alteration stands for a word or two,
# and is capped so `[is]` cannot silently swallow "is not liable because ...".
_GAP_SPAN_LIMIT = {_GAP_ELLIPSIS: 60, _GAP_BRACKET: 4}

_STAR_PAGE_RE = re.compile(r"\*\s?\d+")          # *534 star pagination
_ELLIPSIS_RE = re.compile(r"(?:\.\s*){3,}|…")   # ... / . . . / ellipsis
_BRACKET_LETTER_RE = re.compile(r"\[([a-z])\](?=[a-z0-9])")  # [t]rial -> trial
_BRACKET_RE = re.compile(r"\[[^\]]*\]")          # [ment], [a] -> gap
_HYPHEN_JOIN_RE = re.compile(r"(?<=\w)-\s*(?=\w)")  # non-moving == nonmoving
# CL's extraction drops possessive apostrophes in places ("[A] plaintiffs
# obligation" in Twombly); joining across one keeps the token streams aligned.
_APOSTROPHE_JOIN_RE = re.compile(r"(?<=\w)'(?=\w)")
_TOKEN_RE = re.compile(r"[\x00\x01]|[a-z0-9]+")

# A bare one-or-two-digit token that appears only on the opinion side is a
# footnote marker ("disbarred 8 does not"), not a word of the quotation.
_FOOTNOTE_RE = re.compile(r"^\d{1,2}$")


def _diff_tokens(text: str) -> list[str]:
    """Comparable word tokens, with junk removed and gaps marked."""
    s = _straighten_quotes(text).lower()
    s = _STAR_PAGE_RE.sub(" ", s)
    s = _ELLIPSIS_RE.sub(" %s " % _GAP_ELLIPSIS, s)
    s = _BRACKET_LETTER_RE.sub(r"\1", s)
    s = _BRACKET_RE.sub(" %s " % _GAP_BRACKET, s)
    s = _HYPHEN_JOIN_RE.sub("", s)
    s = _APOSTROPHE_JOIN_RE.sub("", s)
    return _TOKEN_RE.findall(s)


def _split_gaps(tokens: list[str]) -> tuple[list[str], dict[int, str]]:
    """Drop gap markers, returning the word tokens and the gaps by position."""
    words: list[str] = []
    gaps: dict[int, str] = {}
    for tok in tokens:
        if tok in _GAPS:
            gaps.setdefault(len(words), tok)
        else:
            words.append(tok)
    return words, gaps


def _all_footnotes(tokens: list[str]) -> bool:
    return bool(tokens) and all(_FOOTNOTE_RE.match(t) for t in tokens)


def _covered(gaps: dict[int, str], pos: int, run: list[str]) -> bool:
    """Does a gap sit at `pos` and honestly stand for `run`?"""
    return any(abs(g - pos) <= 1 and len(run) <= _GAP_SPAN_LIMIT[kind]
               for g, kind in gaps.items())


def _is_licensed(tag: str, i1: int, j1: int, dropped: list[str],
                 added: list[str], n_gaps: dict[int, str],
                 s_gaps: dict[int, str], n_len: int) -> bool:
    """Is this opcode a disclosed edit or typographic junk, not a misquote?

    Only one-sided opcodes are licensed. An insertion is licensed when it sits
    at an edge of the span (that boundary is the matcher's choice, not the
    quoter's -- every quotation is an excerpt), at a gap in the quote (the
    quoter elided or bracketed what the opinion has there), or on a footnote
    marker. A deletion is licensed by a gap in the *opinion*, whose reported
    text may carry its own bracketed alteration -- as Iqbal does quoting
    Twombly's "[A] plaintiff's obligation". A substitution is a real
    alteration however either side is punctuated.
    """
    if tag == "insert":
        if i1 == 0 or i1 >= n_len:
            return True  # span overhang before/after the quoted words
        return _covered(n_gaps, i1, added) or _all_footnotes(added)
    if tag == "delete":
        return _covered(s_gaps, j1, dropped)
    return False


def _word_opcodes(needle_raw: str, span_raw: str):
    """(needle words, span words, both gap maps, opcodes) for the word diff."""
    n_words, n_gaps = _split_gaps(_diff_tokens(needle_raw))
    s_words, s_gaps = _split_gaps(_diff_tokens(span_raw))
    sm = difflib.SequenceMatcher(None, n_words, s_words, autojunk=False)
    return n_words, s_words, n_gaps, s_gaps, sm.get_opcodes()


def _describe(needle_words: list[str], span_words: list[str]) -> str:
    if needle_words and span_words:
        return "%s -> %s" % (" ".join(needle_words), " ".join(span_words))
    if needle_words:
        return "dropped: %s" % " ".join(needle_words)
    return "added: %s" % " ".join(span_words)


def _word_alterations(needle_raw: str, span_raw: str) -> tuple[str, ...]:
    """Word-level differences that are NOT typographic or disclosed junk."""
    n_words, s_words, n_gaps, s_gaps, opcodes = _word_opcodes(
        needle_raw, span_raw)
    if not n_words:
        return ()
    alterations = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            continue
        dropped, added = n_words[i1:i2], s_words[j1:j2]
        if _is_licensed(tag, i1, j1, dropped, added, n_gaps, s_gaps,
                        len(n_words)):
            continue
        alterations.append(_describe(dropped, added))
    return tuple(alterations)


def _content_similarity(needle_raw: str, span_raw: str) -> float:
    """Word-content similarity: junk and disclosed gaps cost nothing.

    Both sides are reduced to their comparable word tokens (with the licensed
    gap material removed from the span) and compared as text, so a quote that
    differs only in pagination, punctuation or an elided passage scores 1.0
    while a substituted word costs in proportion to its length.
    """
    n_words, s_words, n_gaps, s_gaps, opcodes = _word_opcodes(
        needle_raw, span_raw)
    if not n_words:
        return 0.0
    kept: list[str] = []
    for tag, i1, i2, j1, j2 in opcodes:
        chunk = s_words[j1:j2]
        if tag != "equal" and _is_licensed(
                tag, i1, j1, n_words[i1:i2], chunk, n_gaps, s_gaps,
                len(n_words)):
            # A licensed deletion keeps the quote's own words, so the two
            # streams stay comparable; a licensed insertion drops the
            # opinion's extra words.
            if tag == "delete":
                kept.extend(n_words[i1:i2])
            continue
        kept.extend(chunk)
    return difflib.SequenceMatcher(
        None, " ".join(n_words), " ".join(kept), autojunk=False,
    ).ratio()


# --- Public quote primitive ---

# A quote whose alignment is this poor is not in the opinion at all. Calibrated
# 2026-09-22 over the three frozen corpora + matters/aliaj-rochelle-park: real
# fabrications align at <= 0.52, the worst genuine alteration at 0.69.
_FABRICATED_MAX = 0.6


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
    similarity: float       # 0.0-1.0 word-content similarity (1.0 = VERBATIM)
    matched_passage: str    # best-matching span from opinion_text ("" if none)
    was_ocrd: bool          # whether OCR-confusion rules were applied
    alterations: tuple[str, ...] = field(default=())  # the non-junk word diffs


def verify_quote(
    quote: str, opinion_text: str, *, was_ocrd: bool = False,
) -> QuoteVerification:
    """Verify a quote against opinion text. Public primitive.

    Applies CV's legal-quote normalization always, and the conservative
    OCR-confusion rules only when ``was_ocrd`` is True. Buckets on *what*
    differs between the quote and the span it aligns to, not on a ratio cut:
    junk-only differences are VERBATIM, any altered word is CLOSE, and a quote
    that does not align at all is FABRICATED.
    """
    if not quote or not opinion_text:
        return QuoteVerification(
            quote=quote, result=QuoteMatch.FABRICATED, similarity=0.0,
            matched_passage="", was_ocrd=was_ocrd, alterations=(),
        )

    align_ratio, a, b, _, span = _best_alignment(
        quote, opinion_text, ocr=was_ocrd)
    passage = ""
    if align_ratio >= 0.4 and b > a:
        passage = _extract_passage(opinion_text, a, b - a, 80)

    if align_ratio < _FABRICATED_MAX:
        return QuoteVerification(
            quote=quote, result=QuoteMatch.FABRICATED,
            similarity=round(align_ratio, 2), matched_passage=passage,
            was_ocrd=was_ocrd, alterations=(),
        )

    # The structural diff sees the RAW quote: its ellipses and brackets are the
    # markers that license the opinion's extra words.
    needle = _normalize_ocr_confusions(quote) if was_ocrd else quote
    alterations = _word_alterations(needle, span)
    similarity = (1.0 if not alterations
                  else _content_similarity(needle, span))
    result = QuoteMatch.VERBATIM if not alterations else QuoteMatch.CLOSE
    return QuoteVerification(
        quote=quote,
        result=result,
        similarity=round(similarity, 2),
        matched_passage=passage,
        was_ocrd=was_ocrd,
        alterations=alterations,
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
