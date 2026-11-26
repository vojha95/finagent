"""Phase 4c scenario report: exit check, residuals, outputs and markdown."""

import csv
import json

from model_support import AS_OF, make_set

from finagent.config import load_scenarios
from finagent.model.forecast import build_forecast
from finagent.model.scenarios import ScenarioRun, exit_check, max_residuals, render_report, \
    write_outputs

SCENARIOS = load_scenarios()
NAMES = [s.name for s in SCENARIOS]
HEADER = {"run_id": "f-3", "git_commit": "abc", "git_dirty": True,
          "evidence_manifest_sha256": "f" * 64, "model_version": "m", "as_of": "2026-11-24"}


def _run(ticker, split, statement_set, disagree=()):
    return ScenarioRun(ticker, split, {s.name: build_forecast(statement_set, AS_OF, scenario=s)
                                       for s in SCENARIOS}, list(disagree))


def test_scenario_run_builds_every_scenario_and_reports_errors():
    from finagent.config import load_forecast_config, load_forecast_inputs
    from finagent.model.scenarios import scenario_run

    config, needs = load_forecast_config(), load_forecast_inputs()
    run = scenario_run("SYNT", "validation", make_set(), None, AS_OF, needs, config, SCENARIOS)
    assert run.error is None and list(run.forecasts) == NAMES and run.agent_values == []
    assert all(run.status(n) == build_forecast(make_set(), AS_OF, config, s).status
               for n, s in zip(NAMES, SCENARIOS))
    broken = scenario_run("SYNT", "validation", None, None, AS_OF, needs, config, SCENARIOS)
    assert broken.error and broken.status(NAMES[0]) == "ERROR"  # reported, never hidden


def _runs():
    return [_run("SYNT", "validation", make_set()),
            _run("DISP", "validation", make_set(gaps={(2025, "ppe_net"): "DISAGREE"}),
                 ["ppe_net FY2025"]),
            _run("SHRT", "validation", make_set(gaps={(2025, "accounts_payable"): "DISAGREE"})),
            ScenarioRun("TRN", "train", error="ValueError: boom")]


def test_exit_needs_every_scenario_verified():
    result = exit_check(_runs(), NAMES)
    assert result == {"counted": ["SYNT", "SHRT"], "passed": ["SYNT"],
                      "excluded_disagree": ["DISP"]}


def test_outputs_and_report(tmp_path):
    runs = _runs()
    write_outputs(tmp_path, runs, HEADER, NAMES)
    document = json.loads((tmp_path / "scenarios.json").read_text())
    assert set(document["companies"][0]["scenarios"]) == set(NAMES)
    checks = list(csv.DictReader((tmp_path / "checks.csv").open()))
    assert {r["check"] for r in checks} == {"F1", "F2", "F3", "F4", "F5"}
    assert {r["scenario"] for r in checks} == set(NAMES)
    residuals = max_residuals(runs)
    assert set(residuals) == {"F1", "F2", "F3", "F4", "F5"}
    assert all(v[0] < 0.01 for v in residuals.values())
    text = render_report(runs, HEADER, SCENARIOS)
    assert "Exit **not met**" in text and "| SYNT | Severe | VERIFIED |" in text
    assert "| Severe | -0.120 | +0.035 | +12 / +12 | -8 | +0.040 | 1 | 50% | 0 |" in text
    assert "| TRN | ERROR | ERROR | ERROR |" in text
    assert "Liquidity (F5) ($ millions):" in text
