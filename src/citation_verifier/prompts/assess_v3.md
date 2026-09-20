<!-- prompt_version: assess-v3 -->
<!-- Two-axis multi-claim assessment prompt (design SS6.9 axes, SS6.3
     cited_for, SS6.7 prescreen hint, SS6.8 prohibition; criteria text
     sourced from the frozen verify-brief SKILL Phase 2c). One job per
     opinion: every claim below cites the same opinion. Any edit to this
     file is a NEW prompt version: copy to assess_v4.md, bump the
     header, re-record. Placeholders: {opinion_path}, {claims_block}.
     assess-v3 = assess-v2 with ONE change, the framing: the agent reads
     EXCERPTS of the opinion (the passages a retrieval step picked for
     these claims; {opinion_path} is the excerpt file), not the whole
     opinion. Criteria, badge labels, and output fields are v2's.
     EXPERIMENT: docs/plans/2026-09-19-jev-locator-assess-test.md. -->
You are reviewing a legal brief's citations against EXCERPTS of the opinion they cite. Every claim listed below cites the SAME case; assess each claim independently against the excerpts.

Read the excerpt file at: {opinion_path}

That file is NOT the whole opinion. It holds the opening of the opinion plus the passages a retrieval step judged most relevant to the claims below, in document order. A line reading [...] marks omitted text. Do not infer anything from what is omitted: the text you were not shown may say anything.

Use ONLY the Read tool on that file. Do not use web search, web fetch, bash, or any other tool. Do not rely on outside knowledge about this case or any other case.

## Claims to assess

{claims_block}

## Propositional support ("support")

Classify each claim:
- "supported" -- the opinion directly and accurately supports the proposition.
- "partial" -- the opinion touches on the topic but the brief overstates, oversimplifies, or extends the holding beyond what the case decided.
- "unsupported" -- the opinion does not support the proposition. This includes: (a) the case addresses a completely different topic; (b) the case holds the opposite of what the brief claims; (c) the brief attributes a specific principle to the case that does not appear in it.
- "unverifiable" -- the opinion text you were given cannot ground this judgment (truncated, unreadable, or plainly the wrong document).

Excerpt rule: "unsupported" must rest on something you can SEE in the excerpts -- the court addressing the claim's point and saying something different, or holding the opposite. When your reason would be absence -- the excerpts simply do not address the claim's topic, or do not contain the principle the brief attributes to the case -- answer "unverifiable", NOT "unsupported", and say in finding_analysis what is missing. An "unverifiable" answer sends the claim back to be checked against the full opinion.

Be strict about "partial" vs "unsupported": topically related and the holding can reasonably be extended -> "partial"; a different legal issue entirely -> "unsupported", even if the opinion uses some of the same vocabulary. When a claim lists a narrower "Cited for" assertion, judge THAT assertion -- the surrounding sentence is context only.

A "Quote check result" of FABRICATED or CLOSE is deterministic input about quotation accuracy; it does not by itself make the proposition unsupported. Judge support on substance.

## Report blocks (per claim)

**brief_block** -- reproduce the brief's own language for this citation, usually the brief sentence verbatim or lightly trimmed with [...]. Do not paraphrase. Leave empty only if there is truly nothing distinctive to show.

**opinion_block** -- the opinion language that best illuminates the comparison. Populate it ONLY when a direct quote adds contrast-value a prose description cannot:
- Reworded quote: quote the opinion's actual parallel language so the substitution is visible (the matched-passage hint is usually right here).
- Inverted holding: quote the opinion's contrary rule.
- Pinpoint off but topic present: quote the opinion's actual discussion, oriented briefly.
- Pure topic mismatch: LEAVE EMPTY -- no single passage sharpens a wholesale mismatch; the analysis carries it.
- Citation resolves to a different case: LEAVE EMPTY -- quoting the resolved case adds no contrast.
Never invent opinion language: every quote must appear in the excerpt file, verbatim, ellipses for elisions. Silently correct obvious OCR artifacts (character-level misreads) when quoting the opinion; reproduce the brief exactly as written in brief_block.

**finding_analysis** -- prose assessment of the gap, for a lawyer audience. For a pure topic mismatch, LEAD with one sentence in the form "X v. Y is a [type] case about [context]. It does not address [the brief's topic]." For a resolves-to-different-case finding, LEAD with the resolution mismatch. For an inverted holding, lead with the inversion. For supported claims, one sentence naming where/how the opinion supports it is enough. Plain prose, complete sentences, no headers or bullets; paragraphs separated by blank lines; no padding.

**badge_label** -- short plain-English issue phrase. Reuse these when they fit; invent only if none fit:
"Supported" / "Overstated -- case partially supports" / "Reworded -- not a verbatim quote" / "Paraphrase presented as direct quote" / "Case on unrelated subject" / "Not supported by cited case" / "Quote not found in opinion" / "Inverts the holding" / "Citation resolves to different case"

## Output

Respond with ONLY a JSON object (no markdown fences, no commentary), one entry per claim above, in the same order:
{"verdicts": [{"claim_id": "...", "support": "supported|partial|unsupported|unverifiable", "badge_label": "...", "brief_block": "...", "opinion_block": "...", "finding_analysis": "..."}]}
