"""Default-path check (finagent.evaluation.default_path) on a synthetic company."""

import csv
import json
from dataclasses import replace

from model_support import AS_OF, make_set

from finagent.config import load_forecast_config, load_scenarios
from finagent.evaluation.default_path import (PathCheck, check_values, compare_company,
                                              compare_observations, json_record, load_checks,
                                              load_projections, load_statement_run,
                                              projection_values, summary)
from finagent.model.forecast import build_forecast

SET = make_set()
FORECASTS = {s.name: build_forecast(SET, AS_OF, load_forecast_config(), s)
             for s in load_scenarios()}


def pinned():
    checks = {k: dict(v) for k, v in check_values("SYNT", "validation", FORECASTS).items()}
    return (json_record(SET, "validation"), projection_values("SYNT", "validation", FORECASTS),
            checks)


def test_identical_rebuild_is_unchanged():
    record, projections, checks = pinned()
    check = PathCheck()
    compare_company(check, "SYNT", "validation", record, SET, projections, checks, FORECASTS)
    assert check.unchanged and check.observations == len(record["observations"])
    assert check.projected == len(projections) and "UNCHANGED" in summary(check, "s", "f")[-1]


def test_differences_are_reported():
    record, projections, checks = pinned()
    key = next(iter(projections))
    projections[key] = "1.0"
    projections[("SYNT", "validation", "Base", 2099, "cash")] = "2.0"
    check_key = next(iter(checks))
    checks[check_key] = dict(checks[check_key], detail="old text")
    record = dict(record, observations=record["observations"][1:])
    check = PathCheck()
    compare_company(check, "SYNT", "validation", record, SET, projections, checks, FORECASTS)
    assert len(check.projection_differences) == 1 and len(check.not_produced) == 1
    assert check.observation_differences == 1 and not check.unchanged
    assert check.check_differences == [] and sum(check.detail_changes.values()) == 1


def test_compare_observations_fields_and_keys():
    a = [{"fiscal_year": 2025, "metric": "cash", "value": "1", "flags": []}]
    b = [{"fiscal_year": 2025, "metric": "cash", "value": "2", "flags": []},
         {"fiscal_year": 2025, "metric": "debt", "value": "3"}]
    keys, fields = compare_observations(a, b)
    assert keys == ["only rebuilt FY2025 debt"] and fields == ["FY2025 cash value: '1' -> '2'"]


def test_run_files_load(tmp_path):
    record, projections, checks = pinned()
    (tmp_path / "statements.json").write_text(json.dumps({"companies": [record]}))
    assert ("SYNT", "validation") in load_statement_run(tmp_path / "statements.json")
    with (tmp_path / "projections.csv").open("w", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(["ticker", "split", "scenario", "fiscal_year", "line", "value"])
        w.writerows(list(k) + [v] for k, v in projections.items())
    assert load_projections(tmp_path / "projections.csv") == projections
    with (tmp_path / "checks.csv").open("w", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(["ticker", "split", "scenario", "check", "fiscal_year", "status", "residual",
                    "tolerance", "detail"])
        w.writerows(list(k) + [v["status"], v["residual"], 0.01, v["detail"]]
                    for k, v in checks.items())
    loaded = load_checks(tmp_path / "checks.csv")
    assert {k: (v["status"], v["residual"]) for k, v in loaded.items()} == \
        {k: (v["status"], v["residual"]) for k, v in checks.items()}
    assert replace(PathCheck(), observations=1).observations == 1
