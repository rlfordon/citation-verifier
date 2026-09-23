"""HTML report template for brief verification results.

Generates an interactive HTML report in the proposition-verifier style:
collapsible findings, paired blockquotes, methodology disclosure.
"""

from __future__ import annotations

import re

# Sentence-end pattern for dashboard teaser extraction. Matches `. ` only
# when preceded by at least two lowercase letters and followed by an
# uppercase letter. Excludes:
#   - case-name periods (`Tompkins v. Cyr` — `v` is a single lowercase)
#   - reporter abbreviations (`F. Supp.`, `U.S. `, `Inc. `, `Pa. ` — all
#     end with a capital before the period, or are single letters)
#   - short abbreviations (`p. 180`, `sec. 626`, `e.g.`, `i.e.`)
_SENTENCE_END_RE = re.compile(r'(?<=[a-z]{2})\.\s+(?=[A-Z])')


def generate_report_html(data: dict) -> str:
    """Generate a complete HTML report from structured verification data.

    Args:
        data: Dict with keys: title, case_name, case_number, filed_date,
              report_date, findings (list of issue dicts), verified (list),
              unable_to_verify (list), retrieved_opinions (list),
              unavailable_opinions (list).

    Returns:
        Complete HTML document as a string.
    """
    findings = data.get("findings", [])
    verified = data.get("verified", [])
    unable = data.get("unable_to_verify", [])
    retrieved = data.get("retrieved_opinions", [])
    unavailable = data.get("unavailable_opinions", [])

    # Count CLAIMS, not cards: an unable-to-verify card groups a
    # multiply-cited unavailable case into one card with N propositions,
    # so add N (not 1) to agree with the per-row run_report stats (review #3).
    unable_claims = sum(len(u.get("propositions", [])) or 1 for u in unable)
    total_checked = len(findings) + len(verified) + unable_claims
    red_count = sum(1 for f in findings if f.get("severity") == "red")
    yellow_count = sum(1 for f in findings if f.get("severity") == "yellow")
    checkcite_count = sum(1 for f in findings
                          if f.get("severity") == "orange")
    green_count = len(verified)
    gray_count = len(unable)

    # Build sections
    dashboard_html = _build_dashboard(
        total_checked, red_count, yellow_count, checkcite_count,
        green_count, gray_count, findings, unable,
    )
    findings_html = _build_findings(findings)
    verified_html = _build_verified(verified)
    unable_html = _build_unable_to_verify(unable)
    methodology_html = _build_methodology(retrieved, unavailable)

    # All-clear banner (suppressed when Check Cite items need attention)
    all_clear = ""
    if red_count == 0 and checkcite_count == 0:
        if yellow_count > 0:
            all_clear = (
                '<div style="background:#d4edda;border:1px solid #c3e6cb;'
                'border-radius:6px;padding:1rem;margin-bottom:1rem;">'
                f'<strong>No serious issues found.</strong> {yellow_count} '
                f'concern{"s" if yellow_count != 1 else ""} noted below.</div>'
            )
        else:
            all_clear = (
                '<div style="background:#d4edda;border:1px solid #c3e6cb;'
                'border-radius:6px;padding:1rem;margin-bottom:1rem;">'
                '<strong>No serious issues found.</strong> All citations verified.</div>'
            )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Proposition Verification Report — {_esc(data.get("case_name", ""))}</title>
<link href="https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400;0,600;1,400&family=Source+Sans+3:wght@300;400;600&display=swap" rel="stylesheet">
<style>
{_CSS}
</style>
<script>
function expandAll() {{ document.querySelectorAll('details').forEach(d => d.open = true); }}
function collapseAll() {{ document.querySelectorAll('details').forEach(d => d.open = false); }}
</script>
</head>
<body>

<h1>Proposition Verification Report</h1>
<div class="meta">
  <span><strong>Brief:</strong> {_esc(data.get("title", ""))}</span><br>
  <span><strong>Case:</strong> <em>{_esc(data.get("case_name", ""))}</em>, {_esc(data.get("case_number", ""))}</span><br>
  <span><strong>Filed:</strong> {_esc(data.get("filed_date", ""))}</span>
  <span><strong>Report generated:</strong> {_esc(data.get("report_date", ""))}</span>
</div>

{all_clear}
{dashboard_html}
{findings_html}
{unable_html}
{verified_html}
{methodology_html}

