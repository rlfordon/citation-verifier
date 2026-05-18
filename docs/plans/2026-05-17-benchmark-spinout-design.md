# Benchmark Spin-out Design

**Date:** 2026-05-17
**Status:** Approved, ready for implementation plan
**Supersedes:** `benchmark/docs/plans/benchmark-spinout-prep.md` (preserved in git history before commit that deleted `benchmark/` — the earlier "API-stability prep" framing, walked back in favor of moving benchmark out now)

---

## Motivation

The `benchmark/` sub-project needs to be private while it's being developed. Citation-verifier needs to remain public as a tool. They currently share a repository, so the sub-project must move to its own private repo.

The earlier spin-out-prep plan (May 2026) framed this as "stabilize citation-verifier's public API first, then spin out." That ordering was driven by a hypothetical "external forker" use case. The current motivation — privacy during iteration — doesn't require API stability for external consumers. It requires a clean physical separation between the two codebases.

## Goal

Move the `benchmark/` sub-project from public `citation-verifier` to a new private repo `case-law-proposition-benchmark`, keeping benchmark functional and citation-verifier clean.

## Constraints and decisions

The following were settled during brainstorming and constrain the design:

| Decision | Choice | Rationale |
|---|---|---|
| Long-term privacy | Private until publication; some portion may eventually go public, not necessarily the whole thing | Allows iterating without committing to public API stability or publication strategy |
| Consumption model | Library import via git URL | Benchmark has 28 import sites; CLI rewrite would be large and would lose eyecite metadata fidelity. Library import keeps existing code working. |
| History strategy | Fresh start in new repo (no `git filter-repo`) | Cleanest mental model: pre-spinout = public, post-spinout = private. No archaeological commits in the new repo. |
| Public-side audit of existing benchmark commits | Skip | Anything currently in `benchmark/` on public `main` stays public regardless (history can't be retroactively privatized without a destructive force-push). The spinout protects future work only. |
| `gold_db.py` placement | Move to new repo as `gold_db/` package | Only consumer is benchmark; "reusable infrastructure" framing in CLAUDE.md was aspirational. |
| Dev loop | Editable install for local dev + tag pin in `requirements.txt` for reproducibility | Fast iteration + deterministic CI/fresh-machine setup |

## Non-goals (explicitly deferred)

- **PyPI publishing of citation-verifier.** Git URL pin works fine for a single private consumer. Revisit if/when external forkers appear or PyPI helps a publication.
- **API.md / formalized stable public API.** Most valuable paired with PyPI; defer together.
- **The eyecite-fork hygiene issue.** Citation-verifier's `pyproject.toml` says `eyecite>=2.6` but actually requires the rlfordon fork for `verify-brief` PDF parsing. Real bug, but doesn't block spinout (benchmark doesn't do PDF parsing). Separate cleanup ticket.
- **Destructive history rewrite of citation-verifier.** Inappropriate for "make future work private" — appropriate only for "accidentally committed an API key" situations.
- **`audit-misses` CLI subcommand.** Listed in the earlier prep doc as "next." Not a spinout dependency; can be built later in citation-verifier as time permits.

---

## Repository layout after the move

### citation-verifier (public, stays put)

```
src/citation_verifier/      # gold_db.py REMOVED
tests/                       # test_gold_db.py REMOVED
briefs/                      # /verify-brief work — unchanged
scratch/                     # iterative verification workflow — unchanged
docs/retrospectives/         # unchanged
docs/plans/                  # unchanged (non-benchmark plans)
web/                         # QC web app — unchanged
CLAUDE.md                    # benchmark sections removed
pyproject.toml               # version bumped to 0.2.0, testpaths cleaned
README.md                    # unchanged
```

**Public surface after the move:** the verifier library, the verify-brief skill+pipeline, and the iterative-verification workflow. Coherent story: "a citation verification tool with a brief-checking skill and a QC workflow."

### case-law-proposition-benchmark (new, private)

```
gold_db/
  __init__.py                # was src/citation_verifier/gold_db.py
  migrations/                # was benchmark/gold_db/migrations/
  gold.db                    # was benchmark/gold_db/gold.db
  exports/                   # was benchmark/gold_db/exports/
  README.md                  # was benchmark/gold_db/README.md
runners/                     # was benchmark/runners/
pilot_a/                     # was benchmark/pilot_a/
releases/                    # was benchmark/releases/
docs/                        # was benchmark/docs/
scratch/                     # was benchmark/scratch/
tests/
  test_gold_db.py            # was citation-verifier tests/test_gold_db.py
  ...                        # was benchmark/runners/tests/
pyproject.toml               # new
requirements.txt             # new — pins citation-verifier@<tag>
README.md                    # adapted from benchmark/README.md
CLAUDE.md                    # new — windows env + benchmark conventions
.gitignore                   # copied from citation-verifier
.env.example                 # documents COURTLISTENER_API_TOKEN
ROADMAP.md                   # was benchmark/ROADMAP.md
TODO.md                      # was benchmark/TODO.md
```

**Key transformations:**

- The `benchmark/` prefix is dropped (`benchmark/runners/score.py` → `runners/score.py`).
- Imports: `from citation_verifier.gold_db import X` → `from gold_db import X` (~20 files).
- The `gold_db` directory becomes a Python package — its `__init__.py` is the former `gold_db.py` module. Migrations, exports, and `gold.db` live alongside as data files in the same package.

---

## Move sequence (5 phases)

Each phase ends in a verifiable checkpoint. Critical rule: **Phase 4 doesn't happen until Phase 3 passes.** Deleting benchmark from citation-verifier before verifying it works in its new home means recovery via git history — painful.

### Phase 1 — prep citation-verifier (in this repo, before anything moves)

1. Verify install-clean: in a fresh venv, `pip install git+file:///path/to/citation-verifier@HEAD` and confirm `from citation_verifier import CitationVerifier` works.
2. Tag the current stable point: `git tag v0.1.0 && git push --tags`.

### Phase 2 — create and populate new repo

3. Create private repo `case-law-proposition-benchmark` on GitHub.
4. Clone it locally, next to citation-verifier.
5. Copy `benchmark/` contents to new repo root (drop the `benchmark/` prefix).
6. Move `src/citation_verifier/gold_db.py` → new repo's `gold_db/__init__.py`.
7. Move `tests/test_gold_db.py` → new repo's `tests/test_gold_db.py`.
8. Update imports across ~20 files: `from citation_verifier.gold_db` → `from gold_db`.
9. Write new `pyproject.toml`, `requirements.txt`, `README.md`, `CLAUDE.md`, `.gitignore`, `.env.example`.
10. `requirements.txt` pins: `citation-verifier @ git+https://github.com/rlfordon/citation-verifier.git@v0.1.0` (plus other benchmark deps).
11. Initial commit + push to private GitHub.

### Phase 3 — verify new repo works end-to-end

12. Fresh venv: `pip install -r requirements.txt`. Confirm it pulls citation-verifier from git.
13. Layer editable install on top: `pip install -e ../citation-verifier`.
14. Set `COURTLISTENER_API_TOKEN` in new `.env`.
15. Run benchmark's test suite: should pass.
16. Run a representative benchmark command (e.g., a small scorer pass) end-to-end. Confirm it produces expected output.

### Phase 4 — clean up citation-verifier (only after Phase 3 passes)

17. In citation-verifier: delete `benchmark/`.
18. Delete `src/citation_verifier/gold_db.py`.
19. Delete `tests/test_gold_db.py`.
20. Update CLAUDE.md: remove "Benchmark sub-project" section; remove `gold_db.py` row from Files table; fix the stale comment at [`src/citation_verifier/client.py:28`](../../src/citation_verifier/client.py).
21. Update `pyproject.toml`: remove `benchmark/runners/tests` from `testpaths`. Bump version to `0.2.0`.
22. Verify citation-verifier's tests still pass.
23. Commit, tag `v0.2.0`, push.

### Phase 5 — point new repo at the cleaned-up version

24. In new repo, bump `requirements.txt`: `citation-verifier@v0.2.0`.
25. Reinstall + re-run smoke test from Phase 3.
26. Commit + push the version bump.

---

## Dev workflow after the move

### One-time setup on each computer

```bash
# In a shared parent directory
git clone https://github.com/rlfordon/citation-verifier.git
git clone https://github.com/rlfordon/case-law-proposition-benchmark.git  # private

# Set up citation-verifier (unchanged from today)
cd citation-verifier
python -m venv venv
source venv/Scripts/activate
pip install -e .
pip install -e /path/to/eyecite  # the fork (only needed for verify-brief)

# Set up benchmark
cd ../case-law-proposition-benchmark
python -m venv venv
source venv/Scripts/activate
pip install -r requirements.txt           # pulls citation-verifier@<tag> from GitHub
pip install -e ../citation-verifier       # override with editable install for dev
```

### Day-to-day patterns

| Scenario | What you do |
|---|---|
| Edit benchmark code only | Work in benchmark venv. Commit + push benchmark. |
| Edit citation-verifier, want to test in benchmark | Edit citation-verifier. Editable install makes changes visible immediately. Commit + push citation-verifier when ready. |
| Sync to other computer | Push both repos. On other machine, `git pull` both. Editable install picks up citation-verifier changes automatically. |
| Mark a stable point | `git tag v0.x.y` on citation-verifier, push tags. Bump benchmark's `requirements.txt` when ready to commit to it. |
| Fresh machine / CI | `pip install -r requirements.txt` (without the editable layer) gives a deterministic tag-pinned version. |

The tag-bump cadence is discretionary. Tag when you want benchmark's `requirements.txt` to commit to a known-good citation-verifier; between tags, editable install gives main-tip behavior locally.

**Gotcha:** If you forget to push citation-verifier before pulling on another machine, the other machine's benchmark won't see the change (editable install reads local citation-verifier, not GitHub). The "always commit and push everything" preference in CLAUDE.md already covers this.

---

## Open implementation details

The following are real questions the implementation plan needs to answer, but they're details, not design forks:

- Exact contents of the new repo's `pyproject.toml` (Python version, build backend, dev extras).
- Exact contents of the new repo's `CLAUDE.md` (which sections to carry over from citation-verifier's, what benchmark-specific conventions to add).
- Whether to copy `.gitignore` verbatim or pare down to benchmark-relevant patterns.
- How to handle existing `benchmark/scratch/` working data: copy as-is, or selectively (some scratch trees may be obsolete).
- Where to put `benchmark-spinout-prep.md` (the original prep doc): in the new repo's docs (gets deleted with `benchmark/` from public anyway), or leave behind as historical artifact in citation-verifier (would require copying it out of `benchmark/docs/plans/` to `docs/plans/` before Phase 4 deletes `benchmark/`).

---

## What gets archived / lost

After Phase 4, the following content exists *only* in citation-verifier's git history (public, browsable via `git log -- benchmark/`):

- All of `benchmark/`'s commit history (the v1 release work, gold_db plans, consolidation work, memo iterations).
- The original `benchmark-spinout-prep.md` (unless explicitly copied out before deletion).
- Earlier versions of `gold_db.py`.

This is by design (per the "fresh start" decision). The content remains accessible; it's just not in the new repo. If a future case calls for pulling old commits into the new repo, `git format-patch` from citation-verifier's history → `git am` in the new repo is the path.

---

## Success criteria

The spinout is complete when:

1. The new private repo `case-law-proposition-benchmark` exists, is populated, and its test suite passes against a fresh `pip install -r requirements.txt`.
2. A benchmark scorer runs end-to-end and produces expected output.
3. Citation-verifier no longer contains `benchmark/`, `gold_db.py`, or `test_gold_db.py`.
4. Citation-verifier's tests still pass.
5. Citation-verifier has a `v0.2.0` tag reflecting the post-cleanup state.
6. Benchmark's `requirements.txt` pins `citation-verifier@v0.2.0`.
7. The dev loop (editable install + sync across machines) is documented and working.
