"""A/B harness for the assessment phase (design SS9; roadmap Tier 2 #9).

Runs the assess verb over COPIES of frozen corpus workdirs
(tests/data/assessment_corpora/) with a named config from
tests/ab_test_configs.json, then scores each copy against its
ground_truth.csv via citation_verifier.scoring. The prompt is the
pipeline's versioned template -- this harness no longer carries its own
prompt copy (the old tests/ab_test_runner.py copy WAS the assess-v1
source text; it is now byte-pinned in
src/citation_verifier/prompts/assess_v1.md).

Config keys: model (default opus), executor ("sdk" | "api" | --replay),
prompt_version (default assess-v1). (The Haiku prescreen-hint arm was
deleted in cost-audit F4 -- measured harmful, no A/B gain.)

Usage:
    venv/Scripts/python.exe tools/ab_test_runner.py --replay
        # offline: score the frozen cassettes (the recorded baseline)
    venv/Scripts/python.exe tools/ab_test_runner.py --config opus-v2
        # live: copy corpora, run assess via the Agent SDK, score
    venv/Scripts/python.exe tools/ab_test_runner.py --config A B
    venv/Scripts/python.exe tools/ab_test_runner.py --compare X.jsonl Y.jsonl
    venv/Scripts/python.exe tools/ab_test_runner.py --config opus-v2 --dry-run

tests/ab_test_cases.json stays the human-review ledger; ground_truth.csv
is generated from it by tests/build_assessment_corpora.py.
"""
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

CORPORA = PROJECT_ROOT / "tests" / "data" / "assessment_corpora"
RESULTS_DIR = PROJECT_ROOT / "tests" / "data" / "results"
CONFIGS_FILE = PROJECT_ROOT / "tests" / "ab_test_configs.json"
DEFAULT_CORPORA = ("payne", "wainwright")


def load_configs(path=CONFIGS_FILE):
    with open(path, encoding="utf-8") as f:
        return json.load(f)["configs"]


def make_executor(config, workdir, phase="assess"):
    """Default executor factory: headless Agent SDK (design SS5) or the
    direct Messages API transport (cost-audit F1; config
    `"executor": "api"`, optional `"batch": true`).

    phase (currently always "assess") -- the default factory ignores it
    (the model is already overridden in the config it receives); test
    seams use it to route to per-phase recorded cassettes."""
    transport = config.get("executor", "sdk")
    if transport == "api":
        from citation_verifier.executor import MessagesAPIExecutor
        return MessagesAPIExecutor(model=config.get("model", "opus"),
                                   cwd=str(workdir),
                                   batch=bool(config.get("batch")))
    if transport != "sdk":
        raise ValueError(
            f"unsupported executor {transport!r}: the harness runs "
            f"headless (sdk), api, or --replay only")
    from citation_verifier.executor import AgentSDKExecutor
    return AgentSDKExecutor(model=config.get("model", "opus"),
                            cwd=str(workdir))


def run_ab_config(config_name, config, corpora=DEFAULT_CORPORA,
                  run_root=None, executor_factory=None, replay=False):
    """Run one config over the corpora; returns {corpus: CorpusScore}.

    replay=True scores each FROZEN corpus in place via its recorded
    cassette (read-only; no LLM). Otherwise each corpus is copied to
    run_root/<corpus>, its cassette removed, assess run through the
    factory-built executor, and the copy scored.
    """
    from citation_verifier.proposition_pipeline import run_assess
    from citation_verifier.scoring import format_report, score_workdir

    prompt_version = config.get("prompt_version", "assess-v1")
    scores = {}
    for name in corpora:
        src = CORPORA / name
        if replay:
            scores[name] = score_workdir(src,
                                         prompt_version=prompt_version)
        else:
            if run_root is None:
                raise ValueError("run_root is required for live runs")
            wd = Path(run_root) / name
            shutil.copytree(src, wd)
            cassette = wd / "jobs" / "assess_results.jsonl"
            if cassette.exists():
                cassette.unlink()  # fresh verdicts for this config
            executor = (executor_factory or make_executor)(
                config, wd, "assess")
            stats = run_assess(wd, executor=executor,
                               prompt_version=prompt_version)
            failures = getattr(executor, "failures", [])
            if failures:
                print(f"  WARNING {name}: {len(failures)} job "
                      f"failures: {failures[:3]}")
            if stats.pending:
                print(f"  WARNING {name}: {stats.pending} verdicts "
                      f"still pending -- scoring the rest")
            # Score through a skip-mode RecordedExecutor so transient job
            # failures don't kill the whole multi-corpus run (TODO
            # Priority-1: the strict default raised RecordedVerdictMiss
            # mid-generator and lost payne/wainwright in the 2026-06-13
            # sonnet-v2 arm). The drop is reported, never silent.
            from citation_verifier.executor import RecordedExecutor
            scorer = RecordedExecutor(wd / "jobs" / "assess_results.jsonl",
                                      missing="skip")
            scores[name] = score_workdir(wd, executor=scorer,
                                         prompt_version=prompt_version)
            if scorer.misses:
                print(f"  WARNING {name}: {len(scorer.misses)} claims "
                      f"dropped from scoring (no verdict): "
                      f"{[m[0] for m in scorer.misses]}")
        print(format_report(f"{config_name}/{name}", scores[name]))
    return scores


