"""Tests for finagent/evaluation/e7.py (E7 light; Phase 9 cut-down, BC33)."""

from types import SimpleNamespace

import pytest

from finagent.evaluation import e7

UNIVERSE = {"AAA": "validation", "BBB": "validation", "CCC": "held_out"}


def _rec(qid, cell, author="canonical", issuers=("AAA",), trap="", selected=""):
    return {"id": qid, "cell": cell, "author": author, "question": f"question {qid}",
            "selected_issuer": selected, "trap_type": trap,
            "goals": [{"issuer": t} for t in issuers] or None}


GROUPS = {"C1": "fact", "C2": "forecast", "C3": "wrong_company"}


def test_select_questions_round_robin_and_rules():
    records = [
        _rec("Q003", "C1"), _rec("Q001", "C1"), _rec("Q002", "C1", author="vineet"),
        _rec("Q010", "C2"), _rec("Q011", "C2", issuers=("CCC",)),
        _rec("Q020", "C3", issuers=(), trap="outside_universe"),
        _rec("Q021", "C3", issuers=(), trap="no_company"),
        _rec("Q022", "C3", issuers=(), selected="CCC"),
    ]
    chosen = e7.select_questions(records, GROUPS, UNIVERSE, n=4)
    assert [q["id"] for q in chosen] == ["Q001", "Q010", "Q021", "Q003"]
    assert chosen[0]["group"] == "fact"


def test_select_questions_stops_when_exhausted():
    chosen = e7.select_questions([_rec("Q001", "C1")], GROUPS, UNIVERSE, n=20)
    assert [q["id"] for q in chosen] == ["Q001"]


def test_run_pass_records_failures_and_statuses():
    questions = [{**_rec("Q001", "C1"), "group": "fact"}, {**_rec("Q002", "C1"), "group": "fact"}]

    def ask(record):
        if record["id"] == "Q002":
            raise RuntimeError("boom")
        return SimpleNamespace(status="COMPLETE", stop_reason="goals_resolved", steps_used=2)

    lines = []
    found = e7.run_pass(questions, ask, "cold", echo=lines.append)
    assert [t.status for t in found] == ["COMPLETE", "FAILED"]
    assert found[1].error.startswith("RuntimeError: boom") and found[0].error == ""
    assert found[0].order == 1 and found[0].pass_ == "cold" and len(lines) == 2
    assert e7.timing_record(found[0])["pass"] == "cold"


def test_latency_percentiles():
    out = e7.latency([1.0, 2.0, 3.0, 4.0, 100.0])
    assert out["p50"] == 3.0 and out["p95"] == pytest.approx(80.8)
    assert out["n"] == 5 and out["max"] == 100.0 and out["total"] == 110.0


def _timing(qid, pass_, seconds, status="COMPLETE", error=""):
    return e7.Timing(qid, "fact", "q", "", pass_, 1, seconds, status, "", 1, error)


def test_metrics_summary():
    cold = [_timing("Q1", "cold", 10.0), _timing("Q2", "cold", 0.5, "CLARIFICATION")]
    warm = [_timing("Q1", "warm", 0.2), _timing("Q2", "warm", 0.1, "FAILED", "boom")]
    m = e7.metrics(2.0, cold, warm, {"Q1": "COMPLETE", "Q2": "GAP"})
    assert m["cold_start_seconds"] == 12.0 and m["startup_seconds"] == 2.0
    assert m["failures"]["k"] == 1 and m["failures"]["n"] == 4
    assert m["failures_by_pass"] == {"cold": 0, "warm": 1}
    assert m["same_status_cold_warm"]["k"] == 1
    assert m["same_status_as_reference"]["k"] == 1
    assert m["same_status_as_reference"]["reference"] == e7.REFERENCE_RUN
    assert m["statuses"] == {"COMPLETE": 1, "CLARIFICATION": 1}
    assert m["peak_rss_mb"] > 0


def test_render_report():
    cold = [_timing("Q1", "cold", 10.0)]
    warm = [_timing("Q1", "warm", 0.2)]
    m = {**e7.metrics(2.0, cold, warm, {}), "groups": {"fact": 1}}
    header = {"run_id": "e7-0009", "git_commit": "abc", "git_dirty": False,
              "evidence_manifest_sha256": "89ee" * 16, "as_of": "2026-11-24",
              "model_version": "m1", "intent_model": "v3", "gold_sha256": "f" * 64,
              "environment": e7.environment()}
    text = e7.render_report(header, m, [e7.timing_record(t) for t in (*cold, *warm)])
    assert "# E7 light" in text and "| p50 latency (s) | 10.00 | 0.20 |" in text
    assert "| 1 | Q1 | fact | 10.00 | 0.20 | COMPLETE | q |" in text
