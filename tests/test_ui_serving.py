"""Tests for finagent/ui/serving.py (BC36, D2b): the serving build's helpers and the
validation comparison against the pinned runs, on synthetic data (no company is built)."""

import json
from datetime import date
from types import SimpleNamespace

import pytest

from finagent.ui import serving as s


def test_differences_by_path():
    a = {"x": 1, "y": [1, {"z": "a"}], "w": [1, 2]}
    b = {"x": 1, "y": [1, {"z": "b"}], "w": [1], "v": 3}
    assert s.differences(a, b) == [(".v", "<absent>", 3), (".w", [1, 2], [1]),
                                   (".y[1].z", "a", "b")]
    assert s.differences(a, a) == []


@pytest.mark.parametrize("path, old, new, expected", [
    (".basis", "nil: no channel read it in FY2024", "not read in FY2024; treated as nil;",
     "wording"),
    (".agent.trace[0].latency_ms", 12.5, 13.1, "timing"),
    (".value", "100", "101", "unexplained"),
    (".basis", "nil: no channel read it in FY2024", "something else", "unexplained"),
])
def test_kind(path, old, new, expected):
    assert s.kind((path, old, new)) == expected


def test_known_wording_needs_both_texts():
    assert s.known_wording("treated as nil for FY2025", "treated as nil; x")
    assert not s.known_wording("treated as nil for FY2025", 5)
    assert not s.known_wording("other", "treated as nil;")


def test_scenario_status_per_split():
    run = lambda split, statuses: SimpleNamespace(split=split,  # noqa: E731
                                                  status=lambda n: statuses[n])
    found = s.scenario_status([run("validation", {"Base": "VERIFIED", "Severe": "GAP"}),
                               run("train", {"Base": "VERIFIED", "Severe": "VERIFIED"}),
                               None,
                               run("validation", {"Base": "VERIFIED", "Severe": "VERIFIED"})],
                              ["Base", "Severe"])
    assert found == {"Base": {"validation": {"VERIFIED": 2}, "train": {"VERIFIED": 1}},
                     "Severe": {"validation": {"GAP": 1, "VERIFIED": 1},
                                "train": {"VERIFIED": 1}}}


def test_timed_records_seconds_and_the_first_error():
    c = s.Company("AAA", "validation")
    assert s.timed(c, "a", lambda: 5) == 5 and c.error == ""

    def boom():
        raise ValueError("bad input")

    assert s.timed(c, "b", boom) is None
    assert c.error.startswith("b: ValueError: bad input")
    s.timed(c, "c", boom)
    assert c.error.startswith("b: ")  # the first error is kept
    assert set(c.seconds) == {"a", "b", "c"}


def test_write_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "_git", lambda *args: "abc1234")
    monkeypatch.setattr(s, "ROOT", tmp_path)
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence" / "manifest.json").write_text("{}")
    companies = [s.Company("AAA", "validation", {"statements": 1.0}),
                 s.Company("HHH", "held_out", {"statements": 2.0}, "statements: boom")]
    forecast = SimpleNamespace(split="validation", status=lambda n: "VERIFIED")
    s.write_summary(tmp_path, "serving-0009", date(2026, 11, 24), companies, [forecast],
                    {"AAA": {"status": "VERIFIED"}, "HHH": {"error": "boom"}},
                    {"workbooks": {"AAA": {"status": "PASS"}}},
                    [SimpleNamespace(name="Base")])
    doc = json.loads((tmp_path / "serving.json").read_text())
    assert doc["header"]["as_of"] == "2026-11-24" and "BC36" in doc["header"]["policy"]
    assert doc["built_without_error"] == 1 and doc["errors"] == ["HHH"]
    assert doc["scenario_status"] == {"Base": {"validation": {"VERIFIED": 1}}}
    assert doc["workbook_status"] == {"AAA": "VERIFIED", "HHH": "ERROR"}
    assert doc["recalc"] == {"AAA": "PASS"}


def test_served_orders_by_split_and_includes_held_out(monkeypatch):
    seen = []

    def pinned(store, universe, split, as_of, include_held_out=False):
        seen.append((split, include_held_out))
        return sorted(t for t, x in universe.items() if x == split)

    import finagent.evaluation.e1 as e1

    monkeypatch.setattr(e1, "pinned_tickers", pinned)
    universe = {"B": "train", "A": "validation", "H": "held_out"}
    assert s.served(None, universe, date(2026, 11, 24)) == [("A", "validation"),
                                                           ("B", "train"), ("H", "held_out")]
    assert all(flag for _, flag in seen)


def test_compare_counts_only_unexplained(monkeypatch, tmp_path):
    pairs = {"AAA": [("statements.json", {"v": 1, "basis": "treated as nil for FY2024"},
                      {"v": 1, "basis": "not read in FY2024; treated as nil;"}),
                     ("agent.json", {"trace": [{"latency_ms": 1.0}]},
                      {"trace": [{"latency_ms": 2.0}]})],
             "BBB": [("cards.json", {"x": 1}, {"x": 1})]}
    monkeypatch.setattr(s, "artifact_pairs", lambda serving, ticker: pairs[ticker])
    lines, unexplained = s.compare("serving-0009", {"AAA": "validation", "BBB": "validation",
                                                    "HHH": "held_out"})
    assert unexplained == 0 and lines[-1] == "MATCH"
    assert any("1 known #196 wording" in line for line in lines)
    assert any("1 timing" in line for line in lines)
    pairs["BBB"] = [("cards.json", {"x": 1}, {"x": 2}), ("recalc", {"s": 1}, None)]
    lines, unexplained = s.compare("serving-0009", {"AAA": "validation", "BBB": "validation"})
    assert unexplained == 2 and lines[-1] == "DIFFERENT"
    assert any("UNEXPLAINED .x" in line for line in lines)
    assert any("BBB recalc: missing in serving-0009" in line for line in lines)


def test_build_refuses_an_existing_run(monkeypatch, tmp_path):
    monkeypatch.setattr(s, "RUNS", tmp_path)
    (tmp_path / "serving-0009").mkdir()
    with pytest.raises(SystemExit):
        s.build("serving-0009", date(2026, 11, 24))
