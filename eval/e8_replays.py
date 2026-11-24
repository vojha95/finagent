"""E8 command (blueprint 10.6): evidence replays through the planner.

    python eval/e8_replays.py --run-id e8-0001 [--as-of 2026-11-24] [--dataset-version v3]
        [--statements-run statements-0008] [--forecast-run forecast-0006]
        [--record --single-change "..." --conclusion "..."]

Validation split only (finagent.evaluation.e8.check_tickers refuses anything else, with no flag to
allow it). M1 is loaded from models/m1_{dataset_version}.joblib, never retrained here. An existing
run directory is never overwritten.

Writes runs/<run-id>/ (header.json, cases.jsonl, runs.jsonl, metrics.json, trace/trace.jsonl and
trace.sqlite) and docs/e8_replays.md (notes after the marker are kept). --record appends one row
to eval/experiments.csv (Appendix F.2).
"""

import argparse
import csv
import hashlib
import json
import subprocess
from datetime import date
from pathlib import Path

from finagent.config import load_universe
from finagent.evaluation.e1 import pinned_tickers
from finagent.evaluation.e8 import (VALIDATION, check_tickers, load_e8_spec, metrics,
                                    pinned_problems, render_report, run_company, score,
                                    write_run)
from finagent.planner.compile import CompilerResources
from finagent.statements.report import write_report
from finagent.tools import FinAgentTools

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = ROOT / "eval" / "experiments.csv"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--dataset-version", default="v3")
    parser.add_argument("--statements-run", default="statements-0008")
    parser.add_argument("--forecast-run", default="forecast-0006")
    parser.add_argument("--report", type=Path, default=ROOT / "docs" / "e8_replays.md")
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--single-change", default="first E8 run")
    parser.add_argument("--conclusion", default="")
    args = parser.parse_args(argv)

    out = ROOT / "runs" / args.run_id
    if out.exists():
        raise SystemExit(f"{out} exists: an E8 run is never overwritten; use a new --run-id")
    universe = load_universe()
    spec = load_e8_spec()
    resources = CompilerResources.load()
    tools = FinAgentTools.default(dataset_version=args.dataset_version)
    tickers = pinned_tickers(tools.store, universe, VALIDATION, args.as_of)
    check_tickers(tickers, universe)

    trace_dir = out / "trace"
    trace_dir.mkdir(parents=True)
    cases, references, runs = [], {}, []
    for issuer in tickers:
        found, reference, company_runs = run_company(tools, issuer, args.as_of, spec, resources,
                                                     args.run_id, trace_dir)
        cases += found
        runs += company_runs
        if reference is not None:
            references[issuer] = reference
        print(issuer, len(found), "goals", flush=True)

    scored = score(cases, references, spec, args.as_of)
    summary = metrics(scored, spec)
    statements_json = ROOT / "runs" / args.statements_run / "statements.json"
    projections_csv = ROOT / "runs" / args.forecast_run / "projections.csv"
    pinned = pinned_problems(cases, references,
                             statements_json if statements_json.exists() else None,
                             projections_csv if projections_csv.exists() else None,
                             spec.conditions[0].name, spec.float_tolerance)
    summary["complete"]["pinned"] = pinned

    commit = _git("rev-parse", "--short", "HEAD")
    dirty = bool(_git("status", "--porcelain"))
    manifest = hashlib.sha256((ROOT / "evidence" / "manifest.json").read_bytes()).hexdigest()
    header = {"run_id": args.run_id, "eval_id": "E8", "split": VALIDATION, "companies": tickers,
              "as_of": args.as_of.isoformat(), "model_version": tools.classifier.version,
              "dataset_version": args.dataset_version,
              "intent_model": resources.planner.model_version,
              "git_commit": commit, "git_dirty": dirty, "evidence_manifest_sha256": manifest,
              "conditions": [v.name for v in spec.conditions],
              "goals": {k: {"metrics": list(g.metrics), "scenario": g.scenario,
                            "max_steps": g.max_steps} for k, g in spec.goals.items()},
              "origin_years": {t: r.origin for t, r in references.items()},
              "bootstrap_draws": spec.bootstrap_draws, "seed": spec.seed,
              "statements_run": args.statements_run, "forecast_run": args.forecast_run}
    write_run(out, header, scored, summary, runs)
    write_report(args.report, render_report(header, summary, scored, pinned, runs))
    for name in [v.name for v in spec.conditions[1:]] + ["degraded"]:
        m = summary[name]
        print(name, json.dumps({k: m[k] for k in ("correct", "fallback_correct", "fabricated",
                                                  "stale", "leaks", "wrong_flags",
                                                  "new_values")}, default=str))
    print("complete", json.dumps(summary["complete"], default=str)[:2000])

    if args.record:
        record(args.run_id, f"planner-v1+{tools.classifier.version}", args.dataset_version,
               summary, commit + ("-dirty" if dirty else ""), manifest[:12],
               args.single_change, args.conclusion)
        print(f"Recorded {args.run_id} in {EXPERIMENTS}")
    return 0


def record(run_id: str, version: str, dataset_version: str, summary, commit: str,
           manifest_hash: str, single_change: str, conclusion: str) -> None:
    """Append one Appendix F.2 row: the per-condition summary without the case lists."""
    keep = ("correct", "correct_excluding_new_values", "safe", "fallback_correct", "fabricated",
            "stale", "leaks", "wrong_flags", "gate_rejections", "new_values", "categories",
            "by_kind")
    row = {name: {k: m[k] for k in keep} for name, m in summary.items() if name != "complete"}
    complete = summary["complete"]
    row["complete"] = {k: complete[k] for k in ("cases", "with_value", "status", "problems",
                                                "stale", "gate_rejections")}
    row["complete"]["pinned_checked"] = complete["pinned"]["checked"]
    row["complete"]["pinned_differences"] = len(complete["pinned"]["differences"])
    with EXPERIMENTS.open("a", newline="") as handle:
        csv.writer(handle).writerow([run_id, date.today().isoformat(), "E8", "planner", version,
                                     single_change, dataset_version, VALIDATION,
                                     json.dumps(row, default=str), commit, manifest_hash,
                                     conclusion])


if __name__ == "__main__":
    raise SystemExit(main())