</body>
</html>"""


def _esc(text: str) -> str:
    """HTML-escape a string."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _badge(label: str, severity: str) -> str:
    """Render a severity badge."""
    css_class = {
        "red": "badge-red",
        "yellow": "badge-yellow",
        "orange": "badge-orange",
        "green": "badge-green",
        "blue": "badge-blue",
        "gray": "badge-gray",
    }.get(severity, "badge-gray")
    return f'<span class="badge {css_class}">{_esc(label)}</span>'


def _build_flags(flag_lines: list[str]) -> str:
    """Card-level amber chips: the SS6.5 crosscheck flags, plus the words a
    CLOSE quote altered. Neither ever moves a claim between lanes; both
    render even on Green cards."""
    if not flag_lines:
        return ""
    chips = "".join(
        f'<span class="flag-chip">&#9873; {_esc(t)}</span>'
        for t in flag_lines)
    return f'<div class="card-flags">{chips}</div>'


def _build_dashboard(
    total: int, red: int, yellow: int, checkcite: int, green: int,
    gray: int, findings: list[dict], unable: list[dict],
) -> str:
    """Build the summary dashboard."""
    issue_items = []
    for f in findings:
        sev = f.get("severity", "yellow")
        anchor = f.get("id", "")
        case = f.get("case_name", "")
        citation = f.get("citation", "")
        badge_label = f.get("badge_label", "")
        # Pull a one-line preview from the finding's prose. Dashboard teaser
        # only — the full prose shows in the finding card. Prefer the first
        # sentence; if the sentence itself is very long, truncate at a word
        # boundary instead of mid-word.
        #
        # Sentence-end detection: require a lowercase letter, close bracket,
        # or close quote *before* the period, and an uppercase letter after
        # the space. This excludes case-name periods (`v. Cyr`) and reporter
        # abbreviations (`F. Supp.`, `U.S. `, `Inc. `, `Pa. `) which all have
        # a capital letter before the period — naive `.split(". ")` would
        # cut the teaser mid-citation.
        teaser = f.get("finding_analysis", "").strip()
        m = _SENTENCE_END_RE.search(teaser)
        first_sentence = teaser[:m.start() + 1] if m else teaser
        _TEASER_MAX = 220
        if len(first_sentence) > _TEASER_MAX:
            truncated = first_sentence[:_TEASER_MAX]
            last_space = truncated.rfind(" ")
            if last_space > _TEASER_MAX - 40:
                truncated = truncated[:last_space]
            first_sentence = truncated + "\u2026"
        elif first_sentence and not first_sentence.endswith("."):
            first_sentence += "."
        issue_items.append(
            f'<li class="sev-{sev}"><a href="#{anchor}">'
            f'{_badge(badge_label, sev)} '
            f'p. {_esc(f.get("page", ""))} &mdash; '
            f'<em>{_esc(case)}</em>, {_esc(citation)} &mdash; '
            f'{_esc(first_sentence)}</a></li>'
        )
    for u in unable:
        anchor = u.get("id", "")
        case = u.get("case_name", "")
        citation = u.get("citation", "")
        prop_count = len(u.get("propositions", []))
        suffix = (
            f'cited {prop_count}&times; &mdash; not on CourtListener'
            if prop_count > 1 else "Not on CourtListener"
        )
        issue_items.append(
            f'<li class="sev-gray"><a href="#{anchor}">'
            f'{_badge("Unable to verify", "gray")} '
            f'<em>{_esc(case)}</em>, {_esc(citation)} &mdash; '
            f'{suffix}</a></li>'
        )

    issues_html = "\n".join(issue_items) if issue_items else "<li>None</li>"

    return f"""<div class="dashboard">
  <h2 style="border:none; margin-top:0; padding:0;">Summary</h2>
  <div class="stats">
    <div class="stat"><div class="stat-num">{total}</div><div class="stat-label">Claims checked</div></div>
    <div class="stat stat-red"><div class="stat-num">{red}</div><div class="stat-label">Serious issues</div></div>
    <div class="stat stat-yellow"><div class="stat-num">{yellow}</div><div class="stat-label">Concerns</div></div>
    <div class="stat stat-orange"><div class="stat-num">{checkcite}</div><div class="stat-label">Check cite</div></div>
    <div class="stat stat-green"><div class="stat-num">{green}</div><div class="stat-label">Verified</div></div>
    <div class="stat stat-gray"><div class="stat-num">{gray}</div><div class="stat-label">Unable to verify</div></div>
  </div>
  <h3>Issues</h3>
  <ul class="issue-list">
    {issues_html}
  </ul>
</div>"""


