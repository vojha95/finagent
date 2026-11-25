"""E7 light command (blueprint 10.7; Phase 9 cut-down, BC33): planner latency on 20 fixed E5
canonical questions, validation companies only.

    python eval/e7_light.py run --run-id e7-0001
    python eval/e7_light.py record --run-id e7-0001 --single-change "..." --conclusion "..."

run: refuses a gold set whose hash changed and an existing run directory. In one fresh process
it times the start (planner, intent model, tools), then a cold pass and a warm pass over the
same questions. Writes runs/<run-id>/ (header.json, questions.jsonl, metrics.json) and
docs/e7_light.md. No trace is persisted. record: appends the run's row to eval/experiments.csv
(Appendix F.2) after the result is seen. Definitions: finagent/evaluation/e7.py.
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
REPORT = ROOT / "docs" / "e7_light.md"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT).stdout.strip()


def run(args: argparse.Namespace) -> int:
    started = time.perf_counter()  # cold start: imports and loads below are inside the timing
    from finagent.config import load_universe
    from finagent.evaluation.e5_gold import (file_sha256, load_e5_spec, load_gold, load_grid,
                                             require_frozen)
    from finagent.evaluation.e7 import (REFERENCE_RUN, environment, metrics, render_report,
                                        run_pass, select_questions, timing_record)
    from finagent.planner.compile import CompilerResources, load_trained_parser
    from finagent.planner.investigate import investigate
    from finagent.tools import FinAgentTools

    out = RUNS / args.run_id
    if out.exists():
        raise SystemExit(f"{out} exists: an E7 run is never overwritten; use a new --run-id")
    spec = load_e5_spec()
    files = {k: ROOT / v for k, v in spec.files.items()}
    require_frozen(files["hash"])
    universe = load_universe()
    groups = {c.cell: c.group for c in load_grid(files["grid"])}
    questions = select_questions(load_gold(files["gold"]), groups, universe)
    resources = CompilerResources.load()
    parser = load_trained_parser(resources)
    tools = FinAgentTools.default(dataset_version=args.dataset_version)
    startup = time.perf_counter() - started
    print(f"start {startup:.2f}s; {len(questions)} questions", flush=True)

    def ask(record):
        return investigate(record["question"], spec.as_of, tools=tools, parser=parser,
                           resources=resources, selected_issuer=record["selected_issuer"] or None,
                           run_id=f"{args.run_id}:{record['id']}")

    cold = run_pass(questions, ask, "cold", echo=lambda s: print(s, flush=True))
    warm = run_pass(questions, ask, "warm", echo=lambda s: print(s, flush=True))
    reference_path = RUNS / REFERENCE_RUN / "questions.jsonl"
    reference = {}
    if reference_path.exists():
        for line in reference_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            reference[row["id"]] = row["status"]
    summary = metrics(startup, cold, warm, reference)
    header = {"run_id": args.run_id, "eval_id": "E7", "split": "validation",
              "as_of": spec.as_of.isoformat(), "date": date.today().isoformat(),
              "model_version": tools.classifier.version,
              "intent_model": resources.planner.model_version,
              "dataset_version": args.dataset_version,
              "gold_file": str(files["gold"].relative_to(ROOT)),
              "gold_sha256": file_sha256(files["gold"]),
              "question_ids": [q["id"] for q in questions],
              "git_commit": _git("rev-parse", "--short", "HEAD"),
              "git_dirty": bool(_git("status", "--porcelain")),
              "evidence_manifest_sha256": hashlib.sha256(
                  (ROOT / "evidence" / "manifest.json").read_bytes()).hexdigest(),
              "environment": environment(), "trace": "not persisted (BC33)"}
    rows = [timing_record(t) for t in (*cold, *warm)]
    out.mkdir(parents=True)
    (out / "header.json").write_text(json.dumps(header, indent=1) + "\n", encoding="utf-8")
    (out / "metrics.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    (out / "questions.jsonl").write_text(
        "".join(json.dumps(r, default=str) + "\n" for r in rows), encoding="utf-8")
    REPORT.write_text(render_report(header, summary, rows), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("startup_seconds", "cold_start_seconds", "cold",
                                              "warm", "failures")}, indent=1))
    return 0


def record(args: argparse.Namespace) -> int:
    out = RUNS / args.run_id
    header = json.loads((out / "header.json").read_text(encoding="utf-8"))
    m = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    row = {k: m[k] for k in ("questions", "startup_seconds", "cold_start_seconds", "cold", "warm",
                             "failures", "same_status_cold_warm", "same_status_as_reference",
                             "peak_rss_mb")}
    commit = header["git_commit"] + ("-dirty" if header["git_dirty"] else "")
    with EXPERIMENTS.open("a", newline="") as handle:
        csv.writer(handle).writerow([
            args.run_id, header["date"], "E7", "planner",
            f"planner-v1+{header['model_version']}+intent-{header['intent_model']}", args.single_change,
            header["dataset_version"], "validation", json.dumps(row), commit,
            header["evidence_manifest_sha256"][:12], args.conclusion])
    print(f"Recorded {args.run_id} in {EXPERIMENTS}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run")
    p.add_argument("--run-id", required=True)
    p.add_argument("--dataset-version", default="v3")
    p = sub.add_parser("record")
    p.add_argument("--run-id", required=True)
    p.add_argument("--single-change", required=True)
    p.add_argument("--conclusion", required=True)
    args = parser.parse_args(argv)
    return run(args) if args.command == "run" else record(args)


if __name__ == "__main__":
    raise SystemExit(main())
