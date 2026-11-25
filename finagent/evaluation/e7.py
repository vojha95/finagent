"""E7 light (blueprint 10.7; Phase 9 cut-down, BC33): planner latency, failures, cold vs warm.

    python eval/e7_light.py run --run-id e7-0001
    python eval/e7_light.py record --run-id e7-0001 --single-change "..." --conclusion "..."

Definitions (fixed before e7-0001 ran):

- Questions: 20 from the frozen E5 gold set (gold/intents_test.jsonl, hash-checked first), author
  "canonical" only. A question is eligible when every company it names (its gold goals' issuers
  and its selected company) is in the validation split, and its trap type is not
  `outside_universe` (it names a company outside the universe). Eligible questions are grouped
  by their coverage-grid group, groups ordered by their first question id, questions by id.
  Selection is round robin over the groups (the first question of each group, then the second,
  ...) until 20 are chosen.
- Each question goes through finagent.planner.investigate(question, as_of) with the shipped
  compiler (intent model from configs/planner.yaml), M1 v3 and the question's selected company,
  exactly as E5 does; no trace is persisted.
- Cold start: a fresh process. startup_seconds is the time to import the planner and load the
  compiler resources, the intent model and the tools (evidence store, M1 v3); Python's own start
  is not included. The cold pass then runs the 20 questions once, in order: per-company
  statement sets and the retrieval models load lazily inside it.
- Warm pass: the same 20 questions again, in the same process and order.
- Latency: wall-clock seconds per question (time.perf_counter around investigate). p50 and p95
  per pass: numpy.percentile, linear interpolation.
- Failure: investigate raised an exception. A status is a result, never a failure.
- Consistency (reported): a question's status equal in the two passes, and equal to its status
  in the reference E5 run (e5-0003, the same intent v3 compiler).
- Peak memory: ru_maxrss of the process after both passes (bytes on macOS, KiB on Linux).
"""

import platform
import resource
import sys
import time
import traceback
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from finagent.evaluation.e5 import frac

QUESTIONS = 20
EXCLUDED_TRAPS = ("outside_universe",)
VALIDATION = "validation"
REFERENCE_RUN = "e5-0003"


@dataclass(frozen=True)
class Timing:
    id: str
    group: str
    question: str
    selected_issuer: str
    pass_: str  # cold | warm
    order: int
    seconds: float
    status: str
    stop_reason: str
    steps_used: int | None
    error: str


def eligible(record: Mapping[str, Any], universe: Mapping[str, str]) -> bool:
    named = {g["issuer"] for g in record["goals"] or ()}
    if record["selected_issuer"]:
        named.add(record["selected_issuer"])
    return (record["author"] == "canonical" and record["trap_type"] not in EXCLUDED_TRAPS
            and all(universe.get(t) == VALIDATION for t in named))


def select_questions(records: Sequence[Mapping[str, Any]], groups: Mapping[str, str],
                     universe: Mapping[str, str], n: int = QUESTIONS) -> list[dict[str, Any]]:
    """The fixed selection (module docstring): round robin over groups, ids in order."""
    by_group: dict[str, list[dict[str, Any]]] = {}
    for r in sorted(records, key=lambda r: r["id"]):
        if eligible(r, universe):
            by_group.setdefault(groups[r["cell"]], []).append({**r, "group": groups[r["cell"]]})
    chosen: list[dict[str, Any]] = []
    depth = 0
    while len(chosen) < n and any(len(q) > depth for q in by_group.values()):
        for questions in by_group.values():
            if depth < len(questions) and len(chosen) < n:
                chosen.append(questions[depth])
        depth += 1
    return chosen


def timed(ask: Callable[[Mapping[str, Any]], Any], record: Mapping[str, Any], pass_: str,
          order: int) -> Timing:
    started = time.perf_counter()
    try:
        inv = ask(record)
        status, stop, steps, error = inv.status, inv.stop_reason, inv.steps_used, ""
    except Exception as exc:  # a failure is recorded, never hidden  # noqa: BLE001
        status, stop, steps = "FAILED", "", None
        error = f"{type(exc).__name__}: {exc} | {traceback.format_exc(limit=3)}"
    seconds = time.perf_counter() - started
    return Timing(record["id"], record["group"], record["question"], record["selected_issuer"],
                  pass_, order, round(seconds, 4), status, stop, steps, error)


def run_pass(questions: Sequence[Mapping[str, Any]], ask: Callable[[Mapping[str, Any]], Any],
             pass_: str, echo: Callable[[str], None] | None = None) -> list[Timing]:
    found = []
    for order, record in enumerate(questions, 1):
        t = timed(ask, record, pass_, order)
        found.append(t)
        if echo:
            echo(f"{pass_} {order:2d} {t.id} {t.status:13s} {t.seconds:7.2f}s "
                 f"{'ERROR ' + t.error.splitlines()[0] if t.error else ''}")
    return found


def latency(seconds: Sequence[float]) -> dict[str, Any]:
    values = np.asarray(seconds, dtype=float)
    return {"n": int(values.size), "p50": float(np.percentile(values, 50)),
            "p95": float(np.percentile(values, 95)), "mean": float(values.mean()),
            "min": float(values.min()), "max": float(values.max()),
            "total": float(values.sum())}


def peak_rss_mb() -> float:
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw / 1e6 if sys.platform == "darwin" else raw / 1e3


