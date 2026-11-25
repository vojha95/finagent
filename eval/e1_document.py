"""E1 command (blueprint 10.1): score a document-channel model on one company split.

    python eval/e1_document.py --model m0|m1|m2|m3 --split validation [--as-of 2026-12-31]
        [--compare m0|m1|m2|m3] [--record --single-change "..." --conclusion "..."]

--split held_out also needs --include-held-out: the held-out companies are scored once per
model version, in the final E1 run, and never used for debugging (blueprint 9.2).

M1 and M2 are loaded from models/m{1,2}_{dataset_version}.joblib and M3 from
models/m3_{dataset_version}.safetensors, trained by `python -m finagent.training.m1 --version v1`
(m2, m3 likewise); they are never retrained here. The load refuses a model trained on another
build of the row dataset (and M3 weights whose sha256 differs from their meta.json).

Field results are saved to runs/e1/{model}_{dataset_version}_{split}.jsonl. --compare m0 runs
McNemar against the saved M0 results of the same split, only if they come from the same commit,
and the paired company-bootstrap interval of the accuracy difference (this run minus the saved
one). Row-level accuracy and macro-F1 come from the row dataset's rows of the same split.
--record appends the run to eval/experiments.csv (Appendix F.2) with the git commit and
evidence manifest hash; run ids count the E1 rows only (e1-0001, e1-0002, ...).
"""

import argparse
import csv
import hashlib
import json
import subprocess
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from finagent.config import load_concept_map, load_universe
from finagent.document.classify import RulesClassifier
from finagent.evaluation.e1 import (
    bootstrap_ci,
    coverage,
    error_taxonomy,
    field_accuracy,
    mcnemar,
    paired_bootstrap_difference,
    per_item_accuracy,
    pinned_tickers,
    read_results,
    nci_counts,
    run_e1,
    strict_results,
    write_results,
)
from finagent.evaluation.rows import evaluate_classifier
from finagent.evidence import EvidenceStore

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = ROOT / "eval" / "experiments.csv"
RUNS_DIR = ROOT / "runs" / "e1"
MODELS_DIR = ROOT / "models"
DATASETS_DIR = ROOT / "datasets"


class CompareError(RuntimeError):
    """The saved results to compare against are missing or come from another commit."""


MODEL_FILES = {"m1": "joblib", "m2": "joblib", "m3": "safetensors"}


def load_classifier(model: str, dataset_version: str, models_dir: Path = MODELS_DIR,
                    datasets_dir: Path = DATASETS_DIR):
    """M0 from the synonym rules; M1, M2 and M3 from their saved files (never retrained here)."""
    if model == "m0":
        return RulesClassifier()
    if model == "m3":
        from finagent.training.m3 import load_model
    elif model == "m2":
        from finagent.training.m2 import load_model
    else:
        from finagent.training.m1 import load_model

    path = models_dir / f"{model}_{dataset_version}.{MODEL_FILES[model]}"
    if not path.exists():
        raise SystemExit(f"{path} not found: run python -m finagent.training.{model} "
                         f"--version {dataset_version} first")
    return load_model(models_dir, dataset_version,
                      datasets_dir / f"rows_{dataset_version}.meta.json")


def results_path(model: str, dataset_version: str, split: str, runs_dir: Path = RUNS_DIR) -> Path:
    return runs_dir / f"{model}_{dataset_version}_{split}.jsonl"


def compare(results, other: Path, commit: str) -> dict[str, Any]:
    """McNemar of this run (b) against saved results (a) from the same commit, and the paired
    company-bootstrap interval of b's accuracy minus a's."""
    if not other.exists():
        raise CompareError(f"{other} not found: run that model on this split first")
    header, baseline = read_results(other)
    if header.get("git_commit") != commit:
        raise CompareError(f"{other.name} is from commit {header.get('git_commit')}, not {commit}")
    only_a, only_b, p = mcnemar(baseline, results)
    paired = paired_bootstrap_difference(baseline, results)
    return {"against": header["model_version"], "baseline_only_correct": only_a,
            "this_only_correct": only_b, "p_value": p,
            "difference": None if paired is None else paired[0],
            "ci95_difference": None if paired is None else list(paired[1])}


