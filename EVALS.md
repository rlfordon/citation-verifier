# Citation-Checker Evals

A map of the evaluation corpora in this repo, what each measures, where the
data comes from, and how to run it. Everything here **replays offline and
deterministically** — recorded CourtListener API responses and recorded LLM
verdicts ("cassettes") mean you can score the whole suite with no API token,
no network, and no model spend. Re-record against live services when you want
to refresh for data drift (see [Refreshing cassettes](#refreshing-cassettes)).

There are two layers, because there are two different questions a cite checker
has to answer:

1. **Citation resolution** — does the citation point to a *real* case, and is
   it the case it claims to be? (Catches fabricated/hallucinated citations.)
2. **Proposition support** — does the cited case actually *support the
   proposition it's cited for*? (Catches real cases cited for things they
   don't say.)

---

## Quickstart (from zero, offline)

```bash
git clone https://github.com/rlfordon/citation-verifier.git
cd citation-verifier
python -m venv venv && source venv/bin/activate   # Windows: source venv/Scripts/activate
pip install -e .

# Run every offline eval (no API token needed — replays cassettes).
# The default pytest config already deselects live API tests.
pytest tests/test_benchmark_regression.py \
       tests/test_fallback_regression.py \
       tests/test_refactor_corpus.py \
       tests/test_assessment_regression.py -v
```

The only tests that need a `COURTLISTENER_API_TOKEN` are the live ones (marked
`live_api`, deselected by default). Run them with `pytest -m live_api`.

---

## Layer 1 — Citation resolution

Each corpus is replayed through the verifier with a recorded cassette of
CourtListener responses (`tests/cassette_client.py`), so a scoring/gating
change is scored instantly and offline.

| Corpus | Size | What it measures | Provenance | Test |
|---|---|---|---|---|
| **Charlotin fakes** | **511** | Fabricated citations the verifier should *not* resolve | Mined offline from Damien Charlotin's hallucination database (`scratch/Charlotin-hallucination_cases.csv`, ~1,600 court rulings that addressed AI-hallucinated material) via `build_charlotin_corpus.py`. 509 court-confirmed "Fabricated: Case Law" + 2 real-but-wrong (pincite/court). | Scored by cassette replay (`charlotin_baseline.json`); no pytest guard yet — see note |
| Real-citation benchmark | 204 | Regression guard: real cases must keep resolving (catches over-tightening into false negatives) | Curated real citations, `benchmark_real_citations.json` | `test_benchmark_regression.py` |
| Fallback corpus | 51 | Lookup-miss citations that exercise the fuzzy opinion-search + RECAP fallback path | `build_fallback_corpus.py` | `test_fallback_regression.py` |
| Known fakes | 19 | Court-order + QC-confirmed hallucinations that must be rejected | Sanctions orders + QC triage, `known_fake_citations.json` | `test_false_positives.py` (live + schema) |
| Known reals | 14 | Tricky-but-real cites: abbreviations, WestLaw, docket shorthand, adjacent-page | Hand-verified, `known_real_citations.json` | `test_false_negatives.py` (live + schema) |
| Refactor corpus | 4 fixture buckets | Structural acceptance across the status families after the v0.3 refactor | `refactor_corpus.json` | `test_refactor_corpus.py` |

**A note on the Charlotin set and the "benchmark" naming:**

- The **Charlotin corpus** is labeled *court-confirmed fabrications per
  Charlotin's data — not independently re-verified row by row*. It was cleaned
  after the June 2026 live run (547 → 511: poisoned extractions and Charlotin
  mislabels dropped; see `docs/retrospectives/2026-06-10-charlotin-fake-mining.md`).
  Individual rows are promoted into the curated 19-item `known_fakes` corpus
  only after a live verification/adjudication pass. It is recorded with the
  generic recorder (`record_benchmark_cassette.py --corpus-name charlotin`)
  but is not yet pinned to a dedicated pytest regression test.
- **Current score** (live run 2026-06-11; re-scored 2026-10-31 by offline
  cassette replay through current `main` — 0 status changes):

  | On the 509 court-confirmed fakes | n | % |
  |---|---:|---:|
  | Rejected — `WRONG_CASE` (real cite, different case) | 238 | 46.8% |
  | Rejected — `NOT_FOUND` | 195 | 38.3% |
  | Flagged — `CITE_UNCONFIRMED` ("Check Cite") | 34 | 6.7% |
  | **Caught (total)** | **467** | **91.7%** |
  | Accepted — `VERIFIED` 18 / `VERIFIED_VIA_RECAP` 10 / `VERIFIED_PARTIAL` 3 | 31 | 6.1% |
  | Not assessable — `INSUFFICIENT_DATA` 8 / `VERIFICATION_INCOMPLETE` 3 | 11 | 2.2% |

  Excluding the 11 not assessable: 467/498 caught (93.8%). The 31 accepted
  fakes are almost all a real (often common) case name welded to a fabricated
  reporter/WL cite where CL has no same-family citation to contradict it; per
  the Check Cite retrospective they carry a `cite_not_on_record` warning. The 2
  relabeled real-case targets (wrong pincite, wrong court) both score
  `VERIFIED` — future pin/court-check targets, not misses. Paired false-alarm
  rate: the real-citation benchmark holds at 203/204 `VERIFIED` (the 1 is
  `VERIFICATION_INCOMPLETE`, not a rejection). Bias to keep in mind: the set
  leans toward flagrant fakes that courts quoted verbatim in sanctions rulings.
- To re-score after a logic change: `python tests/record_benchmark_cassette.py
  --corpus-name charlotin --from-cassette` (offline; **rewrites**
  `charlotin_baseline.json`).
- The **"benchmark" cassette/test** is the **204 real-citation** guard — a
  *separate* corpus from the Charlotin fakes, despite the shared word.

---

## Layer 2 — Proposition support

Each corpus is a **frozen pipeline workdir** under
`tests/data/assessment_corpora/<name>/`:

| file | contents |
|---|---|
| `claims.csv` | claims run through the deterministic phases (verify / merge / quote-check), with a stable `claim_id` |
| `opinions/` | the matched opinion texts the claims link to |
| `ground_truth.csv` | human labels (`claim_id, scale, expected, exists, hedged, notes, provenance`) |
| `jobs/assess_results.jsonl` | recorded live LLM verdicts — the cassette, keyed by `(claim_id, prompt_version)` |

So the LLM assessment scores **offline and deterministically** against frozen
ground truth.

| Corpus | Claims | Ground-truth labels | Source |
|---|---|---|---|
| Withers | 34 | 54 | A published green/yellow/red attorney exhibit (labels encode existence + support) |
| Payne | 27 | 27 | Brief + the `ab_test_cases.json` human-review ledger |
| Wainwright | 34 | 34 | Brief + the human-review ledger |

**Run it:**

```bash
# Regression test (pins the baselines below)
pytest tests/test_assessment_regression.py -v

# Score a single corpus directly
python -m citation_verifier.scoring tests/data/assessment_corpora/withers
```

**Baselines** (pinned by `test_assessment_regression.py`): the assessment
prompt is versioned. Under `assess-v1`, Withers catches 14/19 yellows and the
Payne+Wainwright A/B set scores 56/61 (≈92%). Under `assess-v2` (two-axis +
report blocks), Withers catches 16/19 yellows, reds 3/3, A/B 55/61 (90%). Full
detail and the per-row adjudication live in
[`tests/data/assessment_corpora/README.md`](tests/data/assessment_corpora/README.md).

---

## How the cassettes work

A cassette is a recorded set of responses so an eval can replay without
touching the network or a model:

- **Layer 1** — `tests/cassette_client.py` wraps the CourtListener client.
  `mode="record"` calls the real API and stores every return value keyed by
  `(method, args, kwargs)`; `mode="replay"` returns the stored value and never
  hits the network. Cassettes are gzip-compressed (`*_cassette.json.gz`).
- **Layer 2** — `jobs/assess_results.jsonl` holds recorded LLM verdicts;
  `RecordedExecutor` replays them keyed by `(claim_id, prompt_version)`.
  Changing a prompt template bumps the version, which forces a re-record (the
  replay raises a miss rather than silently scoring stale verdicts).

### Refreshing cassettes

```bash
# Layer 1 — re-record a corpus against live CourtListener (needs a token)
python tests/record_benchmark_cassette.py                       # real-citation benchmark
python tests/record_benchmark_cassette.py --corpus-name fallback
python tests/record_benchmark_cassette.py --corpus-name charlotin

# Layer 2 — rebuild the frozen assessment workdirs (idempotent)
python tests/build_assessment_corpora.py
```

---

## Where to go deeper

- `tests/data/README.md` — the false-negative / false-positive corpora and the
  FLP-reporting workflow.
- `tests/data/assessment_corpora/README.md` — the proposition-support corpora,
  scales, prompt versions, and per-row baselines.
- `docs/plans/2026-06-11-proposition-verifier-pipeline-design.md` — the
  assessment pipeline design (scoring, two-axis colors, quote floors).
- `CHANGELOG.md` — status-enum / `VerificationResult` schema history.
