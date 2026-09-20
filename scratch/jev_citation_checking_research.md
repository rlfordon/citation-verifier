# Jev (TypeSafe AI) and citation checking — research + ideas

**Date:** 2026-09-19
**Method:** three Sonnet research agents (web search + page fetches), synthesized.
**Reliability note:** the agents' fetch tool summarizes pages rather than scraping
them byte-for-byte. Items marked *(secondhand)* came from third-party summaries or
search snippets and were not confirmed against a primary page. Verify before
relying on any specific number.

---

## 1. What Jev is

- **Not a coding agent and not an orchestrator.** Jev is a model from **TypeSafe AI**
  (typesafe.ai; docs at docs.typesafe.ai), out of stealth 2026-09-15. Billed as a
  "System One" model: it does not generate text. You send a block of text/JSON (the
  **state**) plus one or more **typed questions**; it returns typed answers with
  calibrated confidence. Current versions referenced: `jev-1.12` / `jev-1.13`.
- **jevcode.ai is a community site, not the vendor.** Footer: "Community-maintained
  technical resource. Not affiliated with TypeSafe AI." It mirrors TypeSafe's
  cookbooks as localized "cases" pages (source: github.com/miounet11/jevcode).
- **Three question types:**
  - `Choice` — pick one of N labeled options; returns a probability per option.
  - `Score` — rate against ordered levels of a rubric.
  - `Noul` — "is this statement true?" returns P(true) from 0 to 1.
- **Fan-out:** many questions can ride in one call. TypeSafe: "All questions are
  evaluated in parallel, so adding more questions to a call typically doesn't add
  any latency." Their parallel-questions cookbook claims 12.2x cheaper / 10x faster
  for 13 questions in one call vs 13 calls.
- **No tools, no web, no files, no prose, no reasoning.** It sees only the state you
  hand it. It returns a label and a number — never an explanation or a quoted passage.
- **How you call it:** HTTPS API (`POST https://api.typesafe.ai/v1/systemone`, bearer
  token `TYPESAFE_API_KEY`); Python SDK `pip install typesafe-sdk`; JS SDK
  `@typesafe-ai/sdk`; playground at console.typesafe.ai/playground. Also listed on
  OpenRouter / Cloudflare AI *(secondhand)*. Works fine from Windows — it's a cloud API.
- **Official Claude Code skill** (`typesafe-ai/skills`) is a documentation-reading
  skill that teaches an agent to write code calling the API. It is not an executor.
  Doc index for LLMs: https://docs.typesafe.ai/llms.txt (append `.md` to any docs path).
- **No first-party MCP server, no batch-submit API, no jobs/JSONL concept.** A few
  unofficial MCP wrappers exist (rahulrajaram/jev-mcp, douglance/jevon).
- **Pricing:** $0.042 per million input tokens; output free. Early access.
- **Limits *(secondhand — verify)*:** ~1,200 requests/min; state + longest single
  question <= 32K tokens; state + all questions <= 64K tokens.