def row_level(classifier, dataset_version: str, split: str,
              datasets_dir: Path = DATASETS_DIR) -> dict[str, Any] | None:
    """Row accuracy, macro-F1 and confusions on the dataset rows of `split` (None if absent)."""
    from finagent.training.m1 import load_rows

    path = datasets_dir / f"rows_{dataset_version}.parquet"
    if not path.exists():
        return None
    rows, items = load_rows(path, split)
    if not rows:
        return None
    metrics, table, confusions = evaluate_classifier(classifier, rows, items,
                                                     list(load_concept_map()))
    return {"metrics": asdict(metrics), "items": [asdict(s) for s in table],
            "confusions": confusions}


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT).stdout.strip()


def record(path: Path, version: str, split: str, dataset_version: str,
           metrics: dict[str, Any], commit: str, manifest_hash: str, single_change: str,
           conclusion: str) -> str:
    """Append one Appendix F.2 row; returns its run_id (the next E1 number; other evaluations'
    rows in the same file do not count)."""
    with path.open(newline="") as handle:
        run_id = f"e1-{sum(r['eval_id'] == 'E1' for r in csv.DictReader(handle)) + 1:04d}"
    with path.open("a", newline="") as handle:
        csv.writer(handle).writerow([
            run_id, date.today().isoformat(), "E1", "document_channel", version, single_change,
            dataset_version, split, json.dumps(metrics, default=str), commit, manifest_hash,
            conclusion,
        ])
    return run_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["m0", "m1", "m2", "m3"], default="m0")
    parser.add_argument("--dataset-version", default="v1")
    parser.add_argument("--split", default="validation")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--compare", choices=["m0", "m1", "m2", "m3"])
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--single-change", default="")
    parser.add_argument("--conclusion", default="")
    parser.add_argument("--include-held-out", action="store_true",
                        help="required with --split held_out (final E1 run only)")
    args = parser.parse_args(argv)

    store = EvidenceStore(ROOT / "evidence")
    universe = load_universe()
    tickers = pinned_tickers(store, universe, args.split, args.as_of, args.include_held_out)
    classifier = load_classifier(args.model, args.dataset_version)
    nci_log: list = []
    results = run_e1(store, tickers, args.as_of, classifier, universe=universe,
                     include_held_out=args.include_held_out, nci_log=nci_log)
    strict = strict_results(results)
    commit = _git("rev-parse", "--short", "HEAD")
    dirty = bool(_git("status", "--porcelain"))
    manifest = hashlib.sha256((ROOT / "evidence" / "manifest.json").read_bytes()).hexdigest()

    metrics: dict[str, Any] = {
        "field_accuracy": field_accuracy(results), "ci95": bootstrap_ci(results),
        "fields": len(results), "coverage": coverage(results), "companies": tickers,
        "errors": error_taxonomy(results),
        # BC13 off: each item must be read from its own row.
        "field_accuracy_strict": field_accuracy(strict), "ci95_strict": bootstrap_ci(strict),
        "errors_strict": error_taxonomy(strict),
        "bc13_fields": sum(r.strict_outcome is not None for r in results),
        "nci_status": nci_counts(nci_log),
    }
    rows = row_level(classifier, args.dataset_version, args.split)
    if rows is not None:
        rm = rows["metrics"]
        metrics["row_accuracy"], metrics["row_macro_f1"] = rm["accuracy"], rm["macro_f1"]
        metrics["row_items_scored"], metrics["rows"] = rm["items_scored"], rm["rows"]
    if args.compare:
        metrics["mcnemar"] = compare(
            results, results_path(args.compare, args.dataset_version, args.split), commit)

    header = {"model_version": classifier.version, "split": args.split,
              "dataset_version": args.dataset_version, "git_commit": commit, "git_dirty": dirty,
              "evidence_manifest_sha256": manifest, "as_of": args.as_of.isoformat(),
              "metrics": metrics, "per_item_fields": per_item_accuracy(results), "rows": rows}
    write_results(results_path(args.model, args.dataset_version, args.split), results, header)
    print(json.dumps(metrics, indent=2, default=str))

    if args.record:
        run_id = record(EXPERIMENTS, classifier.version, args.split, args.dataset_version,
                        metrics, commit + ("-dirty" if dirty else ""), manifest[:12],
                        args.single_change, args.conclusion)
        print(f"Recorded {run_id} in {EXPERIMENTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