def _build_brief_block(f: dict) -> str:
    """Build the "What the brief claims" block for a finding.

    Preference order:
    1. Agent-authored `brief_block` (Phase 2c output) — the agent chose how
       to present the brief's claim; render it verbatim in the styled box.
    2. brief_sentence (full sentence with quoted strings bolded)
    3. quoted strings alone
    4. proposition (agent summary)
    """
    agent_block = f.get("brief_block", "").strip()
    if agent_block:
        return (
            '<div class="bq-label">What the brief claims:</div>'
            f'<div class="bq-brief">{_format_analysis(agent_block)}</div>'
        )

    brief_sentence = f.get("brief_sentence", "").strip()
    quoted_strings = f.get("quoted_strings", [])
    proposition = f.get("proposition", "").strip()

    if brief_sentence:
        # Highlight the quoted strings within the sentence when we can find
        # them. Keeps the reader's eye on what the brief attributed to the
        # court while still showing surrounding context.
        rendered = _esc(brief_sentence)
        for q in quoted_strings:
            if not q:
                continue
            esc_q = _esc(q)
            if esc_q in rendered:
                rendered = rendered.replace(
                    esc_q, f'<strong>{esc_q}</strong>', 1,
                )
        return (
            '<div class="bq-label">What the brief claims:</div>'
            f'<div class="bq-brief">{rendered}</div>'
        )

    if quoted_strings:
        joined = "\u201d \u2026 \u201c".join(quoted_strings)
        return (
            '<div class="bq-label">Quoted in brief:</div>'
            f'<div class="bq-brief" style="font-style:italic;">'
            f'\u201c{_esc(joined)}\u201d</div>'
        )

    if proposition:
        return (
            '<div class="bq-label">What the brief claims:</div>'
            f'<div class="bq-brief">{_esc(proposition)}</div>'
        )

    return ""


def _build_opinion_block(f: dict) -> str:
    """Build the "Actual language in opinion" block for a finding.

    Preference order:
    1. Agent-authored `opinion_block` — agent chose the passage (or narrative)
       that best illuminates the mismatch. If the agent wrote an empty
       string, the block is intentionally omitted (e.g., the analysis
       prose covers it inline).
    2. Deterministic `matched_passages` fallback — shown for legacy claims
       that pre-date agent-authored blocks.
    """
    agent_block = f.get("opinion_block", "").strip()
    if agent_block:
        return (
            '<div class="bq-label">Actual language in opinion:</div>'
            f'<div class="bq-opinion">{_format_analysis(agent_block)}</div>'
        )

    # Legacy-data path: show the deterministic matched_passage. Whether
    # it renders at all is already gated by the similarity threshold in
    # generate_report, so we don't re-check here.
    matched_passages = f.get("matched_passages", [])
    if not matched_passages:
        return ""
    parts = []
    for mp in matched_passages:
        text = mp.get("text", "") if isinstance(mp, dict) else str(mp)
        parts.append(
            '<div class="bq-label">Actual language in opinion:</div>'
            f'<div class="bq-opinion">\u2026 {_esc(text)} \u2026</div>'
        )
    return "".join(parts)


def _format_analysis(text: str) -> str:
    """Format agent-written analysis text into HTML paragraphs.

    Splits on blank lines. Everything else is escaped — agents are told to
    write plain prose, not HTML.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return _esc(text)
    return "".join(f'<p>{_esc(p)}</p>' for p in paragraphs)


def _build_findings(findings: list[dict]) -> str:
    """Build the findings walkthrough section."""
    if not findings:
        return ""

    items = []
    for f in findings:
        sev = f.get("severity", "yellow")
        badge_label = f.get("badge_label", "")
        cl_url = f.get("cl_url", "")
        case_link = (
            f'<a href="{_esc(cl_url)}"><span class="case-name">{_esc(f.get("case_name", ""))}</span></a>'
            if cl_url else
            f'<span class="case-name">{_esc(f.get("case_name", ""))}</span>'
        )

        # Brief block: show the surrounding sentence (with quoted language
        # called out when present). Fall back to isolated quoted strings,
        # then to the proposition.
        brief_block = _build_brief_block(f)

        # Opinion block: agent-authored when present (agent decided what to
        # show, which may be the matched_passage, a different passage, or
        # nothing). Falls back to the deterministic matched_passage for
        # legacy claims.csv data where the agent didn't author this block.
        matched_block = _build_opinion_block(f)

        # Agent-authored narrative. One block, flexible length, may include
        # direct quotations from the opinion when the agent judges it helpful.
        analysis_block = ""
        if f.get("finding_analysis"):
            analysis_block = (
                '<div class="analysis">'
                f'{_format_analysis(f["finding_analysis"])}'
                '</div>'
            )

        # SS6.5 crosscheck flags render at the top of the card body --
        # even when support is otherwise fine.
        flags_block = _build_flags(f.get("card_flags", []))

        items.append(f"""<details id="{_esc(f.get("id", ""))}">
  <summary>
    <strong>p. {_esc(f.get("page", ""))}</strong> &mdash;
    {case_link}, {_esc(f.get("citation", ""))}
    &mdash; {_badge(badge_label, sev)}
  </summary>
  <div class="finding-body">
    {flags_block}
    {brief_block}
    {matched_block}
    {analysis_block}
  </div>
