"""Persist investigation traces (blueprint 8.17): JSON lines and SQLite (stdlib), for the
dashboard. Both are append-only: one row per run and one per step, each with the run's as_of.

trace.jsonl: one {"type": "step", ...} line per step, then one {"type": "run", ...} line.
trace.sqlite: tables runs and steps (JSON columns hold lists and records). The run record keeps
the clauses the compiler dropped for having no request content, with any numbers in them
(dropped_clauses, #232); a runs table written before that column existed gains it on the next
write."""

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

JSONL_NAME, SQLITE_NAME = "trace.jsonl", "trace.sqlite"
RUN_COLUMNS = ("run_id", "question", "as_of", "compiler", "status", "stop_reason", "steps_used",
               "reused", "max_steps", "goals", "answer", "dropped_clauses")
RUN_JSON_COLUMNS = ("goals", "dropped_clauses")
STEP_COLUMNS = ("run_id", "step", "as_of", "tool", "goal", "arguments", "eligible_tools",
                "decision", "status", "reason", "reused_by", "observations", "gate",
                "latency_ms")
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, question TEXT, as_of TEXT, "
    "compiler TEXT, status TEXT, stop_reason TEXT, steps_used INTEGER, reused INTEGER, "
    "max_steps INTEGER, goals TEXT, answer TEXT, dropped_clauses TEXT)",
    "CREATE TABLE IF NOT EXISTS steps (run_id TEXT, step INTEGER, as_of TEXT, tool TEXT, "
    "goal TEXT, arguments TEXT, eligible_tools TEXT, decision TEXT, status TEXT, reason TEXT, "
    "reused_by TEXT, observations TEXT, gate TEXT, latency_ms REAL, "
    "PRIMARY KEY (run_id, step))",
)


def _json(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def run_record(result: Any) -> dict[str, Any]:
    """The run-level record of an Investigation (finagent.planner.investigate)."""
    return {
        "run_id": result.run_id, "question": result.question, "as_of": result.as_of.isoformat(),
        "compiler": result.compilation.compiler, "status": result.status,
        "stop_reason": result.stop_reason, "steps_used": result.steps_used,
        "reused": result.reused, "max_steps": result.max_steps,
        "goals": [g.record() for g in result.goals], "answer": result.answer,
        "dropped_clauses": [asdict(d) for d in result.compilation.dropped],
    }


def step_records(result: Any) -> list[dict[str, Any]]:
    records = []
    for s in result.trace:
        d = asdict(s)
        d.pop("as_of", None)
        records.append({"run_id": result.run_id, "as_of": result.as_of.isoformat(), **d})
    return records


def write_jsonl(result: Any, directory: Path) -> Path:
    path = Path(directory) / JSONL_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for record in step_records(result):
            fh.write(_json({"type": "step", **record}) + "\n")
        fh.write(_json({"type": "run", **run_record(result)}) + "\n")
    return path


def write_sqlite(result: Any, directory: Path) -> Path:
    path = Path(directory) / SQLITE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    run = run_record(result)
    with sqlite3.connect(path) as db:
        for statement in SCHEMA:
            db.execute(statement)
        present = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
        if "dropped_clauses" not in present:  # a table written before #232
            db.execute("ALTER TABLE runs ADD COLUMN dropped_clauses TEXT")
        db.execute(f"INSERT INTO runs ({', '.join(RUN_COLUMNS)}) "
                   f"VALUES ({', '.join('?' * len(RUN_COLUMNS))})",
                   [_json(run[c]) if c in RUN_JSON_COLUMNS else run[c] for c in RUN_COLUMNS])
        for record in step_records(result):
            db.execute(f"INSERT INTO steps VALUES ({', '.join('?' * len(STEP_COLUMNS))})",
                       [record[c] if c in ("run_id", "step", "as_of", "tool", "decision",
                                           "status", "reason", "latency_ms")
                        else _json(record[c]) for c in STEP_COLUMNS])
    return path


def write_trace(result: Any, directory: Path | str) -> tuple[Path, Path]:
    """Append one investigation to trace.sqlite and trace.jsonl in `directory` (SQLite first:
    a run id already stored is refused before anything is appended)."""
    sqlite_path = write_sqlite(result, Path(directory))
    return write_jsonl(result, Path(directory)), sqlite_path


def read_runs(directory: Path | str) -> list[dict[str, Any]]:
    """The runs table (for the dashboard and tests)."""
    with sqlite3.connect(Path(directory) / SQLITE_NAME) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute("SELECT * FROM runs ORDER BY rowid")]


def read_steps(directory: Path | str, run_id: str) -> list[dict[str, Any]]:
    with sqlite3.connect(Path(directory) / SQLITE_NAME) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute("SELECT * FROM steps WHERE run_id = ? ORDER BY step",
                                            (run_id,))]
