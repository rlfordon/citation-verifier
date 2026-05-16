"""
Build coverage_report.html — a single-file HTML version of MEMO.md
with embedded Plotly charts.

Pipeline:
  1. Read MEMO.md, render markdown to HTML with python-markdown.
  2. Replace the Mermaid flowchart block with a Plotly Sankey that
     carries the same numbers.
  3. Inject inline Plotly charts at <!-- chart:NAME --> placeholders:
       chart:per_tier_coverage   — per-tier coverage bar
       chart:coverage_buckets    — donut of 5 coverage buckets
       chart:diagnosis           — diagnosis breakdown for the 34 misses
       chart:cite_type_x_diag    — stacked bar
  4. Wrap in a CSS shell.

The HTML is self-contained except for one CDN reference to plotly's JS
bundle (so the file is small; the bundle is ~3.5 MB).

Usage:
    venv/Scripts/python.exe 19_make_report.py
"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path

import markdown
import plotly.graph_objects as go

HERE = Path(__file__).parent
MEMO_MD = HERE / "MEMO.md"
UNIFIED_CSV = HERE / "unified_review.csv"
OUT_HTML = HERE / "coverage_report.html"

PLOTLY_CDN = (
    '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>'
)


def cite_type(cite: str) -> str:
    c = cite or ""
    if re.search(r"\bWL\b", c):
        return "Westlaw"
    if re.search(r"\bF\.\s*Supp", c):
        return "F. Supp."
    if re.search(r"\bF\.\s*\dth?\b", c):
        return "F.[Nd]"
    if re.search(r"\bCal\.", c):
        return "Cal."
    if re.search(r"\bSo\.\s*\dd?\b", c):
        return "So."
    if re.search(r"\bU\.S\.\b", c):
        return "U.S."
    return "other"


def load_rows() -> list[dict[str, str]]:
    return list(csv.DictReader(UNIFIED_CSV.open(encoding="utf-8")))


# ---- Sankey ----------------------------------------------------------------

def make_sankey(rows: list[dict[str, str]]) -> str:
    """Sankey: 250 -> measurable/excluded -> lookup hit/miss -> recovery -> outcome."""
    # Counts
    total = len(rows)
    excluded = sum(1 for r in rows if r["coverage"] == "excluded")
    measurable = total - excluded
    found_lookup = sum(1 for r in rows if r["coverage"] == "found_via_lookup")
    miss = measurable - found_lookup

    in_op = sum(1 for r in rows if r["coverage"] == "in_opinions")
    in_recap = sum(1 for r in rows if r["coverage"] == "in_recap")
    not_found = sum(1 for r in rows if r["coverage"] == "not_found_anywhere")

    # Sub-breakdowns
    op_auto = sum(
        1 for r in rows
        if r["coverage"] == "in_opinions" and r["diagnosis"] == "cl_cluster_citations_empty"
    )
    op_manual_rule25 = sum(
        1 for r in rows
        if r["coverage"] == "in_opinions" and r["diagnosis"] == "caption_divergence_rule_25d"
    )
    op_manual_ssa = sum(
        1 for r in rows
        if r["coverage"] == "in_opinions" and r["diagnosis"] == "ssa_pseudonym"
    )
    recap_auto = sum(
        1 for r in rows
        if r["coverage"] == "in_recap" and not r.get("user_corrected_url")
    )
    recap_manual = in_recap - recap_auto

    not_in_cl = sum(1 for r in rows if r["diagnosis"] == "not_in_cl")
    fpos = sum(1 for r in rows if r["diagnosis"] == "rescue_was_false_positive")
    amb = sum(1 for r in rows if r["diagnosis"] == "audit_ambiguous")

    # Single-line labels with the count inline, so the node boxes stay short.
    labels = [
        f"250 cited citations",                                        # 0
        f"Measurable ({measurable})",                                  # 1
        f"Excluded ({excluded})",                                      # 2
        f"Resolved by /citation-lookup/ ({found_lookup})",             # 3
        f"Missed by /citation-lookup/ ({miss})",                       # 4
        f"in_opinions ({in_op})",                                      # 5
        f"in_recap ({in_recap})",                                      # 6
        f"not_found_anywhere ({not_found})",                           # 7
        f"Auto: name search ({op_auto})",                              # 8
        f"Manual: Rule 25(d) / Doe ({op_manual_rule25})",              # 9
        f"Manual: SSA pseudonym ({op_manual_ssa})",                    # 10
        f"Auto: RECAP search ({recap_auto})",                          # 11
        f"Manual: docket-only ({recap_manual})",                       # 12
        f"not_in_cl ({not_in_cl})",                                    # 13
        f"Wrong-cluster rescue ({fpos})",                              # 14
        f"audit_ambiguous ({amb})",                                    # 15
    ]
    # Explicit x positions create clean columns; explicit y prevents
    # plotly from stacking the terminal nodes at varied depths in the
    # rightmost column. Values must be in (0, 1).
    node_x = [
        0.01,  # 0 total
        0.18, 0.18,  # 1 measurable, 2 excluded
        0.42, 0.42,  # 3 lookup hit, 4 lookup miss
        0.62, 0.62, 0.62,  # 5 in_op, 6 in_recap, 7 not_found
        0.99, 0.99, 0.99,  # 8-10 in_op sub-outcomes
        0.99, 0.99,         # 11-12 in_recap sub-outcomes
        0.99, 0.99, 0.99,  # 13-15 not_found sub-outcomes
    ]
    node_y = [
        0.50,        # 0 total
        0.55, 0.05,  # 1 measurable (large), 2 excluded (small, top)
        0.30, 0.80,  # 3 found (upper area), 4 missed (lower area)
        0.55, 0.78, 0.92,  # 5 in_op, 6 in_recap, 7 not_found
        0.45, 0.59, 0.66,  # 8 auto, 9 r25, 10 ssa
        0.74, 0.82,        # 11 recap auto, 12 recap manual
        0.86, 0.96, 0.99,  # 13 not_in_cl, 14 wrong-cluster, 15 ambiguous
    ]
    sources, targets, values, colors = [], [], [], []

    def add(s: int, t: int, v: int, color: str) -> None:
        if v > 0:
            sources.append(s)
            targets.append(t)
            values.append(v)
            colors.append(color)

    # Stage 1: total -> measurable / excluded
    add(0, 1, measurable, "rgba(180, 180, 180, 0.3)")
    add(0, 2, excluded, "rgba(200, 200, 200, 0.25)")
    # Stage 2: measurable -> lookup hit / miss
    add(1, 3, found_lookup, "rgba(76, 175, 80, 0.35)")  # green hit
    add(1, 4, miss, "rgba(255, 193, 7, 0.35)")  # amber miss
    # Stage 3: miss -> outcome
    add(4, 5, in_op, "rgba(255, 235, 59, 0.35)")
    add(4, 6, in_recap, "rgba(255, 235, 59, 0.35)")
    add(4, 7, not_found, "rgba(244, 67, 54, 0.35)")
    # Stage 4: in_op breakdown
    add(5, 8, op_auto, "rgba(255, 235, 59, 0.3)")
    add(5, 9, op_manual_rule25, "rgba(255, 152, 0, 0.4)")
    add(5, 10, op_manual_ssa, "rgba(255, 152, 0, 0.4)")
    # Stage 4: in_recap breakdown
    add(6, 11, recap_auto, "rgba(255, 235, 59, 0.3)")
    add(6, 12, recap_manual, "rgba(255, 152, 0, 0.4)")
    # Stage 4: not_found breakdown
    add(7, 13, not_in_cl, "rgba(244, 67, 54, 0.35)")
    add(7, 14, fpos, "rgba(244, 67, 54, 0.35)")
    add(7, 15, amb, "rgba(244, 67, 54, 0.35)")

    node_colors = [
        "#9e9e9e",  # total
        "#9e9e9e",  # measurable
        "#bdbdbd",  # excluded
        "#4caf50",  # found by lookup
        "#ffb300",  # lookup miss
        "#fdd835",  # in_opinions
        "#fdd835",  # in_recap
        "#e53935",  # not found
        "#fff176",  # op auto
        "#ffa726",  # op manual rule25
        "#ffa726",  # op manual ssa
        "#fff176",  # recap auto
        "#ffa726",  # recap manual
        "#ef5350",  # not_in_cl
        "#ef5350",  # wrong-cluster rescue
        "#ef5350",  # audit_ambiguous
    ]

    fig = go.Figure(
        data=[
            go.Sankey(
                arrangement="fixed",
                node=dict(
                    pad=22,
                    thickness=16,
                    line=dict(color="rgba(0,0,0,0.2)", width=0.5),
                    label=labels,
                    color=node_colors,
                    x=node_x,
                    y=node_y,
                ),
                link=dict(source=sources, target=targets, value=values, color=colors),
            )
        ]
    )
    fig.update_layout(
        title=dict(
            text="Where each of the 250 cited citations ended up",
            font=dict(size=16),
        ),
        font=dict(size=11),
        height=620,
        margin=dict(l=10, r=180, t=60, b=10),  # right margin keeps terminal labels visible
    )
    return fig.to_html(include_plotlyjs=False, full_html=False, div_id="chart-sankey")


# ---- Per-tier coverage ------------------------------------------------------

def make_per_tier_chart(rows: list[dict[str, str]]) -> str:
    tiers = ("SCOTUS", "Circuit", "State_COLR", "State_IAC", "Federal_District")
    in_buckets = {"found_via_lookup", "in_opinions", "in_recap"}
    data = []
    for t in tiers:
        n_in = sum(1 for r in rows if r["cited_tier"] == t and r["coverage"] in in_buckets)
        denom = sum(1 for r in rows if r["cited_tier"] == t and r["coverage"] != "excluded")
        pct = 100 * n_in / denom if denom else 0
        data.append((t, n_in, denom, pct))

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=[d[0] for d in data],
            y=[d[3] for d in data],
            text=[f"{d[1]}/{d[2]}<br>{d[3]:.1f}%" for d in data],
            textposition="outside",
            marker=dict(
                color=[d[3] for d in data],
                colorscale=[[0, "#ef5350"], [0.5, "#ffa726"], [1, "#4caf50"]],
                cmin=70,
                cmax=100,
                showscale=False,
            ),
        )
    )
    fig.update_layout(
        title="Coverage by tier (after manual corrections + Phase 6)",
        yaxis=dict(title="Percent in CL", range=[0, 120]),
        xaxis=dict(title=""),
        height=400,
        margin=dict(l=40, r=20, t=60, b=40),
    )
    return fig.to_html(include_plotlyjs=False, full_html=False, div_id="chart-per-tier")


# ---- Coverage buckets donut -------------------------------------------------

def make_coverage_donut(rows: list[dict[str, str]]) -> str:
    cov = Counter(r["coverage"] for r in rows)
    order = [
        ("found_via_lookup", "Found via /citation-lookup/"),
        ("in_opinions", "Cluster in CL; lookup missed"),
        ("in_recap", "RECAP docket only; no cluster"),
        ("not_found_anywhere", "Not found anywhere"),
        ("excluded", "Unmeasurable (Phase 6)"),
    ]
    labels = [pretty for _, pretty in order]
    values = [cov.get(k, 0) for k, _ in order]
    colors = ["#4caf50", "#fdd835", "#ffa726", "#ef5350", "#bdbdbd"]
    fig = go.Figure(
        data=[
            go.Pie(
                labels=labels,
                values=values,
                hole=0.45,
                marker=dict(colors=colors, line=dict(color="white", width=2)),
                textinfo="value+percent",
                textposition="outside",
                sort=False,
            )
        ]
    )
    fig.update_layout(
        title="Coverage bucket distribution (250 citations)",
        height=400,
        margin=dict(l=20, r=20, t=60, b=20),
        legend=dict(orientation="v", x=1.0, y=0.5),
    )
    return fig.to_html(include_plotlyjs=False, full_html=False, div_id="chart-coverage-donut")


# ---- Diagnosis bar (34 lookup misses) ---------------------------------------

def make_diagnosis_chart(rows: list[dict[str, str]]) -> str:
    miss = [r for r in rows if r["coverage"] in ("in_opinions", "in_recap")]
    counts = Counter(r["diagnosis"] for r in miss)
    order = [
        ("cl_cluster_citations_empty", "cluster citations[] empty"),
        ("caption_divergence_rule_25d", "Rule 25(d) / Doe reveal"),
        ("ssa_pseudonym", "SSA pseudonym"),
        ("cl_docket_only_no_cluster", "docket only, no cluster"),
    ]
    labels = [pretty for _, pretty in order]
    values = [counts.get(k, 0) for k, _ in order]
    colors = ["#fdd835", "#ffa726", "#ffa726", "#ff7043"]

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=values,
            y=labels,
            orientation="h",
            text=[f"{v}" for v in values],
            textposition="outside",
            marker=dict(color=colors),
        )
    )
    fig.update_layout(
        title="The 34 lookup misses by diagnosis",
        xaxis=dict(title="Cases"),
        yaxis=dict(autorange="reversed"),
        height=320,
        margin=dict(l=180, r=40, t=60, b=40),
    )
    return fig.to_html(include_plotlyjs=False, full_html=False, div_id="chart-diagnosis")


# ---- Cite type × tier of the 34 misses --------------------------------------

def make_cite_type_chart(rows: list[dict[str, str]]) -> str:
    miss = [r for r in rows if r["coverage"] in ("in_opinions", "in_recap")]
    types = ["Westlaw", "Cal.", "F. Supp.", "F.[Nd]", "So.", "other"]
    tiers = ("Federal_District", "State_IAC", "State_COLR", "Circuit", "SCOTUS")
    cube: dict[tuple[str, str], int] = Counter()
    for r in miss:
        cube[(cite_type(r["citation_string"]), r["cited_tier"])] += 1

    fig = go.Figure()
    colors = {
        "Federal_District": "#5e35b1",
        "State_IAC": "#1e88e5",
        "State_COLR": "#00897b",
        "Circuit": "#fdd835",
        "SCOTUS": "#ef5350",
    }
    for tier in tiers:
        ys = [cube.get((t, tier), 0) for t in types]
        if sum(ys) == 0:
            continue
        fig.add_trace(
            go.Bar(name=tier, x=types, y=ys, marker_color=colors.get(tier))
        )
    fig.update_layout(
        title="The 34 lookup misses by citation type and tier",
        barmode="stack",
        xaxis=dict(title="Citation type"),
        yaxis=dict(title="Cases"),
        height=360,
        margin=dict(l=40, r=20, t=60, b=40),
        legend=dict(orientation="h", y=-0.2),
    )
    return fig.to_html(include_plotlyjs=False, full_html=False, div_id="chart-cite-type")


# ---- Markdown → HTML --------------------------------------------------------

# Strip the Mermaid fenced block (we replace it with the Plotly Sankey).
_MERMAID_RE = re.compile(r"```mermaid\n.*?\n```", re.DOTALL)


def render_md(md_text: str) -> str:
    md_text = _MERMAID_RE.sub('<div class="chart-placeholder" id="sankey-anchor"></div>', md_text)
    html = markdown.markdown(md_text, extensions=["tables", "fenced_code", "toc"])
    return html


# ---- Assembly ---------------------------------------------------------------

CSS = """
<style>
  :root {
    --fg: #1a1a1a;
    --muted: #666;
    --rule: #e5e5e5;
    --accent: #1a5490;
    --bg: #fafafa;
    --card: #ffffff;
    --code-bg: #f3f3f3;
  }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue",
                 Arial, sans-serif;
    color: var(--fg);
    line-height: 1.55;
    max-width: 1000px;
    margin: 0 auto;
    padding: 2rem 2.5rem 4rem;
    background: var(--bg);
  }
  h1, h2, h3, h4 {
    color: var(--fg);
    line-height: 1.2;
    margin-top: 2.2rem;
    margin-bottom: 0.8rem;
  }
  h1 { font-size: 1.8rem; border-bottom: 2px solid var(--accent); padding-bottom: 0.5rem; }
  h2 { font-size: 1.35rem; border-bottom: 1px solid var(--rule); padding-bottom: 0.3rem; }
  h3 { font-size: 1.12rem; }
  h4 { font-size: 1.02rem; color: var(--muted); }
  p, ul, ol { margin: 0.7rem 0; }
  code {
    background: var(--code-bg);
    padding: 0.08rem 0.3rem;
    border-radius: 3px;
    font-size: 0.92em;
    font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  }
  pre code { background: transparent; padding: 0; }
  pre {
    background: var(--code-bg);
    padding: 0.8rem 1rem;
    border-radius: 4px;
    overflow-x: auto;
    font-size: 0.88em;
  }
  blockquote {
    border-left: 3px solid var(--accent);
    background: var(--card);
    margin: 1rem 0;
    padding: 0.7rem 1rem;
    color: var(--muted);
    font-size: 0.95em;
    border-radius: 0 4px 4px 0;
  }
  table {
    border-collapse: collapse;
    margin: 1rem 0;
    font-size: 0.93em;
    width: 100%;
    background: var(--card);
  }
  th, td {
    border: 1px solid var(--rule);
    padding: 0.45rem 0.7rem;
    text-align: left;
    vertical-align: top;
  }
  th { background: #f0f0f0; font-weight: 600; }
  tr:nth-child(even) td { background: #fafafa; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  .chart-placeholder {
    background: var(--card);
    border: 1px solid var(--rule);
    border-radius: 4px;
    padding: 0.5rem;
    margin: 1.2rem 0;
  }
  .meta {
    color: var(--muted);
    font-style: italic;
    margin-bottom: 1.5rem;
  }
  .toc {
    background: var(--card);
    border-left: 3px solid var(--accent);
    padding: 0.8rem 1rem 0.8rem 1.5rem;
    margin: 1rem 0 2rem;
    font-size: 0.95em;
  }
  .toc ul { margin: 0.2rem 0; }
</style>
"""


def build_html(rows: list[dict[str, str]], md_html: str) -> str:
    sankey_div = make_sankey(rows)
    per_tier_div = make_per_tier_chart(rows)
    donut_div = make_coverage_donut(rows)
    diag_div = make_diagnosis_chart(rows)
    cite_type_div = make_cite_type_chart(rows)

    # Substitute the Sankey anchor in the markdown HTML
    md_html = md_html.replace(
        '<div class="chart-placeholder" id="sankey-anchor"></div>',
        f'<div class="chart-placeholder">{sankey_div}</div>',
    )

    # The toc extension wraps headers with id="..." attributes. Use a
    # regex that matches the heading regardless of attributes.
    def insert_after_h2(html: str, title: str, insertion: str) -> str:
        pattern = re.compile(
            r'(<h2[^>]*>\s*' + re.escape(title) + r'\s*</h2>)', re.IGNORECASE
        )
        return pattern.sub(lambda m: m.group(1) + insertion, html, count=1)

    def insert_before_h2(html: str, title: str, insertion: str) -> str:
        pattern = re.compile(
            r'(<h2[^>]*>\s*' + re.escape(title) + r'\s*</h2>)', re.IGNORECASE
        )
        return pattern.sub(lambda m: insertion + m.group(1), html, count=1)

    md_html = insert_after_h2(
        md_html,
        "Headline coverage",
        f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin:1rem 0;">'
        f'<div class="chart-placeholder">{per_tier_div}</div>'
        f'<div class="chart-placeholder">{donut_div}</div>'
        f'</div>',
    )
    md_html = insert_before_h2(
        md_html,
        "Recommendations",
        f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin:1rem 0;">'
        f'<div class="chart-placeholder">{diag_div}</div>'
        f'<div class="chart-placeholder">{cite_type_div}</div>'
        f'</div>',
    )

    html = (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>CourtListener coverage of cited cases</title>\n"
        f"{PLOTLY_CDN}\n"
        f"{CSS}\n"
        "</head>\n<body>\n"
        f"{md_html}\n"
        "</body>\n</html>\n"
    )
    return html


def main() -> int:
    rows = load_rows()
    md_text = MEMO_MD.read_text(encoding="utf-8")
    md_html = render_md(md_text)
    html = build_html(rows, md_html)
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"wrote {OUT_HTML.name} ({len(html):,} bytes, {len(rows)} rows of data)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