</details>""")

    controls = (
        '<div class="controls">'
        '<button onclick="expandAll()">Expand All</button> | '
        '<button onclick="collapseAll()">Collapse All</button>'
        '</div>'
    )

    return f"""<h2>Findings</h2>
{controls}
{"".join(items)}"""


def _build_unable_to_verify(unable: list[dict]) -> str:
    """Build the unable-to-verify section with one card per cited case.

    When the same unavailable case is cited for multiple propositions, all
    propositions are listed inside a single card rather than duplicating
    the card itself.
    """
    if not unable:
        return ""

    items = []
    for u in unable:
        propositions = u.get("propositions", [])
        if propositions:
            rows = []
            for p in propositions:
                page = p.get("page", "")
                prop_text = p.get("proposition", "")
                # Show quoted text inline when present
                quoted_raw = p.get("quoted_text", "")
                quoted_suffix = ""
                try:
                    import json as _j
                    qs = _j.loads(quoted_raw) if quoted_raw else []
                    if qs:
                        joined = "\u201d \u2026 \u201c".join(qs)
                        quoted_suffix = (
                            f' <em style="color:#666;">(quotes brief: '
                            f'\u201c{_esc(joined)}\u201d)</em>'
                        )
                except (ValueError, TypeError):
                    pass
                rows.append(
                    f'<li><strong>p. {_esc(page)}:</strong> '
                    f'{_esc(prop_text)}{quoted_suffix}</li>'
                )
            prop_list = (
                '<div class="bq-label">Propositions the brief attributes to '
                'this case:</div>'
                f'<ul class="prop-list">{"".join(rows)}</ul>'
            )
        else:
            prop_list = ""

        # Summary header: case + citation + count suffix if > 1
        count = len(propositions)
        count_suffix = (
            f' <span style="color:#888;">(cited {count}\u00d7)</span>'
            if count > 1 else ""
        )

        items.append(f"""<details id="{_esc(u.get("id", ""))}">
  <summary>
    <span class="case-name">{_esc(u.get("case_name", ""))}</span>, {_esc(u.get("citation", ""))}
    {count_suffix}
    &mdash; {_badge("Unable to verify -- opinion text unavailable", "gray")}
  </summary>
  <div class="finding-body">
    {prop_list}
    <div class="explanation">{_esc(u.get("explanation", ""))}</div>
  </div>
</details>""")

    return "\n".join(items)


def _build_verified(verified: list[dict]) -> str:
    """Build the collapsed verified-citations section."""
    if not verified:
        return ""

    items = []
    for v in verified:
        cl_url = v.get("cl_url", "")
        case_link = (
            f'<a href="{_esc(cl_url)}"><em>{_esc(v.get("case_name", ""))}</em></a>'
            if cl_url else
            f'<em>{_esc(v.get("case_name", ""))}</em>'
        )
        supp = ""
        if v.get("supporting_language"):
            supp = (
                f' &mdash; <span style="color:#555;font-size:0.85em;">'
                f'"{_esc(v["supporting_language"])}"</span>'
            )
        badge_label = v.get("badge_label", "Supported")
        items.append(
            f'<div class="verified-item">'
            f'<span class="checkmark">&#10003;</span> '
            f'<strong>p. {_esc(v.get("page", ""))}</strong> &mdash; '
            f'{case_link}, {_esc(v.get("citation", ""))} &mdash; '
            f'{_esc(v.get("proposition", ""))} '
            f'{_badge(badge_label, "green")}'
            f'{supp}'
            f'{_build_flags(v.get("card_flags", []))}'
            f'</div>'
        )

    return f"""<h2>Verified Citations</h2>
