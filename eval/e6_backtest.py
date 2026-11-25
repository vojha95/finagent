"""E6 command: the point-in-time forecast backtest (blueprint 10.5; BC7; BC34).

    python eval/e6_backtest.py run --split train --run-id e6-train-0001
    python eval/e6_backtest.py run --split validation --run-id e6-0001
    python eval/e6_backtest.py record --run-id e6-0001 --single-change "..." --conclusion "..."

run: the shipped system (M1 v3, the post-agent statement set, the BC7 history, build_forecast,
Base) at every origin of every company of the split, as of the run's as_of. Writes
runs/<run-id>/ (header.json, origins.jsonl, points.jsonl, coverage.json); nothing is written
until every company has run, so a crashed run leaves nothing.
- **train:** harness build and debug only. Coverage (origins, points per metric, exclusions with
  reasons) is printed; no error, MAE or MAPE is computed or reported, and nothing is tuned.
- **validation:** once, as pre-registered in docs/e6_prereg.md. Refused if an E6 validation run
  exists, if docs/e6_prereg.md is not committed unchanged, or if the tree is dirty (except
  eval/experiments.csv). Also writes metrics.json and docs/e6_results.md, whatever they show.
- held-out: never (no such choice; the tools refuse held-out issuers too).
record: appends the validation run's row to eval/experiments.csv (Appendix F.2).
Definitions: finagent/evaluation/e6.py and e6_history.py.
"""

import argparse
import csv
import hashlib
import json
import subprocess
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = ROOT / "eval" / "experiments.csv"
RUNS = ROOT / "runs"
PREREG = ROOT / "docs" / "e6_prereg.md"
REPORT = ROOT / "docs" / "e6_results.md"
HASHED = ("configs/e6.yaml", "configs/forecast.yaml", "configs/scenarios.yaml",
          "configs/derivations.yaml", "configs/concept_map.yaml", "configs/model_inputs.yaml",
          "configs/identities.yaml", "configs/correlation_agent.yaml", "models/m1_v3.joblib",
          "models/m1_v3.meta.json", "evidence/manifest.json")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT).stdout.strip()


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def run(args: argparse.Namespace) -> int:
    from finagent.config import check_not_held_out, load_universe
    from finagent.evaluation.e1 import pinned_tickers
    from finagent.evaluation.e6 import (coverage, dirty_paths, earlier_validation_runs,
                                        load_e6_config, metrics, render_results, run_company,
                                        validation_refusal)
    from finagent.tools import FinAgentTools

    out = RUNS / args.run_id
    if out.exists():
        raise SystemExit(f"{out} exists: an E6 run is never overwritten; use a new --run-id")
    dirty = dirty_paths(_git("status", "--porcelain"))
    if args.split == "validation":
        tracked = bool(_git("ls-files", "docs/e6_prereg.md"))
        changed = bool(_git("status", "--porcelain", "docs/e6_prereg.md"))
        refusal = validation_refusal(earlier_validation_runs(RUNS), tracked, changed, dirty)
        if refusal:
            raise SystemExit(refusal)
    config = load_e6_config()
    universe = load_universe()
    tools = FinAgentTools.default(dataset_version=args.dataset_version)
    tickers = pinned_tickers(tools.store, universe, args.split, args.as_of)  # never held-out
    if args.tickers:
        wanted = [t for t in args.tickers.split(",") if t]
        tickers = [t for t in tickers if t in wanted]
    check_not_held_out(tickers, universe, include_held_out=False)
    started = time.perf_counter()
    results = []
    for ticker in tickers:
        found = run_company(tools, ticker, args.as_of, config)
        results.extend(found)
        print(ticker, len(found), "origins;",
              sum(p.scored for r in found for p in r.points), "points scored;",
              [r.forecast_status or r.error for r in found], flush=True)
    header = {
        "run_id": args.run_id, "eval_id": "E6", "split": args.split,
        "as_of": args.as_of.isoformat(), "date": date.today().isoformat(),
        "git_commit": _git("rev-parse", "--short", "HEAD"), "git_dirty": bool(dirty),
        "model_version": tools.classifier.version, "dataset_version": args.dataset_version,
        "scenario": config.scenario, "correlation_agent": True, "companies": tickers,
        "sha256": {p: _sha256(ROOT / p) for p in HASHED},
        "prereg_sha256": _sha256(PREREG), "seconds": round(time.perf_counter() - started, 1),
    }
    cover = coverage(results, config)
    out.mkdir(parents=True)
    (out / "header.json").write_text(json.dumps(header, indent=1) + "\n", encoding="utf-8")
    (out / "origins.jsonl").write_text(
        "".join(json.dumps(r.record(), default=str) + "\n" for r in results), encoding="utf-8")
    (out / "points.jsonl").write_text(
        "".join(json.dumps(p.record(), default=str) + "\n" for r in results for p in r.points),
        encoding="utf-8")
    (out / "coverage.json").write_text(json.dumps(cover, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(cover, indent=1))
    if args.split == "validation":
        found = metrics([p for r in results for p in r.points], config)
        (out / "metrics.json").write_text(json.dumps(found, indent=1) + "\n", encoding="utf-8")
        REPORT.write_text(render_results(header, found, cover), encoding="utf-8")
        print(REPORT.read_text(encoding="utf-8"))
    return 0


def record(args: argparse.Namespace) -> int:
    out = RUNS / args.run_id
    header = json.loads((out / "header.json").read_text(encoding="utf-8"))
    if header["split"] != "validation":
        raise SystemExit("only the validation run is recorded")
    found = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    keep = ("scored", "points", "companies", "mae_model", "mae_naive", "mae_ratio",
            "mape_model", "mape_naive", "mean_ape_difference", "ci95", "beats_naive", "wins",
            "losses", "ties", "exclusions")
    row = {name: {k: m[k] for k in keep} for name, m in found.items()}
    commit = header["git_commit"] + ("-dirty" if header["git_dirty"] else "")
    with EXPERIMENTS.open("a", newline="") as handle:
        csv.writer(handle).writerow([
            args.run_id, header["date"], "E6", "forecast",
            f"forecast-base+{header['model_version']}+bc7-history", args.single_change,
            header["dataset_version"], "validation", json.dumps(row), commit,
            header["sha256"]["evidence/manifest.json"][:12], args.conclusion])
    print(f"Recorded {args.run_id} in {EXPERIMENTS}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run")
    p.add_argument("--split", choices=("train", "validation"), required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 11, 24))
    p.add_argument("--dataset-version", default="v3")
    p.add_argument("--tickers", default="", help="debugging on train only")
    p = sub.add_parser("record")
    p.add_argument("--run-id", required=True)
    p.add_argument("--single-change", required=True)
    p.add_argument("--conclusion", required=True)
    args = parser.parse_args(argv)
    if args.command == "run" and args.split == "validation" and args.tickers:
        raise SystemExit("--tickers is for train debugging; the validation run covers all 8")
    return run(args) if args.command == "run" else record(args)


if __name__ == "__main__":
    raise SystemExit(main())
