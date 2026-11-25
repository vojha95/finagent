"""E5 command (blueprint 10.4): the planner end to end on the frozen gold set.

    python eval/e5_planner.py run --run-id e5-0001 --compiler m0 [--record ...]
    python eval/e5_planner.py run --run-id e5-0002 --compiler m1 [--record ...]
    python eval/e5_planner.py report --runs e5-0001 e5-0002 [--posthoc e5-0003 --changes "..."]
    python eval/e5_planner.py record --run-id e5-0001 --single-change "..." --conclusion "..."

run: refuses a gold set that is not frozen or whose hashes changed, and an existing run
directory. Every question goes through finagent.planner.investigate(question, as_of) with the
compiler (m0: the regex grammar; m1: the trained compiler at the configured threshold) and the
cell's selected company; validation companies only. Writes runs/<run-id>/ (header.json,
questions.jsonl, metrics.json, trace/). Run one at a time: the runs share no state but are
compared, so never run two at once. --record appends one row to eval/experiments.csv.

report: renders docs/e5_v1.md from the runs (notes after the marker are kept); --posthoc adds a
run made after changes prompted by the last run's errors as section 9, labelled post-hoc, not
blind (#234), without changing sections 1-8. record: appends
a finished run's row to eval/experiments.csv (Appendix F.2), so the conclusion is written after
the result is seen.
Definitions: finagent/evaluation/e5.py.
"""

import argparse
import csv
import hashlib
import json
import subprocess
import time
from datetime import date
from pathlib import Path

from finagent.config import _load_yaml, load_universe
from finagent.evaluation.e5 import (VALIDATION, Pinned, load_scores, m1_diagnostics, metrics,
                                    render_report, run_question, write_run)
from finagent.evaluation.e5_gold import (E5_PATH, file_sha256, load_e5_spec, load_gold,
                                         load_grid, require_frozen)
from finagent.planner.compile import CompilerResources, GrammarParser, load_trained_parser
from finagent.statements.report import write_report
from finagent.tools import FinAgentTools

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = ROOT / "eval" / "experiments.csv"
RUNS = ROOT / "runs"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT).stdout.strip()


def run(args: argparse.Namespace) -> int:
    out = RUNS / args.run_id
    if out.exists():
        raise SystemExit(f"{out} exists: an E5 run is never overwritten; use a new --run-id")
    spec = load_e5_spec()
    files = {k: ROOT / v for k, v in spec.files.items()}
    require_frozen(files["hash"])
    extra = _load_yaml(E5_PATH)
    records = load_gold(files["gold"])
    groups = {c.cell: c.group for c in load_grid(files["grid"])}
    universe = load_universe()
    named = {g["issuer"] for r in records for g in r["goals"] or ()} | \
        {r["selected_issuer"] for r in records if r["selected_issuer"]}
    refused = sorted(t for t in named if universe.get(t) != VALIDATION)
    if refused:
        raise SystemExit(f"E5 runs validation companies only; the gold set names {refused}")

    resources = CompilerResources.load()
    if args.compiler == "m0":
        parser, threshold, diagnose = GrammarParser(), None, None
    else:
        parser = load_trained_parser(resources)
        threshold = parser.threshold
        diagnose = m1_diagnostics(resources, parser.classifier)
    tools = FinAgentTools.default(dataset_version=args.dataset_version)
    pinned_runs = dict(extra["pinned_runs"])
    pinned = Pinned.load(RUNS, sorted(t for t, s in universe.items() if s == VALIDATION),
                         **pinned_runs)
    trace_dir = out / "trace"
    trace_dir.mkdir(parents=True)
    started = time.perf_counter()
    results = []
    for n, record in enumerate(records, 1):
        r = run_question(record, groups[record["cell"]], tools=tools, parser=parser,
                         resources=resources, as_of=spec.as_of, universe=universe,
                         pinned=pinned, run_id=args.run_id, trace_dir=trace_dir,
                         diagnose=diagnose)
        results.append(r)
        print(f"{n:3d} {r.id} {r.outcome:13s} {'ok ' if r.compiled_correct else 'ERR'} "
              f"answer={r.answer_correct} values={r.values} problems={len(r.problems)} "
              f"{r.seconds:.1f}s", flush=True)
    seconds = time.perf_counter() - started
    summary = metrics(results)
    commit = _git("rev-parse", "--short", "HEAD")
    dirty = bool(_git("status", "--porcelain"))
    manifest = hashlib.sha256((ROOT / "evidence" / "manifest.json").read_bytes()).hexdigest()
    header = {"run_id": args.run_id, "eval_id": "E5", "split": VALIDATION,
              "as_of": spec.as_of.isoformat(), "compiler": args.compiler,
              "compiler_name": parser.name, "threshold": threshold,
              "intent_model": resources.planner.model_version,
              "dataset_version": args.dataset_version,
              "gold_file": str(files["gold"].relative_to(ROOT)),
              "gold_sha256": file_sha256(files["gold"]),
              "grid_sha256": file_sha256(files["grid"]),
              "questions": len(records), "pinned": pinned_runs,
              "git_commit": commit, "git_dirty": dirty, "evidence_manifest_sha256": manifest,
              "seconds": round(seconds, 1), "llm_fallback": "not run (no external AI API; #121)"}
    write_run(out, header, results, summary)
    o = summary["overall"]
    print(json.dumps({k: o[k] for k in ("goal_accuracy", "coverage", "precision",
                                        "compiled_correct", "answer_correct", "values",
                                        "unverified_values")}, default=str))
    if args.record:
        record_row(args.run_id, parser.name, args.dataset_version, summary,
                   commit + ("-dirty" if dirty else ""), manifest[:12], args.single_change,
                   args.conclusion)
        print(f"Recorded {args.run_id} in {EXPERIMENTS}")
    return 0