- **Documented weaknesses** (TypeSafe's own "jaggedness" page,
  docs.typesafe.ai/model-jaggedness/jev-1.13, plus reviewers): reads instructions
  **literally** (won't infer unstated scope or negation); **can't count reliably**;
  **treats dates as text**, not ordered quantities; weak numeric precision;
  **distracted by large irrelevant state**; affected by adversarial content.
- **Benchmark skepticism:** all headline speed/cost/accuracy claims are TypeSafe's
  own; architecture and weights unpublished; reviewers say the headline figures
  can't be reconstructed from the published table.

## 2. What people have done on citation checking

Public material is **thin**, and **nothing legal-specific has actually been built**
that we could find.

### 2a. TypeSafe's cookbook — "Double-checking citations"
https://docs.typesafe.ai/cookbooks/citation_check (mirrored at
https://www.jevcode.ai/en/cases/citation-check/ and other locales)

1. **Deterministic quote match** — normalize whitespace + curly quotes, exact
   substring search. "A quote that is not in the source is fabricated, and no model
   is needed to find that out."
2. **One `Choice` question** on the matched section vs the claim:

```python
QUESTIONS = {
    "relation": Choice(
        instructions="How does the section relate to the claim?",
        criteria={
            "supports": "The section states the claim or directly implies that it is true",
            "contradicts": "The section states the opposite of the claim or implies it is false",
            "says_nothing": "The section does not address what the claim asserts, either way",
        },
    ),
}
RELATION_TO_VERDICT = {"supports": "verified", "contradicts": "contradicted",
                       "says_nothing": "unsupported"}
```

3. **Confidence gate:** `AUTO_ACCEPT = 0.8`; below that, a human confirms. "Start
   high, and lower the threshold as you see how the model does on your own documents."

Test: 8 planted citations against RFC 7519 (the JSON Web Token spec), 45 sections.
4 accurate -> verified at 0.93–0.99. 4 broken -> 1 `fabricated` (string match),
1 `contradicted` at 0.99, 2 `unsupported` at 0.27 and 0.56 (routed to review).
Stated limitation: exact match only — "a quote that is truncated or lightly
reworded comes back as `fabricated`." Replay via a shipped `json_cache.json`
(same idea as our cassettes).

**Versus us:** same shape as `quote_matcher` -> assess, but our quote matching is
more sophisticated (fuzzy + OCR-aware), and the cookbook has **no existence step** —
it assumes the source document is already correct. Our CourtListener
existence/name-match has no counterpart.

### 2b. Isaac Flath — "Six things I tried with Jev" (2026-09-16)
https://isaacflath.com/writing/six-things-i-tried-with-jev

Independent hands-on test, Gemini as baseline. Item 4 (citations): gives Jev the
question, the answer, and the cited text; asks agree / disagree / irrelevant. Caught
a deliberately altered underwriting fee. Pitch: fast enough to run inline on every
agent turn. Item 1 (fact-checking scripts): 24/24 for both Jev and Gemini, Jev
0.41s vs 1.68s; caught a subtle mismatch (a "half hour" that referred to call prep,
not the call). Item 3 (PDF passage reranking): right passage ranked #1 in 7/8 vs
1/8 for raw embeddings. Items 5–6 (categorizing notes, diagnosing agent failures):
Gemini slightly *more* accurate (25/28 vs 24/28; 20/24 vs 19/24), Jev ~8–13x faster.
**Pattern: roughly LLM-level accuracy, slightly worse on subjective categorization,
much faster.**

### 2c. Paper Trellis — the one real, working tool (academic papers, not legal)
https://github.com/MarissaFamularo/citation-verifier (demo: verify.papertrellis.com)

Resolves references via PubMed/Crossref/OpenAlex, fetches full text or abstract.
**Claude** proposes a verdict plus the verbatim supporting passage; **the app
itself proves that passage exists** in the fetched source (an unverifiable quote
is discarded and flagged, never shown as supporting); **Jev** then reads the citing
sentence next to the *proven passage* and returns supports/contradicts/says_nothing
as a secondary reliability score; a human records the final verdict. "Model
verdicts are a triage aid, not a finding." Closest public analog to our
architecture. Note that Jev judges a short proven passage, not the whole source —
which sidesteps its "distracted by large state" weakness.

### 2d. Seth Chandler — "What Jev might mean to the legal world" (2026-09-17)
https://legaled.ai/what-jev-might-mean-to-the-legal-world/

The only legal-specific piece; speculative, not a build. Proposes exactly our use
case ("Pair each proposition in an AI-drafted brief with the passage cited for it,
and ask whether the passage supports the proposition") and floats
citation-treatment classification (overruled / followed / distinguished) across the
whole CourtListener corpus. Skepticism worth heeding: no multi-step reasoning; no
retrieval; **cannot return the supporting passage, so verdicts are "hard to check
and hard to defend"**; calibration on demo tasks doesn't establish calibration on
legal text; and a **terms-of-service flag** — TypeSafe's ToS reportedly allows
disclosure for undefined "objectionable content," which litigation documents are
full of.

### 2e. Low-confidence / not relevant
- A dozen near-identical "awesome-jev-usecases" GitHub repos appeared within days
  of launch; they look auto-generated and mostly restate the cookbook. One cites
  @jan__kubica testing Jev "for legal use cases in stll_app" — unconfirmed (tweet
  unreadable), may not be citation checking.
- CLERC legal reranking cookbook (TypeSafe): reranking 30-passage BM25 shortlists
  for 40 legal queries raised top-1 from 5% to 18%, top-10 from 38% to 62%.
  Legal-domain, but search reranking rather than verification — and modest numbers.
- Langfuse post on using Jev for evals:
  https://langfuse.com/blog/2026-09-18-using-typesafes-jev-for-evals (not read in depth).
- Checked and found no Jev mention: BriefCatch citation-tools roundup, Virginia
  Lawyers Weekly hallucination-tools piece, Above the Law on RealityCheck,
  r/law, r/LegalTechnology, Bluesky, Spanish-language searches.

---

## 3. A gap this research exposed in our own code (not Jev-related)

Paper Trellis's rule is "proof, not assertion": code proves every model-supplied
quote exists before displaying it. Our `assess_v2.md` tells the agent "Never invent
opinion language: every quote must appear in the opinion file, verbatim" — but
`run_apply_assessments` (`proposition_pipeline.py:1262`) writes `opinion_block`
straight through with **no check that it actually appears in the opinion text**.
The green box in the report is therefore asserted, not proven. We already have the
machinery (`quote_matcher.verify_quote`). Cheap deterministic fix, independent of
Jev, and arguably the most valuable single takeaway here.

---

## 4. Design constraints for any Jev experiment

- **Jev can only ever fill the `support` axis.** `brief_block`, `opinion_block`,
  `finding_analysis` need a text-generating model. Every Yellow/Red finding still
  goes through Opus/Sonnet.
- **Cost is a non-issue; accuracy is the whole question.** A 10K-token opinion is
  ~$0.0004 per call. A 30-claim brief is about a penny even with dozens of questions.
- **A "logic router" rarely needs to be sequential.** Because questions in one call
  are parallel and nearly free, ask the entire decision tree up front and let Python
  walk it. True sequencing is only needed when the *state* changes between steps
  (find the passage -> judge that passage).
- **Keep Opus blind to Jev.** Our Haiku prescreen (deleted in cost-audit F4) failed
  by feeding hints to Opus and biasing it lenient (Withers 16 -> 14 yellows). A claim
  either bypasses Opus entirely or goes to Opus with no Jev information.
- **Keep counting, dates, pincites, and number comparison in code.** These are Jev's
  documented weak spots, and `run_crosscheck` already handles them deterministically.
- **Jev never issues a Yellow or Red on its own.** Those are accusations and need an
  audit trail (Chandler's point). Jev may only clear Greens or escalate.
- **Confidentiality / ToS:** opinions are public; unfiled brief text would go to an
  early-access third-party API with a reportedly broad disclosure clause. Read the
  ToS before sending anything non-public.
- **Honest savings math:** with the direct-API executor the audit estimated
  ~$1–2.50 per 30-claim brief, so clearing half the claims saves about a dollar. The
  bigger wins are **speed** (seconds vs minutes) and **scale** (screening whole
  dockets or corpora).

---

## 5. Ideas, ranked

> **Snapshot from before any experiments.** Current status of every idea, plus
> ten more that came out of the work, is in `jev/IDEAS.md`. What was actually
> done: `../docs/retrospectives/2026-09-19-jev-exploration.md`.

| Rank | Idea | One-line rationale |
|---|---|---|
| 1 | A. Shadow rubric | Near-free experiment that tells us whether anything else is viable |
| 2 | E. Second reader on the proven passage | Tiny state, proven design (Paper Trellis), additive-only, no lenient risk |
| 3 | B. Auto-Green gate | The only idea that saves money/time; blocked on A's results |
| 4 | D. Quote-alteration adjudicator | Best natural fit for Jev; fixes a known noisy band |
| 5 | C. Passage locator | Big enabler but most work; vendor's own legal numbers are mediocre |
| 6 | H. Poor-man's citator | New capability rather than savings; exciting, large scope |
| 7 | F. Wrong-document tripwire | Cheap, but overlaps A's first question |
| 8 | G. Extraction checker | Real gap, but needs labeled extraction data we don't have |

### A. Decomposed support rubric, shadow mode
Replace one "does it support?" judgment with 6–8 atomic questions in a single call,
opinion as state, all claims citing that opinion packed together (mirrors assess-v2
per-opinion packing):
- Does the opinion address the same legal issue as the proposition? (Noul)
- Does it state the proposition or directly imply it? (Choice: states / implies / neither)
- Is that language the court's own holding, or a party's argument, a dissent, or
  background? (Choice)
- Does the opinion hold the opposite? (Noul)
- Does the brief use an absolute where the opinion is qualified? (Noul)
- Is the procedural posture consistent with what the brief implies? (Noul)

Python combines answers into `supported` / `partial` / `unsupported`;
`derive_color` untouched. Build as `JevExecutor` behind the `LLMExecutor` protocol
with a JSON cache for replay. Shadow mode = runs alongside Opus, changes nothing.
Score against the human labels in the frozen corpora (~95 claims: Withers, Payne,
Wainwright). Whole experiment costs pennies. Follows TypeSafe's own guidance:
keep each question atomic and "combine the results with logic in your code."

### B. Auto-Green gate
A claim skips Opus only when: rubric unanimous AND confidence above threshold AND
`cl_status` clean VERIFIED AND no `quote_floor` AND no `crosscheck_flags`.
Everything else goes to Opus unchanged and blind. Tune the threshold on the frozen
corpora to **zero lenient errors**, then read off the clearance rate. Green
`finding_analysis` becomes a template around the matched passage. Expect "partial"
(overstated holdings) to be Jev's weak class — which is why the gate only clears Greens.

### C. Passage locator
Chunk the opinion into paragraphs; Jev scores each against the proposition. Uses:
(1) `matched_passage` hints for paraphrase claims (today only quoted text gets one);
(2) lets A judge a focused passage instead of a whole opinion; (3) handles opinions
over the 32K cap; (4) optionally trims Opus input to top passages + syllabus —
risky for "case on unrelated subject" findings, which need the whole opinion to
prove absence. Measure before trusting.

### D. Quote-alteration adjudicator
For `CLOSE` quotes, state = just the brief's quote and the opinion's matched
passage. Choice: transcription/OCR artifact / meaning-preserving edit /
meaning-changing alteration. Number and date diffs checked in code first. Replaces
the blunt 0.75 similarity cutoff in `_quote_floor` with a materiality judgment.
Independent of A/B.

### E. Second reader on the proven passage (Paper Trellis pattern)
After Opus returns, (1) code proves `opinion_block` exists in the opinion (see §3),
then (2) Jev reads `cited_for` next to that proven passage and returns
supports / contradicts / says_nothing. Confident disagreement with Opus's `support`
-> amber "second reader disagrees" chip in `crosscheck_flags` style. **Flags only,
never colors.** Costs ~nothing, no lenient risk, tiny focused state. Targets known
misses (`payne-03`, the 3 missed Withers yellows). Doesn't apply where
`opinion_block` is deliberately empty (topic-mismatch Reds).

### F. Wrong-document / topic-mismatch tripwire
One question on the syllabus or first few thousand tokens: is this opinion about
the same subject as the proposition? Catches bad merge linkage and "case on
unrelated subject" early. Variant: identity check for `CITE_UNCONFIRMED` /
`POSSIBLE_MATCH` (does the opinion's content match how the brief describes the
case?) — warning only, no score change (Muldrow constraint).

### G. Extraction checker
No extraction eval exists today (cost audit). Per extracted row: does `proposition`
faithfully restate `brief_sentence`? Is the citation attached to the right clause?
What is the citation signal (*see*, *cf.*, *but see*)? A "but see" cite is
*supposed* to contradict — nothing downstream knows that today.

### H. Poor-man's citator (from Chandler's essay)
For each cited case, pull citing opinions from CourtListener's citation graph, use
eyecite to find the cite location, and have Jev classify the surrounding window:
followed / distinguished / criticized / overruled / mere mention. 500 citing
opinions x ~1K-token windows is ~$0.02. Would add an "is this still good law?"
axis we don't have at all. Large scope; treatment classification is nuanced and
the literalness weakness may bite; would need its own labeled eval set.

---

## 6. Suggested first step

> Done the same day. Next step now: `../docs/plans/2026-09-19-jev-locator-assess-test.md`.

Read the TypeSafe ToS, confirm the context limits from the official docs, then build
`JevExecutor` + the A rubric in shadow mode and score it on the three frozen corpora.
Separately (no Jev needed): add the `opinion_block` verbatim check from §3.

## Sources

- https://typesafe.ai/ · https://docs.typesafe.ai/ · https://docs.typesafe.ai/llms.txt
- https://docs.typesafe.ai/cookbooks/citation_check
- https://docs.typesafe.ai/model-jaggedness/jev-1.13
- https://typesafe.ai/blog/introducing-system-one-models-and-jev
- https://github.com/typesafe-ai/typesafe-sdk-python · https://github.com/typesafe-ai/skills
- https://www.jevcode.ai/en/cases/citation-check/ · https://www.jevcode.ai/en/cases/use-case-map/
- https://isaacflath.com/writing/six-things-i-tried-with-jev
- https://github.com/MarissaFamularo/citation-verifier · https://verify.papertrellis.com
- https://legaled.ai/what-jev-might-mean-to-the-legal-world/
- https://kingy.ai/blog/typesafe-jev-review-the-ai-model-that-doesnt-generate-text/
- https://forkast.news/typesafe-ais-jev-is-not-an-llm-and-that-may-be-the-point/
- https://langfuse.com/blog/2026-09-18-using-typesafes-jev-for-evals