<details>
  <summary>{len(verified)} citation-proposition pair{"s" if len(verified) != 1 else ""} verified &mdash; click to expand</summary>
  <div class="finding-body">
    {"".join(items)}
  </div>
</details>"""


def _build_methodology(
    retrieved: list[dict], unavailable: list[dict],
) -> str:
    """Build the methodology & limitations section."""
    retrieved_items = "\n".join(
        f'<li><em>{_esc(r.get("case_name", ""))}</em>, '
        f'{_esc(r.get("citation", ""))} (cluster {_esc(r.get("cluster_id", ""))})</li>'
        for r in retrieved
    )
    unavailable_items = "\n".join(
        f'<li><em>{_esc(u.get("case_name", ""))}</em>, '
        f'{_esc(u.get("citation", ""))} &mdash; {_esc(u.get("reason", ""))}</li>'
        for u in unavailable
    )

    unavailable_section = ""
    if unavailable_items:
        unavailable_section = f"""<p>The following opinions were <strong>not available on CourtListener</strong> and could not be verified against opinion text:</p>
  <ul>
    {unavailable_items}
  </ul>"""

    return f"""<div class="methodology">
  <h2>Methodology &amp; Limitations</h2>
  <ul>
    <li><strong>Quote verification</strong> is near-deterministic but limited by the quality of CourtListener's text. Minor discrepancies may reflect OCR artifacts in the source database rather than errors in the brief.</li>
    <li><strong>Propositional support</strong> assessment is an AI judgment. It reflects Claude's reading of the opinion text and should be verified by counsel. Reasonable lawyers may disagree about whether a case "supports" a given proposition.</li>
    <li>This report checks whether cited cases say what the brief claims they say. It does not assess the overall legal merit of the brief's arguments or whether the cited cases are the best authorities available.</li>
    <li>Cases marked "unable to verify" should be checked through other sources (Westlaw, Lexis, etc.).</li>
  </ul>
  <h3>Verification method disclosure</h3>
  <p>The following opinions were <strong>retrieved from CourtListener</strong> and verified against their full text:</p>
  <ul>
    {retrieved_items}
  </ul>
  {unavailable_section}
  <p>No opinions in this report were assessed from training knowledge. All assessments are based on retrieved opinion text.</p>