def metrics(startup_seconds: float, cold: Sequence[Timing], warm: Sequence[Timing],
            reference: Mapping[str, str]) -> dict[str, Any]:
    both = [*cold, *warm]
    failed = [t for t in both if t.error]
    warm_by_id = {t.id: t for t in warm}
    with_ref = [t for t in cold if t.id in reference]
    return {
        "questions": len(cold),
        "startup_seconds": round(startup_seconds, 3),
        "cold_start_seconds": round(startup_seconds + (cold[0].seconds if cold else 0.0), 3),
        "cold": latency([t.seconds for t in cold]),
        "warm": latency([t.seconds for t in warm]),
        "failures": frac(len(failed), len(both)),
        "failures_by_pass": {"cold": sum(1 for t in cold if t.error),
                             "warm": sum(1 for t in warm if t.error)},
        "statuses": dict(Counter(t.status for t in cold)),
        "same_status_cold_warm": frac(sum(1 for t in cold if t.id in warm_by_id
                                          and warm_by_id[t.id].status == t.status), len(cold)),
        "same_status_as_reference": {"reference": REFERENCE_RUN, **frac(
            sum(1 for t in with_ref if reference[t.id] == t.status), len(with_ref))},
        "groups": dict(Counter(t.group for t in cold)),
        "peak_rss_mb": round(peak_rss_mb(), 1),
    }


def environment() -> dict[str, Any]:
    import os

    return {"python": platform.python_version(), "platform": platform.platform(),
            "machine": platform.machine(), "processor": platform.processor(),
            "cpu_count": os.cpu_count()}


def timing_record(t: Timing) -> dict[str, Any]:
    d = asdict(t)
    d["pass"] = d.pop("pass_")
    return d


def render_report(header: Mapping[str, Any], m: Mapping[str, Any],
                  rows: Sequence[Mapping[str, Any]]) -> str:
    """docs/e7_light.md from the run's header, metrics and per-question timings."""
    env = header["environment"]
    lines = [
        f"# E7 light: latency, failures, cold vs warm ({header['run_id']})", "",
        "Phase 9 cut-down (BC33). Written by `python eval/e7_light.py run`; definitions in "
        "`finagent/evaluation/e7.py`. Validation companies only; no held-out company is named.",
        "",
        "## Run", "",
        f"- Commit {header['git_commit']}{' (dirty)' if header['git_dirty'] else ''}, evidence "
        f"manifest {header['evidence_manifest_sha256'][:12]}…, as_of {header['as_of']}.",
        f"- Document model {header['model_version']}; intent model {header['intent_model']}.",
        f"- Machine: {env['platform']}, {env['machine']}, {env['cpu_count']} CPUs, Python "
        f"{env['python']}. Local, CPU only, offline.",
        f"- Questions: {m['questions']} canonical E5 questions (gold sha256 "
        f"{header['gold_sha256'][:12]}…), groups: "
        + ", ".join(f"{g} {k}" for g, k in m["groups"].items()) + ".", "",
        "## Results", "",
        "| Measure | Cold pass | Warm pass |", "|---|---|---|",
        f"| p50 latency (s) | {m['cold']['p50']:.2f} | {m['warm']['p50']:.2f} |",
        f"| p95 latency (s) | {m['cold']['p95']:.2f} | {m['warm']['p95']:.2f} |",
        f"| Mean / max (s) | {m['cold']['mean']:.2f} / {m['cold']['max']:.2f} | "
        f"{m['warm']['mean']:.2f} / {m['warm']['max']:.2f} |",
        f"| Total for 20 questions (s) | {m['cold']['total']:.1f} | {m['warm']['total']:.1f} |",
        f"| Failures (exceptions) | {m['failures_by_pass']['cold']} | "
        f"{m['failures_by_pass']['warm']} |", "",
        f"- **Cold start:** loading the planner, the intent model and the tools took "
        f"{m['startup_seconds']:.2f} s; with the first question, {m['cold_start_seconds']:.2f} s.",
        f"- **Failures:** {m['failures']['k']} of {m['failures']['n']} calls raised an exception.",
        f"- **Statuses (cold pass):** "
        + ", ".join(f"{s} {k}" for s, k in sorted(m["statuses"].items())) + ".",
        f"- **Same status in both passes:** {m['same_status_cold_warm']['k']} of "
        f"{m['same_status_cold_warm']['n']}. **Same status as {m['same_status_as_reference']['reference']}:** "
        f"{m['same_status_as_reference']['k']} of {m['same_status_as_reference']['n']}.",
        f"- **Peak memory (RSS):** {m['peak_rss_mb']:.0f} MB.", "",
        "## Per question", "",
        "| # | Id | Group | Cold (s) | Warm (s) | Status | Question |", "|---|---|---|---|---|---|---|",
    ]
    warm = {r["id"]: r for r in rows if r["pass"] == "warm"}
    for r in (r for r in rows if r["pass"] == "cold"):
        w = warm.get(r["id"], {})
        lines.append(f"| {r['order']} | {r['id']} | {r['group']} | {r['seconds']:.2f} | "
                     f"{w.get('seconds', float('nan')):.2f} | {r['status']} | {r['question']} |")
    lines += ["", "## Limits", "",
              "- One machine, one run, 20 questions: an indication of local latency, not a load "
              "test. The full E7 (load, logging, cost) was cut (BC33, #318).",
              "- The cold pass includes lazy per-company work (statement sets, the retrieval "
              "models), so its p95 reflects the first question about each company.", ""]
    return "\n".join(lines)