def dry_run_config(config_name, config, corpora=DEFAULT_CORPORA):
    """Print how many assess jobs each corpus would run. No copies."""
    import csv

    from citation_verifier.proposition_pipeline import _assessable
    print(f"=== {config_name} (dry run) ===")
    for name in corpora:
        with open(CORPORA / name / "claims.csv", newline="",
                  encoding="utf-8") as f:
            n = sum(1 for c in csv.DictReader(f) if _assessable(c))
        print(f"  {name}: {n} assess jobs "
              f"(model={config.get('model', 'opus')}, "
              f"prompt={config.get('prompt_version', 'assess-v1')})")


def save_results(config_name, scores):
    """Per-claim score rows to a timestamped JSONL (compare format)."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = RESULTS_DIR / f"ab_{config_name}_{ts}.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for corpus, score in scores.items():
            for row in score.rows:
                f.write(json.dumps({"corpus": corpus, **row}) + "\n")
    print(f"  Results saved to {out}")
    return out


def compare_results(file_a, file_b):
    """Side-by-side comparison of two saved score-row files, keyed by
    (corpus, claim_id). New format only (old case_id-keyed files predate
    the SS9 re-point)."""
    def load(path):
        rows = {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                rows[(r["corpus"], r["claim_id"])] = r
        return rows

    a, b = load(file_a), load(file_b)
    name_a, name_b = Path(file_a).stem, Path(file_b).stem
    keys = sorted(set(a) | set(b))
    print(f"\n=== {name_a} vs {name_b} ===")
    disagreements = []
    for k in keys:
        ra, rb = a.get(k, {}), b.get(k, {})
        pa, pb = ra.get("predicted", "-"), rb.get("predicted", "-")
        if pa != pb:
            disagreements.append(k)
            print(f"  DIFF {k[0]}/{k[1]}: expected "
                  f"{ra.get('expected') or rb.get('expected')}, "
                  f"{name_a[:20]}={pa}, {name_b[:20]}={pb}")
    for name, rows in ((name_a, a), (name_b, b)):
        scored = [r for r in rows.values() if "correct" in r]
        correct = sum(1 for r in scored if r["correct"])
        print(f"  {name}: {correct}/{len(scored)} correct")
    print(f"  Disagreements: {len(disagreements)} of {len(keys)}")


def main():
    parser = argparse.ArgumentParser(
        description="A/B harness: assess verb over frozen corpus "
                    "workdirs, scored against ground truth (SS9)")
    parser.add_argument("--config", nargs="+",
                        help="config name(s) from tests/ab_test_configs.json")
    parser.add_argument("--corpus", nargs="+",
                        default=list(DEFAULT_CORPORA),
                        help="corpus names under "
                             "tests/data/assessment_corpora")
    parser.add_argument("--replay", action="store_true",
                        help="score the frozen cassettes offline (no LLM)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print job counts; run nothing")
    parser.add_argument("--compare", nargs=2, metavar="FILE",
                        help="compare two saved score-row JSONL files")
    args = parser.parse_args()

    if args.compare:
        compare_results(args.compare[0], args.compare[1])
        return

    if args.replay:
        run_ab_config("replay", {}, corpora=args.corpus, replay=True)
        return

    if not args.config:
        parser.error("specify --config, --replay, or --compare")

    configs = load_configs()
    outfiles = []
    for name in args.config:
        if name not in configs:
            print(f"Unknown config: {name}. Available: {list(configs)}")
            sys.exit(1)
        if args.dry_run:
            dry_run_config(name, configs[name], corpora=args.corpus)
            continue
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_root = RESULTS_DIR / "ab_runs" / f"{name}_{ts}"
        run_root.mkdir(parents=True, exist_ok=True)
        scores = run_ab_config(name, configs[name], corpora=args.corpus,
                               run_root=run_root)
        outfiles.append(save_results(name, scores))
    if len(outfiles) == 2:
        compare_results(str(outfiles[0]), str(outfiles[1]))


if __name__ == "__main__":
    main()