</div>"""


# ---------------------------------------------------------------------------
# CSS — matches proposition-verifier report style
# ---------------------------------------------------------------------------

_CSS = """* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Source Sans 3', sans-serif;
  font-size: 15px;
  line-height: 1.6;
  color: #2d2d2d;
  max-width: 900px;
  margin: 0 auto;
  padding: 2rem 1.5rem;
  background: #fafaf8;
}
h1, h2, h3 { font-family: 'Lora', serif; }
h1 { font-size: 1.6rem; margin-bottom: 0.3rem; color: #1a1a1a; }
h2 { font-size: 1.25rem; margin: 2rem 0 0.8rem; color: #333; border-bottom: 1px solid #ddd; padding-bottom: 0.3rem; }
h3 { font-size: 1.05rem; margin: 1rem 0 0.5rem; }

.meta { color: #666; font-size: 0.9rem; margin-bottom: 1.5rem; }
.meta span { display: inline-block; margin-right: 1.5rem; }

.dashboard { background: #fff; border: 1px solid #e0e0e0; border-radius: 6px; padding: 1.2rem 1.5rem; margin-bottom: 2rem; }
.stats { display: flex; gap: 1.5rem; flex-wrap: wrap; margin-bottom: 1rem; }
.stat { text-align: center; }
.stat-num { font-size: 1.8rem; font-weight: 600; }
.stat-label { font-size: 0.8rem; color: #666; text-transform: uppercase; letter-spacing: 0.05em; }
.stat-red .stat-num { color: #c0392b; }
.stat-yellow .stat-num { color: #d4a017; }
.stat-orange .stat-num { color: #e67e22; }
.stat-green .stat-num { color: #27ae60; }
.stat-gray .stat-num { color: #888; }

.issue-list { list-style: none; padding: 0; }
.issue-list li {
  padding: 0.5rem 0.7rem;
  margin-bottom: 0.4rem;
  border-left: 4px solid #ccc;
  background: #fefefe;
  font-size: 0.9rem;
}
.issue-list li.sev-red { border-left-color: #c0392b; background: #fdf2f2; }
.issue-list li.sev-yellow { border-left-color: #d4a017; background: #fefcf0; }
.issue-list li.sev-orange { border-left-color: #e67e22; background: #fff7ef; }
.issue-list li.sev-gray { border-left-color: #999; background: #f5f5f5; }
.issue-list a { color: inherit; text-decoration: none; }
.issue-list a:hover { text-decoration: underline; }

.badge {
  display: inline-block;
  font-size: 0.75rem;
  font-weight: 600;
  padding: 0.15rem 0.5rem;
  border-radius: 3px;
  vertical-align: middle;
}
.badge-red { background: #f8d7da; color: #721c24; }
.badge-yellow { background: #fff3cd; color: #856404; }
.badge-orange { background: #ffe0b2; color: #8a4500; }
.badge-green { background: #d4edda; color: #155724; }
.badge-blue { background: #d1ecf1; color: #0c5460; }
.badge-gray { background: #e9ecef; color: #495057; }

.bq-brief {
  border-left: 4px solid #e67e22;
  background: #fef9f3;
  padding: 0.7rem 1rem;
  margin: 0.5rem 0;
  font-size: 0.9rem;
}
.bq-opinion {
  border-left: 4px solid #27ae60;
  background: #f0faf4;
  padding: 0.7rem 1rem;
  margin: 0.5rem 0;
  font-size: 0.9rem;
}
.bq-label { font-weight: 600; font-size: 0.8rem; color: #666; margin-bottom: 0.2rem; }

.card-flags { margin: 0.2rem 0 0.5rem; }
.flag-chip {
  display: inline-block;
  font-size: 0.75rem;
  font-weight: 600;
  background: #fff3e0;
  color: #8a4500;
  border: 1px solid #f0c27a;
  border-radius: 3px;
  padding: 0.1rem 0.5rem;
  margin: 0 0.3rem 0.2rem 0;
}

details { margin-bottom: 0.8rem; }
summary {
  cursor: pointer;
  padding: 0.6rem 0.8rem;
  background: #fff;
  border: 1px solid #e0e0e0;
  border-radius: 4px;
  font-size: 0.9rem;
  list-style: none;
}
summary::-webkit-details-marker { display: none; }
summary::before { content: "\\25B6\\00a0"; font-size: 0.7rem; color: #888; }
details[open] > summary::before { content: "\\25BC\\00a0"; }
details[open] > summary { border-radius: 4px 4px 0 0; border-bottom: none; }
.finding-body {
  background: #fff;
  border: 1px solid #e0e0e0;
  border-top: none;
  border-radius: 0 0 4px 4px;
  padding: 1rem;
}
.case-name { font-family: 'Lora', serif; font-style: italic; }
.explanation { margin-top: 0.7rem; font-size: 0.9rem; line-height: 1.5; }
.analysis { margin-top: 0.8rem; font-size: 0.92rem; line-height: 1.55; color: #2d2d2d; }
.analysis p { margin-bottom: 0.6rem; }
.analysis p:last-child { margin-bottom: 0; }
.prop-list { margin: 0.3rem 0 0.6rem 1.2rem; font-size: 0.9rem; }
.prop-list li { margin-bottom: 0.25rem; }

.controls { margin-bottom: 0.5rem; font-size: 0.8rem; }
.controls button {
  background: none;
  border: none;
  color: #2980b9;
  cursor: pointer;
  font-size: 0.8rem;
  padding: 0.2rem 0.4rem;
}
.controls button:hover { text-decoration: underline; }

.verified-item {
  padding: 0.3rem 0;
  font-size: 0.85rem;
  border-bottom: 1px solid #f0f0f0;
}
.checkmark { color: #27ae60; font-weight: bold; }

.methodology { font-size: 0.85rem; color: #555; margin-top: 2rem; }
.methodology h2 { font-size: 1.1rem; }
.methodology ul { padding-left: 1.2rem; }
.methodology li { margin-bottom: 0.5rem; }

@media print {
  details { open: true; }
  details[open] > summary { border-bottom: 1px solid #e0e0e0; }
  body { max-width: 100%; padding: 1rem; font-size: 12px; }
  .controls { display: none; }
  .dashboard { box-shadow: none; }
}"""
