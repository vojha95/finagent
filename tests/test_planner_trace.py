"""Trace persistence (blueprint 8.17): JSON lines and SQLite, append-only, as_of on every row."""

import json
import sqlite3

import pytest
from planner_support import resources
from tools_support import AS_OF, KeywordClassifier, StubTools

from finagent.planner.compile import TrainedParser
from finagent.planner.investigate import investigate
from finagent.planner.trace import (JSONL_NAME, SQLITE_NAME, read_runs, read_steps, run_record,
                                    step_records, write_jsonl, write_sqlite, write_trace)

R = resources()


def result(run_id="r1"):
    return investigate("Forecast Microsoft for next year.", AS_OF, tools=StubTools(),
                       parser=TrainedParser(KeywordClassifier({}, "forecast"), 0.8),
                       resources=R, run_id=run_id)


def test_records_carry_run_id_and_as_of():
    r = result()
    run = run_record(r)
    assert run["run_id"] == "r1" and run["as_of"] == "2026-11-24" and run["steps_used"] == 2
    steps = step_records(r)
    assert [s["step"] for s in steps] == [1, 2]
    assert all(s["as_of"] == "2026-11-24" and s["run_id"] == "r1" for s in steps)
    assert steps[1]["reused_by"] and steps[1]["goal"]["kind"] == "forecast"


def test_write_trace_appends_runs(tmp_path):
    write_trace(result("a"), tmp_path)
    write_trace(result("b"), tmp_path)
    assert [r["run_id"] for r in read_runs(tmp_path)] == ["a", "b"]
    steps = read_steps(tmp_path, "b")
    assert len(steps) == 2 and json.loads(steps[0]["arguments"])["as_of"] == "2026-11-24"
    lines = [json.loads(x) for x in (tmp_path / JSONL_NAME).read_text().splitlines()]
    assert [x["type"] for x in lines] == ["step", "step", "run"] * 2


def test_a_run_id_is_stored_once(tmp_path):
    write_trace(result("a"), tmp_path)
    before = (tmp_path / JSONL_NAME).read_text()
    with pytest.raises(sqlite3.IntegrityError):
        write_trace(result("a"), tmp_path)
    assert (tmp_path / JSONL_NAME).read_text() == before  # nothing appended


def test_writers_separately(tmp_path):
    r = result()
    assert write_jsonl(r, tmp_path).name == JSONL_NAME
    assert write_sqlite(r, tmp_path).name == SQLITE_NAME


def test_dropped_clauses_are_stored_and_an_old_table_gains_the_column(tmp_path):  # #232
    r = investigate("Forecast Microsoft for next year. Thanks, the 2 of us", AS_OF,
                    tools=StubTools(), parser=TrainedParser(KeywordClassifier({}, "forecast"),
                                                            0.8),
                    resources=R, run_id="d1")
    expected = [{"text": "thanks, the 2 of us", "numbers": ["2"]}]
    assert json.loads(json.dumps(run_record(r)["dropped_clauses"])) == expected
    assert "It contains a number (2) that was not used." in r.answer
    with sqlite3.connect(tmp_path / SQLITE_NAME) as db:  # a runs table written before #232
        db.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, question TEXT, as_of TEXT, "
                   "compiler TEXT, status TEXT, stop_reason TEXT, steps_used INTEGER, "
                   "reused INTEGER, max_steps INTEGER, goals TEXT, answer TEXT)")
    write_sqlite(r, tmp_path)
    (run,) = read_runs(tmp_path)
    assert json.loads(run["dropped_clauses"]) == expected
