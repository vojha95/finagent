"""Tests for finagent/ui/data.py and finagent/ui/verify.py (Phase 9; BC33, BC36). The checks
on real pinned data cover the validation and train companies only: held-out companies are run
by the serving build and the app alone (D2b)."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from finagent.config import ConfigError, load_universe
from finagent.planner.gate import GateDecision
from finagent.records import AnswerObservation, Compilation, Goal, TraceStep
from finagent.ui import data as d
from finagent.ui import verify

ROOT = Path(__file__).resolve().parents[1]
UNIVERSE = {"AAA": "validation", "BBB": "validation", "CCC": "held_out", "DDD": "train"}
SERVED = ["validation", "train", "held_out"]


# ---------------------------------------------------------------- config

def test_load_ui_config_defaults():
    cfg = d.load_ui_config()
    assert list(cfg.splits) == SERVED  # D2b: every split is served, labelled
    assert cfg.splits["train"]["label"].startswith("In-sample: the document model was trained")
    assert cfg.splits["held_out"]["label"] == "Unseen: measured once in held-out E1"
    assert cfg.splits["validation"]["label"] == "Measured: validation"
    assert cfg.serving["run"] == "serving-0002"
    assert cfg.as_of_default <= cfg.snapshot
    assert len(cfg.examples) == 6
    assert {e.kind for e in cfg.examples} == {"fact", "forecast", "liquidity", "explain", "risk",
                                              "clarification"}
    assert len(cfg.cards) == len({r["card"] for r in cfg.results if r.get("card")})
    assert all(r.get("explain") for r in cfg.results)  # every metric explained in one line
    assert cfg.manual_build["hours"] == 20


def test_load_ui_config_rejects_unknown_split_and_bad_cards(tmp_path):
    raw = yaml.safe_load(d.UI_PATH.read_text())
    bad = {**raw, "splits": {**raw["splits"], "other": raw["splits"]["train"]}}
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(bad))
    with pytest.raises(ConfigError):
        d.load_ui_config(tmp_path / "a.yaml")
    (tmp_path / "b.yaml").write_text(yaml.safe_dump({**raw, "cards": ["Not a card"]}))
    with pytest.raises(ConfigError):
        d.load_ui_config(tmp_path / "b.yaml")


def test_load_ui_config_rejects_default_after_snapshot(tmp_path):
    raw = yaml.safe_load(d.UI_PATH.read_text())
    raw["as_of"]["default"] = date(2027, 1, 1)
    path = tmp_path / "ui.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ConfigError):
        d.load_ui_config(path)


def test_check_as_of():
    cfg = d.load_ui_config()
    d.check_as_of(cfg.snapshot, cfg)
    with pytest.raises(ValueError):
        d.check_as_of(date(cfg.snapshot.year + 1, 1, 1), cfg)


# ---------------------------------------------------------------- companies and refusal

def test_served_companies_and_names():
    assert d.served_companies(UNIVERSE, SERVED) == ["AAA", "BBB", "DDD", "CCC"]
    assert d.served_companies(UNIVERSE, ["validation"]) == ["AAA", "BBB"]
    names = d.company_names()
    assert all(isinstance(v, str) and v for v in names.values())


def test_split_label():
    cfg = d.load_ui_config()
    assert d.split_label("CCC", UNIVERSE, cfg)["short"] == cfg.splits["held_out"]["short"]
    assert d.split_label("ZZZ", UNIVERSE, cfg) == {"short": "", "label": "", "tip": ""}


def _compilation(issuers=(), held_out=(), goals=()):
    return Compilation("q", "test", goals=goals, issuers=issuers, held_out=held_out)


def test_refusal_rules():
    assert d.refusal(_compilation(("AAA",)), UNIVERSE, SERVED, "AAA") is None
    goal = Goal("BBB", "latest", "fact", "revenue")
    assert d.refusal(_compilation((), goals=(goal,)), UNIVERSE, SERVED, "AAA") is None
    # D2b: a held-out or train company is served
    assert d.refusal(_compilation(("CCC",), held_out=("CCC",)), UNIVERSE, SERVED, "AAA") is None
    assert d.refusal(_compilation(("AAA", "DDD")), UNIVERSE, SERVED, "AAA") is None
    outside = d.refusal(_compilation(("ZZZ",)), UNIVERSE, SERVED, "AAA")
    assert "ZZZ" in outside and "outside" in outside and "Nothing was run" in outside
    assert d.refusal(_compilation(()), UNIVERSE, SERVED, "ZZZ") is not None
    assert d.refusal(_compilation(("CCC",)), UNIVERSE, ["validation"], "AAA") is not None


# ---------------------------------------------------------------- pinned runs

def test_run_as_of(tmp_path):
    (tmp_path / "summary.json").write_text(json.dumps({"header": {"as_of": "2026-11-24"}}))
    assert d.run_as_of(tmp_path) == date(2026, 11, 24)
    assert d.run_as_of(tmp_path / "missing") is None


def test_serving_run_needs_every_file(tmp_path):
    cfg = SimpleNamespace(serving={"run": "serving-0009", "files": ["serving.json",
                                                                    "workbooks/summary.json"]})
    assert d.serving_run(cfg, tmp_path) is None
    run = tmp_path / "serving-0009"
    (run / "workbooks").mkdir(parents=True)
    (run / "serving.json").write_text("{}")
    assert d.serving_run(cfg, tmp_path) is None
    (run / "workbooks" / "summary.json").write_text("{}")
    assert d.serving_run(cfg, tmp_path) == run


# ---------------------------------------------------------------- statements

@pytest.mark.parametrize("value, fmt, shown", [
    (469822000000.0, '#,##0,,;(#,##0,,);"–"', "469,822"),
    (-567000000.0, '#,##0,,;(#,##0,,);"–"', "(567)"),
    (0.0, '#,##0,,;(#,##0,,);"–"', "–"),
    (0.0, "#,##0,,;(#,##0,,);0", "0"),
    (0.0311, "0.0%;(0.0%)", "3.1%"),
    (-0.077, "0.0%;(0.0%)", "(7.7%)"),
    (0.0304, "0.00%", "3.04%"),
    (1.5, '0.00"x";-0.00"x"', "1.50x"),
    (110.74, "0.0", "110.7"),
])
def test_format_number(value, fmt, shown):
    assert d.format_number(value, fmt) == shown


def _workbook(tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Income Statement"
    for col, head in enumerate(["$ millions", "FY2024A", "FY2025A", "FY2026E"], 1):
        ws.cell(5, col, head)
    dollars = '#,##0,,;(#,##0,,);"–"'
    ws.cell(6, 1, "Revenue")
    ws.cell(6, 2, 100e6).number_format = dollars
    ws.cell(6, 3, None)  # GAP
    ws.cell(6, 4, "=C6*1.1").number_format = dollars
    ws.cell(7, 1, "Growth")
    ws.cell(7, 3, '=IF(B6<=0,"n.m.",C6/B6-1)').number_format = "0.0%;(0.0%)"
    ws.cell(7, 4, "=D6/C6-1").number_format = "0.0%;(0.0%)"
    ws.cell(8, 1, "Other")
    ws.cell(8, 2, 0.0).number_format = dollars
    path = tmp_path / "X.xlsx"
    wb.save(path)
    manifest = {"verified_scenarios": ["Base", "Moderate"],
                "cells": {"Income Statement!D6": {"expected": {"Base": 121e6, "Moderate": 99e6,
                                                               "Severe": 80e6}},
                          "Income Statement!C7": {"expected": {"*": "n.m."}},
                          "Income Statement!D7": {"expected": {"Base": 0.1, "Moderate": -0.1,
                                                               "Severe": -0.2}}},
                "gaps": [{"cell": "Income Statement!C6", "reason": "FY2025: GAP (NEITHER)"}],
                "nils": [{"cell": "Income Statement!B8", "basis": "nil (#152)"}]}
    return path, manifest


def test_statement_tables(tmp_path):
    path, manifest = _workbook(tmp_path)
    (t,) = d.statement_tables(path, manifest, "Moderate", sheets=("Income Statement",))
    assert t.columns == ("Line", "FY2024A", "FY2025A", "FY2026E")
    assert t.rows[0] == ("Revenue", "100", "n/a", "99")
    assert t.tips[0] == ("", "", "FY2025: GAP (NEITHER)", "")
    assert t.rows[1] == ("Growth", "", "n.m.", "(10.0%)")
    assert t.rows[2] == ("Other", "nil", "", "")
    assert t.tips[2][1] == "nil (#152)"
    assert t.notes == ("FY2025A Revenue: FY2025: GAP (NEITHER)", "FY2024A Other: nil (#152)")


def test_statement_html(tmp_path):
    path, manifest = _workbook(tmp_path)
    (t,) = d.statement_tables(path, manifest, "Moderate", sheets=("Income Statement",))
    shown = d.statement_html(t)
    assert '<th class="num a">FY2024A</th>' in shown and '<th class="num e">FY2026E</th>' in shown
    assert '<td class="num a na" title="FY2025: GAP (NEITHER)">n/a</td>' in shown
    assert '<td class="num e">99</td>' in shown
    escaped = d.statement_html(d.StatementTable("S", ("Line", "FY2025A"), (("<b>", "1"),), ()))
    assert "&lt;b&gt;" in escaped and "<b>" not in escaped


def test_statement_tables_unverified_scenario_blank(tmp_path):
    path, manifest = _workbook(tmp_path)
    (t,) = d.statement_tables(path, manifest, "Severe", sheets=("Income Statement",))
    assert t.rows[0][3] == "" and t.rows[1][2] == "n.m."  # shared expected values still show


# ---------------------------------------------------------------- checks and credit

def _write_csv(path, header, rows):
    path.write_text("\n".join([",".join(header), *(",".join(map(str, r)) for r in rows)]) + "\n")


def test_check_table(tmp_path):
    path = tmp_path / "checks.csv"
    _write_csv(path, ["ticker", "split", "scenario", "check", "fiscal_year", "status", "residual",
                      "tolerance", "detail"],
               [["AAA", "validation", "Base", "F1", 2026, "PASS", 0, 0.01, "a - l - e"],
                ["AAA", "validation", "Base", "F2", 2026, "PASS", 0, 0.01, "cash"],
                ["AAA", "validation", "Base", "F1", 2027, "FAIL", 1, 0.01, "a - l - e"],
                ["AAA", "validation", "Severe", "F1", 2026, "PASS", 0, 0.01, "a - l - e"],
                ["BBB", "validation", "Base", "F1", 2026, "PASS", 0, 0.01, "a - l - e"]])
    head, rows = d.check_table(path, "AAA", "Base")
    assert head == ("Check", "What it tests", "FY2026", "FY2027")
    assert rows == [("F1", "a - l - e", "PASS", "FAIL"), ("F2", "cash", "PASS", "")]


def test_credit_table(tmp_path):
    _write_csv(tmp_path / "debt_service.csv",
               ["ticker", "scenario", "fiscal_year", "cash_available", "debt_service",
                "scoped_dscr", "status", "detail"],
               [["AAA", "Base", 2026, 2e9, 1e9, 2.0, "VERIFIED", "recomputed"],
                ["AAA", "Severe", 2026, 1e9, 1e9, 1.0, "VERIFIED", "x"]])
    _write_csv(tmp_path / "liquidity.csv",
               ["ticker", "scenario", "fiscal_year", "cash_headroom", "label",
                "headroom_with_reserve", "label_with_reserve", "status"],
               [["AAA", "Base", 2026, -5e8, "MODELLED CASH SHORTFALL", 1e8,
                 "NO MODELLED SHORTFALL", "VERIFIED"]])
    head, rows, notes = d.credit_table(tmp_path, "AAA", "Base")
    assert head == ("Measure", "FY2026")
    shown = dict((r[0], r[1]) for r in rows)
    assert shown["Scoped DSCR"] == "2.00x"
    assert shown["Headroom, cash basis"] == "USD -500.0m"
    assert shown["Result, cash + haircut securities basis"] == "NO MODELLED SHORTFALL"
    assert notes == ["FY2026: recomputed"]


def test_scenario_and_driver_rows(tmp_path):
    scenarios = {"companies": [{"ticker": "AAA", "scenarios": {
        "Base": {"status": "VERIFIED", "origin_year": 2025, "flags": ["CASH_SHORTFALL"],
                 "reasons": []},
        "Severe": {"status": "GAP", "origin_year": 2025, "flags": [], "reasons": ["x", "y"]}}}]}
    rows = d.scenario_rows(scenarios, "AAA")
    assert rows[0] == {"Scenario": "Base", "Status": "VERIFIED", "From the 10-K of": "FY2025",
                       "Flags": "CASH_SHORTFALL", "Reasons": ""}
    assert rows[1]["Reasons"] == "x; y" and d.scenario_rows(scenarios, "BBB") == []
    _write_csv(tmp_path / "drivers.csv", ["ticker", "split", "scenario", "driver", "value",
                                          "raw_value", "basis", "status", "flags", "detail"],
               [["AAA", "validation", "Base", "revenue_growth", 0.1612, "", "CAGR_4Y", "SET", "",
                 "cagr"],
                ["AAA", "validation", "Base", "debt_principal", 9.25e9, "", "LATEST", "SET", "",
                 "note"]])
    rows = d.driver_rows(tmp_path / "drivers.csv", "AAA", "Base")
    assert [(r["Driver"], r["Value"]) for r in rows] == [("Revenue growth", "16.1%"),
                                                        ("Debt principal", "USD 9.2bn")]
    assert d.driver_value("") == ""


def test_recalc_text(tmp_path):
    path = tmp_path / "recalc.json"
    assert d.recalc_text(path, "AAA") == (False, "This workbook was not recalculated.")
    first = ["Ratios!C8: -6003000000 != -6003400000.0 (tolerance 0.01)"]
    path.write_text(json.dumps({"workbooks": {
        "AAA": {"status": "PASS", "scenarios": {"Base": {"mismatches": 0, "first": []}}},
        "BBB": {"status": "FAIL", "scenarios": {s: {"mismatches": 1, "first": first}
                                               for s in d.SCENARIOS}}}}))
    ok, text = d.recalc_text(path, "AAA")
    assert ok and "every formula gives the model's value" in text
    ok, text = d.recalc_text(path, "BBB")
    assert not ok and text.count("Ratios!C8") == 1 and "1 cell(s) differ" in text


def test_company_cards():
    statements = {"companies": [{"issuer": "AAA", "filings": [{"fiscal_year": 2024},
                                                              {"fiscal_year": 2025}],
                                 "observations": [
        {"metric": "revenue", "fiscal_year": 2025, "value": "716924000000",
         "status": "VERIFIED", "filed": "2026-02-06", "flags": ["CORRECTED"]},
        {"metric": "revenue", "fiscal_year": 2024, "value": "1", "status": "VERIFIED"},
        {"metric": "net_income", "fiscal_year": 2025, "value": "5", "status": "DISAGREE",
         "reason": "VALUES_DIFFER"},
        {"metric": "operating_cash_flow", "fiscal_year": 2025, "value": "7000000",
         "status": "SINGLE_CHANNEL", "reason": "TAG_ONLY", "accepted": True,
         "filed": "2026-02-06"},
        {"metric": "cash", "fiscal_year": 2025, "value": "1", "status": "SINGLE_CHANNEL",
         "accepted": False}]}]}
    year, cards = d.company_cards(statements, "AAA", ["revenue", "net_income", "total_assets",
                                                      "operating_cash_flow", "cash"],
                                  {"revenue": "Revenue"})
    assert year == 2025
    assert cards[0] == d.MetricCard("Revenue", "USD 716.9bn", "VERIFIED",
                                    "FY2025, 10-K filed 6 Feb 2026; corrected")
    assert cards[1].value == "n/a" and cards[1].status == "DISAGREE"  # never a value
    assert cards[2].value == "n/a" and "not reported" in cards[2].help
    assert cards[3].value == "USD 7.0m" and "one source only" in cards[3].help
    assert cards[4].value == "n/a"  # not accepted
    assert d.company_cards(statements, "BBB", ["revenue"], {}) == (None, [])


def test_signal_states(tmp_path):
    _write_csv(tmp_path / "signals.csv", ["ticker", "fiscal_year", "signal", "state", "reason",
                                          "detail", "values"],
               [["AAA", "FY2025", "R1", "NOT_FIRED", "", "", ""],
                ["AAA", "FY2025", "R2", "FIRED", "", "", ""],
                ["AAA", "FY2024", "R1", "FIRED", "", "", ""],
                ["BBB", "FY2025", "R1", "GAP", "NO_YEAR", "x", ""]])
    states = d.signal_states(tmp_path / "signals.csv", "AAA", 2025)
    assert [(s["signal"], s["state"]) for s in states] == [("R1", "NOT_FIRED"), ("R2", "FIRED")]


# ---------------------------------------------------------------- insight cards

def _card(signal, status, evidence=(), low=None, scenario=""):
    return {"signal_id": signal, "name": "Test signal", "severity": "medium", "status": status,
            "scenario": scenario, "text": f"{signal} card text", "detail": "",
            "accession": "0000000000-26-000001", "filed": "2026-02-01",
            "filing_url": "https://example.invalid/10k.htm", "evidence": list(evidence),
            "low_confidence": low,
            "metrics": [{"label": "capex to revenue up", "state": "FIRED", "value": 5.37,
                         "unit": "pp", "op": ">", "threshold": 2.0,
                         "values": [[2024, 13.01], [2025, 18.39]]}]}


def test_insight_cards_latest_year(tmp_path):
    quote = {"chunk_id": "c1", "accession": "a1", "text": "Capex rose.", "section": "ITEM_7",
             "score": 0.5}
    low = {"quote": {**quote, "text": "Maybe related."}, "rerank_score": 3.65,
           "threshold": 5.263, "label": "Low confidence: may not explain the movement"}
    data = {"companies": [{"ticker": "AAA", "cards": {
        "2024": [_card("R1", "VERIFIED", [quote])],
        "2025": [_card("R3", "EVIDENCE_GAP", low=low), _card("R4", "VERIFIED", [quote])]}}]}
    path = tmp_path / "cards.json"
    path.write_text(json.dumps(data))
    year, cards = d.insight_cards(path, "AAA")
    assert year == 2025 and [c.title for c in cards] == ["R3 Test signal", "R4 Test signal"]
    gap, quoted = cards
    assert gap.low["label"].startswith("Low confidence") and not gap.quotes
    assert quoted.quotes[0]["text"] == "Capex rose." and quoted.low is None
    assert gap.metrics[0] == ("capex to revenue up", "FIRED", "+5.37 pp", "> 2 pp",
                              "FY2024 13.01, FY2025 18.39")
    assert "not evidence" in d.low_confidence_text(gap.low)
    assert "3.650, below the threshold 5.263" in d.low_confidence_text(gap.low)
    assert d.insight_cards(path, "BBB") == (None, [])


def test_metric_value_units():
    assert d.metric_value(36897979044.9675, "USD") == "USD 36.9bn"
    assert d.metric_value(569_000_000.0, "USD") == "USD 569.0m"
    assert d.metric_value(1.48, "x") == "1.48x"
    assert d.metric_value(10.2, "days") == "10.2 days"
    assert d.metric_value(None, "x") == ""


# ---------------------------------------------------------------- Ask page

def _obs(flags=(), validated=True, metric="revenue"):
    return AnswerObservation(
        issuer="AAA", cik="1", fiscal_year=2025, metric=metric, unit="USD",
        value=Decimal("1000000000"), classification="REPORTED_FACT", accession="acc-1",
        filing_url="https://example.invalid/a", filed=date(2026, 2, 1),
        fiscal_period_end=date(2025, 12, 31), basis="AGREE", validated=validated,
        channels=("document", "tag"), flags=tuple(flags), sources=("acc-0",))


def test_verification_label():
    assert d.verification_label(_obs(), True) == "VERIFIED"
    assert d.verification_label(_obs(("SINGLE_CHANNEL",)), True) == "SINGLE_CHANNEL"
    assert d.verification_label(_obs(("CORRECTED",)), True) == "CORRECTED"
    assert d.verification_label(_obs(), False) == "INVALID (gate)"
    assert d.verification_label(_obs(validated=False), None) == "NOT VALIDATED"


def _investigation():
    goal = Goal("AAA", 2025, "fact", "revenue")
    result = SimpleNamespace(goal=goal, requested=True, status="VERIFIED", reason="", detail="",
                             observations=(_obs(),),
                             decisions=(GateDecision("revenue", True, "", ""),))
    step = TraceStep(step=1, tool="reported_verified", arguments={"issuer": "AAA"},
                     eligible_tools=("reported_verified",), decision="call", status="VERIFIED",
                     reused_by=(), observations=1, latency_ms=12.5, goal="fact AAA",
                     reason="", as_of=date(2026, 11, 24), gate="accepted")
    return SimpleNamespace(goals=(result,), trace=(step,), answer="AAA FY2025 revenue: raw")


def test_ask_tables():
    inv = _investigation()
    (g,) = d.goal_rows(inv)
    assert g["Period"] == "FY2025" and g["Status"] == "VERIFIED" and g["Requested"] == "yes"
    (v,) = d.value_rows(inv)
    assert v["Value"] == "USD 1.0bn" and v["Verification"] == "VERIFIED"
    assert v["Gate"] == "accepted" and v["10-K"] == "acc-1"
    (gate,) = d.gate_rows(inv)
    assert gate["Accepted"] == "yes"
    (t,) = d.trace_rows(inv)
    assert t["Tool"] == "reported_verified" and t["Latency (ms)"] == 12.5
    sources = {r["Accession"]: r for r in d.source_rows(inv)}
    assert sources["acc-1"]["URL"] == "https://example.invalid/a"
    assert sources["acc-0"]["Cited by"] == "revenue FY2025"


def test_trace_export_keeps_the_raw_answer(monkeypatch):
    monkeypatch.setattr(d, "run_record", lambda inv: {"run": 1})
    monkeypatch.setattr(d, "step_records", lambda inv: [])
    exported = json.loads(d.trace_export(_investigation()))
    assert exported["answer"] == "AAA FY2025 revenue: raw" and exported["run"] == {"run": 1}
    assert exported["statement_sets"] == []
    noted = json.loads(d.trace_export(_investigation(), ["AAA: reused from serving-0009"]))
    assert noted["statement_sets"] == ["AAA: reused from serving-0009"]  # BC37 trace note


# ---------------------------------------------------------------- Results page

def test_resolve_paths():
    data = {"a": {"b": [{"variant": "x", "v": 1}, {"variant": "y", "v": 2}]}}
    assert d.resolve(data, ["a", "b", "variant=y", "v"]) == 2
    assert d.resolve(data, ["a", "b", 0, "v"]) == 1


@pytest.mark.parametrize("kind, args, shown", [
    ("rate_ci", (0.9052, None, None), None),
    ("diff_ci", (0.0288, [0.0, 0.0554], None), "+0.029 [+0.000, +0.055]"),
    ("kn", ({"k": 68, "n": 92, "rate": 0.7391, "ci95": [0.6411, 0.8180]},), "68/92 = 0.739 [0.641, 0.818]"),
    ("pair", ([0.7037, [0.51852, 0.85185], 0],), "0.704 [0.519, 0.852]"),
    ("before_after", (0.8641, None, 0.8774), "0.864 -> 0.877"),
    ("p50_p95", ({"p50": 0.2482, "p95": 16.3847},), "0.25 / 16.38"),
    ("number", (0,), "0"),
    ("number", (0.625,), "0.625"),
    ("text", ("bm25 stays",), "bm25 stays"),
])
def test_format_result(kind, args, shown):
    if kind == "rate_ci":
        assert d.format_result(kind, 0.9052, [0.8585, 0.9503]) == "0.905 [0.859, 0.950]"
        return
    assert d.format_result(kind, *args) == shown


def test_e6_text():
    row = {"scored": 28, "qualifying": 32, "companies_scored": 8, "companies_qualifying": 8,
           "mean_ape_difference": -0.0193, "ci95": [-0.0716, 0.0272], "beats_naive": False}
    assert d.format_result("e6", row) == ("APE difference -0.019 [-0.072, +0.027]; coverage "
                                          "28/32 origins, 8/8 companies; no accuracy claim")
    none = {**row, "scored": 0, "companies_scored": 0, "mean_ape_difference": None,
            "ci95": None}
    assert d.e6_text(none).startswith("no scored origin; coverage 0/32")
    assert d.e6_text({**row, "beats_naive": True}).endswith("beats naive (claim)")


def test_headline_rows_and_charts(tmp_path):
    (tmp_path / "eval").mkdir()
    rows = {"e1-0001": {"field_accuracy": 0.9, "ci95": [0.85, 0.95], "fields": 10},
            "e1-0002": {"field_accuracy": 0.8, "ci95": [0.7, 0.9], "fields": 10},
            "e5-0001": {"overall": {m: {"k": 1, "n": 2, "rate": 0.5, "ci95": [0.1, 0.9]}
                                    for m in ("goal_accuracy", "precision")}}}
    with (tmp_path / "eval" / "experiments.csv").open("w") as fh:
        fh.write("run_id,metrics\n")
        for run, m in rows.items():
            fh.write(f'{run},"{json.dumps(m).replace(chr(34), chr(34) * 2)}"\n')
    (tmp_path / "runs" / "e4-0001").mkdir(parents=True)
    (tmp_path / "runs" / "e4-0001" / "metrics.json").write_text(
        json.dumps({"unsupported_rate": [0.7, [0.5, 0.85]]}))
    cfg = d.UiConfig(
        splits={}, as_of_default=date(2026, 11, 24), snapshot=date(2026, 11, 24),
        serving={"run": "serving-0001"}, pinned_files={}, retrieval_models=(), examples=(),
        limitations=Path("x"), cards=("E4 card", "E1 card"),
        results=(
            {"experiment": "E1", "run": "e1-0001", "model": "M1", "measure": "acc",
             "source": "csv", "path": ["field_accuracy"], "ci": ["ci95"], "kind": "rate_ci",
             "card": "E1 card", "explain": "share read correctly"},
            {"experiment": "E4", "run": "e4-0001", "model": "v1", "measure": "unsupported",
             "source": "runs/e4-0001/metrics.json", "path": ["unsupported_rate"],
             "kind": "pair", "note": "headline", "card": "E4 card"},
            {"experiment": "E7", "run": "e7-0009", "model": "p", "measure": "lat",
             "source": "runs/e7-0009/metrics.json", "path": ["cold"], "kind": "p50_p95"},
            {"experiment": "E5", "run": "e5-0009", "model": "p", "measure": "acc",
             "source": "csv", "path": ["overall"], "kind": "kn"}),
        charts={"e1": [{"run": "e1-0001", "model": "M1", "split": "validation"},
                       {"run": "e1-0002", "model": "M0", "split": "held-out"}],
                "e5": {"runs": [{"run": "e5-0001", "compiler": "M0"}],
                       "measures": {"goal_accuracy": "Goal accuracy"}}})
    table = d.headline_rows(cfg, tmp_path)
    assert [r["Value"] for r in table] == ["0.900 [0.850, 0.950]", "0.700 [0.500, 0.850]",
                                           d.NOT_RUN, d.NOT_RUN]
    assert table[1]["Note"] == "headline"
    assert table[0]["What it means"] == "share read correctly"
    cards = d.headline_cards(cfg, tmp_path)
    assert [(c["Title"], c["Value"]) for c in cards] == [("E4 card", "0.700 [0.500, 0.850]"),
                                                         ("E1 card", "0.900 [0.850, 0.950]")]
    assert cards[1]["Explain"] == "share read correctly" and cards[1]["Run"] == "e1-0001"
    experiments = d.read_experiments(tmp_path / "eval" / "experiments.csv")
    e1 = d.e1_chart_rows(cfg, experiments)
    assert e1[1] == {"Model": "M0", "Split": "held-out", "Run": "e1-0002", "Field accuracy": 0.8,
                     "Low": 0.7, "High": 0.9, "Fields": 10}
    (e5,) = d.e5_chart_rows(cfg, experiments)
    assert e5["Count"] == "1/2" and e5["Low"] == 0.1


def test_e7_questions(tmp_path):
    assert d.e7_questions(tmp_path) == []
    (tmp_path / "runs" / "e7-0001").mkdir(parents=True)
    (tmp_path / "runs" / "e7-0001" / "questions.jsonl").write_text('{"id": "Q1"}\n')
    assert d.e7_questions(tmp_path) == [{"id": "Q1"}]


E7_RUNS = ({"run": "e7-0001", "label": "before", "doc": "docs/a.md"},
           {"run": "e7-0002", "label": "after", "doc": "docs/b.md"})


def test_manual_vs_app():
    builds = ({"run": "serving-0008", "label": "Full build from the filings", "secondary": False},
              {"run": "serving-0009", "label": "With cached embeddings", "secondary": True})
    cfg = SimpleNamespace(serving={"run": "serving-0009"}, e7=E7_RUNS, build_times=builds,
                          manual_build={"hours": 20, "source": "an estimate (D10)"})
    serving = {"companies": [{"seconds": {"a": 10.0, "b": 2.0}, "error": ""},
                             {"seconds": {"a": 30.0}, "error": ""},
                             {"seconds": {"a": 99.0}, "error": "boom"}]}
    assert d.build_seconds(serving) == [12.0, 30.0]  # a failed build is not timed
    cached = {"companies": [{"seconds": {"a": 5.0}, "error": ""}]}
    before = {"startup_seconds": 2.016, "cold": {"p50": 0.25, "p95": 16.38, "max": 35.53}}
    after = {"startup_seconds": 6.5, "cold": {"p50": 0.2, "p95": 0.9, "max": 1.2}}
    rows = d.manual_vs_app(cfg, {"serving-0008": serving, "serving-0009": cached},
                           {"e7-0001": before, "e7-0002": after})
    # #370: the full build is the headline beside D10; the cached build comes last only.
    assert [r["Time"] for r in rows] == ["21 s / 30 s", "about 20 hours", "2.02 s",
                                         "0.25 s / 16.38 s / 35.53 s", "6.50 s",
                                         "0.20 s / 0.90 s / 1.20 s", "5 s / 5 s"]
    assert rows[0]["What"] == "Full build from the filings"
    assert "serving-0008, 2 companies" in rows[0]["Who"] and "D10" in rows[1]["Who"]
    assert rows[3]["Who"] == "FinAgent (e7-0001: before)"
    assert rows[5]["Who"] == "FinAgent (e7-0002: after)"
    assert rows[-1]["What"] == "With cached embeddings" and "serving-0009" in rows[-1]["Who"]
    only_before = d.manual_vs_app(cfg, {}, {"e7-0001": before, "e7-0002": None})
    assert [r["Time"] for r in only_before] == ["about 20 hours", "2.02 s",
                                                "0.25 s / 16.38 s / 35.53 s"]
    assert [r["Time"] for r in d.manual_vs_app(cfg, {}, {})] == ["about 20 hours"]


def test_configured_build_times_put_the_full_build_first(tmp_path):
    cfg = d.load_ui_config()
    assert [b["secondary"] for b in cfg.build_times] == [False, True]
    assert cfg.build_times[0]["label"].startswith("Full build of one company from the filings")
    assert "cached passage embeddings" in cfg.build_times[1]["label"]
    (tmp_path / "runs" / "serving-0001").mkdir(parents=True)
    (tmp_path / "runs" / "serving-0001" / "serving.json").write_text('{"companies": []}')
    assert d.build_runs(cfg, tmp_path) == {"serving-0001": {"companies": []},
                                           "serving-0002": None}


def test_e7_metrics_and_latest(tmp_path):
    cfg = SimpleNamespace(e7=E7_RUNS)
    (tmp_path / "runs" / "e7-0001").mkdir(parents=True)
    (tmp_path / "runs" / "e7-0001" / "metrics.json").write_text('{"cold": {}}')
    found = d.e7_metrics(cfg, tmp_path)
    assert found == {"e7-0001": {"cold": {}}, "e7-0002": None}
    assert d.latest_e7(cfg, found) == "e7-0001"
    assert d.latest_e7(cfg, {**found, "e7-0002": {"cold": {}}}) == "e7-0002"
    assert d.latest_e7(cfg, {}) is None


def test_configured_e7_runs():
    cfg = d.load_ui_config()
    assert [r["run"] for r in cfg.e7][0] == "e7-0001"  # the before run stays shown
    assert all(r["label"] and r["doc"].startswith("docs/") for r in cfg.e7)


def test_configured_results_resolve_on_the_real_experiments():
    """Every configured headline row resolves (no KeyError) on eval/experiments.csv, E6
    included with its coverage and the pre-registered claim."""
    cfg = d.load_ui_config()
    table = d.headline_rows(cfg)
    assert len(table) == len(cfg.results)
    csv_rows = [r for r, c in zip(table, cfg.results) if c["source"] == "csv"]
    assert all(r["Value"] != d.NOT_RUN for r in csv_rows)
    e6 = [r for r in table if r["Run"] == "e6-0001"]
    assert e6 and all("coverage" in r["Value"] and r["Value"].endswith("no accuracy claim")
                      for r in e6)
    assert [c["Title"] for c in d.headline_cards(cfg)] == list(cfg.cards)


# ---------------------------------------------------------------- verify

def test_verify_problems(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"abc")
    cfg = SimpleNamespace(pinned_files={
        "a.bin": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        "b.bin": "00", "c.bin": "11"}, retrieval_models=())
    (tmp_path / "c.bin").write_bytes(b"x")
    assert verify.problems(cfg, tmp_path, pins={}) == ["b.bin: missing",
                                                      "c.bin: sha256 differs from configs/ui.yaml"]


# ---------------------------------------------------------------- real pinned data (local only)

needs_models = pytest.mark.skipif(not (ROOT / "models" / "intent_v3.joblib").exists(),
                                  reason="models are git-ignored; run where they are pinned")


def test_snapshot_not_before_any_filing():
    """The date picker's maximum hides no pinned 10-K of a validation or train company (#323;
    tests never open held-out companies, D2b)."""
    from finagent.evidence import EvidenceStore

    cfg = d.load_ui_config()
    store = EvidenceStore(ROOT / "evidence")
    for ticker in d.served_companies(load_universe(), ["validation", "train"]):
        filings = store.load_filings(ticker, cfg.snapshot)
        assert filings and max(f.filed for f in filings) <= cfg.snapshot


@needs_models
def test_examples_compile_to_their_kind_for_every_served_company():
    from finagent.planner.compile import (CompilerResources, compile_question,
                                          load_trained_parser)

    cfg = d.load_ui_config()
    resources = CompilerResources.load()
    parser = load_trained_parser(resources)
    for ticker in d.served_companies(load_universe(), ["validation"]):
        for ex in cfg.examples:
            c = compile_question(ex.question, parser, resources, ticker)
            if ex.kind == "clarification":
                assert c.clarification is not None, (ticker, ex.question)
            else:
                assert c.clarification is None and c.goals[0].kind == ex.kind, (ticker, ex)
                assert {g.issuer for g in c.goals} == {ticker}


@needs_models
def test_verify_main_on_the_pinned_files(capsys):
    assert verify.main() == 0
    assert capsys.readouterr().out.startswith("OK")