def record_row(run_id: str, version: str, dataset_version: str, summary, commit: str,
               manifest_hash: str, single_change: str, conclusion: str) -> None:
    """Append one Appendix F.2 row: the headline measures overall and by author."""
    keep = ("goal_accuracy", "coverage", "precision", "false_clarification",
            "clarification_correct", "compiled_correct", "answer_correct", "values",
            "unverified_values")
    row = {"overall": {k: summary["overall"][k] for k in keep},
           "by_author": {a: {k: s[k] for k in keep} for a, s in summary["by_author"].items()},
           "slot_f1": {k: v["f1"] for k, v in summary["overall"]["slots"].items()},
           "clarification": {k: v["correct"]["rate"]
                             for k, v in summary["overall"]["clarification"].items()},
           "llm_fallback": "not run"}
    with EXPERIMENTS.open("a", newline="") as handle:
        csv.writer(handle).writerow([run_id, date.today().isoformat(), "E5", "planner", version,
                                     single_change, dataset_version, VALIDATION,
                                     json.dumps(row, default=str), commit, manifest_hash,
                                     conclusion])


def record(args: argparse.Namespace) -> int:
    directory = RUNS / args.run_id
    header = json.loads((directory / "header.json").read_text(encoding="utf-8"))
    summary = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
    record_row(args.run_id, header["compiler_name"], header["dataset_version"], summary,
               header["git_commit"] + ("-dirty" if header["git_dirty"] else ""),
               header["evidence_manifest_sha256"][:12], args.single_change, args.conclusion)
    print(f"Recorded {args.run_id} in {EXPERIMENTS}")
    return 0


def report(args: argparse.Namespace) -> int:
    runs = []
    for run_id in args.runs:
        directory = RUNS / run_id
        header = json.loads((directory / "header.json").read_text(encoding="utf-8"))
        runs.append((header, load_scores(directory / "questions.jsonl")))
    point_out = _load_yaml(E5_PATH).get("point_out") or {}
    posthoc = None
    if args.posthoc:
        directory = RUNS / args.posthoc
        posthoc = (json.loads((directory / "header.json").read_text(encoding="utf-8")),
                   load_scores(directory / "questions.jsonl"))
    write_report(args.out, render_report(runs, point_out, posthoc, args.changes))
    print(f"Wrote {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--run-id", required=True)
    r.add_argument("--compiler", choices=("m0", "m1"), required=True)
    r.add_argument("--dataset-version", default="v3")
    r.add_argument("--record", action="store_true")
    r.add_argument("--single-change", default="")
    r.add_argument("--conclusion", default="")
    c = sub.add_parser("record")
    c.add_argument("--run-id", required=True)
    c.add_argument("--single-change", required=True)
    c.add_argument("--conclusion", required=True)
    p = sub.add_parser("report")
    p.add_argument("--runs", nargs="+", required=True)
    p.add_argument("--out", type=Path, default=ROOT / "docs" / "e5_v1.md")
    p.add_argument("--posthoc", help="a run made after changes prompted by the last run's "
                   "errors: section 9, labelled post-hoc, not blind (#234)")
    p.add_argument("--changes", default="", help="what changed before the post-hoc run")
    args = parser.parse_args(argv)
    return {"run": run, "record": record, "report": report}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
