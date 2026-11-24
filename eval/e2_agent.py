"""E2 command (blueprint 10.2): the Correlation Agent with and without injected errors.

    python eval/e2_agent.py --run-id e2-0002 [--as-of 2026-11-24] [--dataset-version v3]
        [--compare-run e2-0001] [--provenance "..."] [--agent-version v1.1]
        [--record --single-change "..." --conclusion-clean "..." --conclusion-corrupted "..."]

Validation split only: E2 never opens a held-out company (finagent.evaluation.e2.load_cases
refuses one, with no flag to allow it). Evaluation mode: the agent never sees a tag. M1 is
loaded from models/m1_{dataset_version}.joblib, never retrained here. An existing run directory
is never overwritten.

Writes runs/<run-id>/ (header.json, injections.csv, cases.jsonl, metrics.json) and
docs/e2_agent.md (notes after the marker are kept). --compare-run adds a side-by-side section
with an earlier run and records whether the injections are identical. --record appends two rows to
eval/experiments.csv (Appendix F.2): <run-id>/clean and <run-id>/corrupted, with the metrics at
the configured prior and the prior sensitivity.
"""

import argparse
import csv
import hashlib
import json
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

from finagent.config import load_agent_config, load_e1_equivalences, load_e2_config, \
    load_universe
from finagent.evaluation.e1 import pinned_tickers
from finagent.evaluation.e2 import CLEAN, CORRUPTED, E2Run, injections_text, load_cases, \
    load_previous, render_report, run_e2, write_run
from finagent.evidence import EvidenceStore
from finagent.statements.build import StatementConfigs
from finagent.statements.report import write_report

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = ROOT / "eval" / "experiments.csv"
SPLIT = "validation"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT).stdout.strip()


def record(path: Path, run_id: str, version: str, dataset_version: str, run: E2Run,
           commit: str, manifest_hash: str, single_change: str,
           conclusions: dict[str, str]) -> None:
    """Append one Appendix F.2 row per condition."""
    base = run.priors[0]
    with path.open("a", newline="") as handle:
        writer = csv.writer(handle)
        for condition in (CLEAN, CORRUPTED):
            metrics: dict[str, Any] = {"prior": base, **run.metrics[base, condition],
                                       "prior_sensitivity": {
                                           f"{p:g}": _summary(run.metrics[p, condition])
                                           for p in run.priors[1:]}}
            writer.writerow([f"{run_id}/{condition}", date.today().isoformat(), "E2",
                             "correlation_agent", version, f"{single_change} ({condition})",
                             dataset_version, SPLIT, json.dumps(metrics, default=str), commit,
                             manifest_hash, conclusions[condition]])


def _summary(m: dict[str, Any]) -> dict[str, Any]:
    keys = ("field_accuracy_after", "wrongly_changed", "fixed", "actions_mean")
    found = {k: m[k] for k in keys}
    found["mcnemar_p"] = m["mcnemar"]["p_value"]
    if "injected" in m:
        found["detected"] = m["injected"]["detected"]["rate"]
        found["corrected"] = m["injected"]["corrected"]["rate"]
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--dataset-version", default="v3")
    parser.add_argument("--report", type=Path, default=ROOT / "docs" / "e2_agent.md")
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--single-change", default="Correlation Agent v1 on M1 readings, "
                        "evaluation mode")
    parser.add_argument("--conclusion-clean", default="")
    parser.add_argument("--conclusion-corrupted", default="")
    parser.add_argument("--agent-version", default="v1",
                        help="label recorded in experiments.csv (correlation-agent-<label>)")
    parser.add_argument("--compare-run", default=None,
                        help="an earlier E2 run in runs/ to report side by side")
    parser.add_argument("--provenance", default="",
                        help="what was decided after which run (e.g. not blind); in the report")
    args = parser.parse_args(argv)

    out = ROOT / "runs" / args.run_id
    if out.exists():
        raise SystemExit(f"{out} exists: an E2 run is never overwritten; use a new --run-id")
    previous = load_previous(ROOT / "runs" / args.compare_run) if args.compare_run else None

    from finagent.training.m1 import load_model

    store = EvidenceStore(ROOT / "evidence")
    universe = load_universe()
    tickers = pinned_tickers(store, universe, SPLIT, args.as_of)
    classifier = load_model(ROOT / "models", args.dataset_version,
                            ROOT / "datasets" / f"rows_{args.dataset_version}.meta.json")
    configs = StatementConfigs.load()
    agent, e2 = load_agent_config(), load_e2_config()
    equivalences = load_e1_equivalences(concept_map=configs.concept_map)
    cases, keys = load_cases(store, tickers, classifier, configs, args.as_of, equivalences,
                             universe)
    run = run_e2(cases, keys, configs, agent, e2, args.as_of)

    commit = _git("rev-parse", "--short", "HEAD")
    dirty = bool(_git("status", "--porcelain"))
    manifest = hashlib.sha256((ROOT / "evidence" / "manifest.json").read_bytes()).hexdigest()
    header = {"run_id": args.run_id, "eval_id": "E2", "split": SPLIT, "companies": tickers,
              "as_of": args.as_of.isoformat(), "model_version": classifier.version,
              "dataset_version": args.dataset_version, "git_commit": commit, "git_dirty": dirty,
              "evidence_manifest_sha256": manifest, "seed": e2.seed,
              "injection_types": list(e2.injection_types), "priors": list(run.priors),
              "bootstrap_draws": e2.bootstrap_draws, "max_actions": agent.max_actions,
              "swap_min_probability": agent.swap_min_probability,
              "agent_version": args.agent_version, "company_years": len(cases),
              "provenance": args.provenance}
    if previous is not None:
        header["compare_run"] = args.compare_run
        header["injections_identical_to_compare_run"] = \
            injections_text(run).splitlines() == previous.injections.splitlines()
    write_run(out, run, header)
    write_report(args.report, render_report(run, header, previous))
    for condition in (CLEAN, CORRUPTED):
        print(condition, json.dumps(run.metrics[run.priors[0], condition], default=str))
    if previous is not None:
        print("injections identical to", args.compare_run,
              header["injections_identical_to_compare_run"])

    if args.record:
        record(EXPERIMENTS, args.run_id,
               f"correlation-agent-{args.agent_version}+{classifier.version}",
               args.dataset_version, run, commit + ("-dirty" if dirty else ""), manifest[:12],
               args.single_change, {CLEAN: args.conclusion_clean,
                                    CORRUPTED: args.conclusion_corrupted})
        print(f"Recorded {args.run_id}/clean and /corrupted in {EXPERIMENTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
