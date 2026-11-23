"""E1 command (blueprint 10.1): score a document-channel model on one company split.

    python eval/e1_document.py --model m0 --split validation [--as-of 2026-12-31] [--record]

--split held_out also needs --include-held-out: the held-out companies are scored once per
model version, in the final E1 run, and never used for debugging (blueprint 9.2).

M1 trains on the train split's labelled rows (datasets/rows_{version}.parquet) before scoring.
--record appends the run to eval/experiments.csv with the git commit and manifest hash.
"""

import argparse
import csv
import hashlib
import json
import subprocess
from datetime import date
from pathlib import Path

from finagent.config import load_universe
from finagent.document.classify import RulesClassifier
from finagent.evaluation.e1 import (
    bootstrap_ci,
    error_taxonomy,
    field_accuracy,
    pinned_tickers,
    run_e1,
)
from finagent.evidence import EvidenceStore

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = ROOT / "eval" / "experiments.csv"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["m0"], default="m0")
    parser.add_argument("--split", default="validation")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--include-held-out", action="store_true",
                        help="required with --split held_out (final E1 run only)")
    args = parser.parse_args(argv)

    store = EvidenceStore(ROOT / "evidence")
    universe = load_universe()
    tickers = pinned_tickers(store, universe, args.split, args.as_of, args.include_held_out)
    classifier = RulesClassifier()
    results = run_e1(store, tickers, args.as_of, classifier, universe=universe,
                     include_held_out=args.include_held_out)
    accuracy, ci, errors = field_accuracy(results), bootstrap_ci(results), error_taxonomy(results)
    metrics = {"field_accuracy": accuracy, "ci95": ci, "fields": len(results),
               "companies": tickers, "errors": errors}
    print(json.dumps(metrics, indent=2, default=str))

    if args.record:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                                text=True, cwd=ROOT).stdout.strip()
        manifest = hashlib.sha256((ROOT / "evidence" / "manifest.json").read_bytes()).hexdigest()
        with EXPERIMENTS.open() as handle:
            run_id = sum(1 for _ in handle)
        with EXPERIMENTS.open("a", newline="") as handle:
            csv.writer(handle).writerow([
                f"e1-{run_id:04d}", date.today().isoformat(), "E1", "document_channel",
                classifier.version, "", "", args.split, json.dumps(metrics, default=str),
                commit, manifest[:12], "",
            ])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
