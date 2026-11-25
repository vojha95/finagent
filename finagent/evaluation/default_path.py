"""Default-path check: the current code reproduces the pinned statement and forecast runs.

    python -m finagent.evaluation.default_path --out runs/<run>/default_path_check.txt \
        [--statements-run statements-0008] [--forecast-run forecast-0006] \
        [--splits validation,train]

For every company of the given splits (held-out companies are never run), the statement set is
rebuilt after the Correlation Agent and compared with runs/<statements-run>/statements.json
observation by observation, on every field of the record. Each scenario's forecast is rebuilt
with the default configuration and its projected values compared with
runs/<forecast-run>/projections.csv (exact float repr), and its checks with checks.csv (status
and residual exact; a changed detail text is counted separately).
"""

import argparse
import csv
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from finagent.records import Forecast
from finagent.statements.build import StatementSet
from finagent.statements.report import set_record

ROOT = Path(__file__).resolve().parents[2]
ProjectionKey = tuple[str, str, str, int, str]  # ticker, split, scenario, year, line
CheckKey = tuple[str, str, str, str, int]  # ticker, split, scenario, check, year


@dataclass
class PathCheck:
    """Differences of one rebuild against the pinned runs."""

    lines: list[str] = field(default_factory=list)
    observations: int = 0
    observation_differences: int = 0
    projected: int = 0
    projection_differences: list[str] = field(default_factory=list)
    not_produced: list[str] = field(default_factory=list)
    new_lines: list[str] = field(default_factory=list)
    checks: int = 0
    check_differences: list[str] = field(default_factory=list)
    detail_changes: dict[str, int] = field(default_factory=dict)

    @property
    def unchanged(self) -> bool:
        return not (self.observation_differences or self.projection_differences
                    or self.not_produced or self.check_differences)


# ---------------------------------------------------------------- statements

def load_statement_run(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """(issuer, split) -> the company record of a statements run."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {(c["issuer"], c["split"]): c for c in doc["companies"]}


def json_record(statement_set: StatementSet, split: str) -> dict[str, Any]:
    """The company record exactly as a statements run writes it."""
    return json.loads(json.dumps(set_record(statement_set, split), default=str))


def compare_observations(reference: Sequence[Mapping[str, Any]],
                         current: Sequence[Mapping[str, Any]]) -> tuple[list[str], list[str]]:
    """(key differences, field differences) of two observation lists keyed by year and metric."""
    a = {(o["fiscal_year"], o["metric"]): o for o in reference}
    b = {(o["fiscal_year"], o["metric"]): o for o in current}
    keys = [f"only {'pinned' if k in a else 'rebuilt'} FY{k[0]} {k[1]}"
            for k in sorted(set(a) ^ set(b))]
    fields = [f"FY{k[0]} {k[1]} {f}: {a[k].get(f)!r} -> {b[k].get(f)!r}"
              for k in sorted(set(a) & set(b))
              for f in sorted(set(a[k]) | set(b[k])) if a[k].get(f) != b[k].get(f)]
    return keys, fields


# ---------------------------------------------------------------- forecasts

def load_projections(path: Path) -> dict[ProjectionKey, str]:
    with path.open(encoding="utf-8") as handle:
        return {(r["ticker"], r["split"], r["scenario"], int(r["fiscal_year"]), r["line"]):
                r["value"] for r in csv.DictReader(handle)}


def load_checks(path: Path) -> dict[CheckKey, dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return {(r["ticker"], r["split"], r["scenario"], r["check"], int(r["fiscal_year"])): r
                for r in csv.DictReader(handle)}


def projection_values(ticker: str, split: str,
                      forecasts: Mapping[str, Forecast]) -> dict[ProjectionKey, str]:
    """The projected values as a forecast run writes them (repr of the float)."""
    return {(ticker, split, name, y.fiscal_year, line): repr(value)
            for name, f in forecasts.items() for y in f.years for line, value in y.lines.items()}


def check_values(ticker: str, split: str,
                 forecasts: Mapping[str, Forecast]) -> dict[CheckKey, dict[str, str]]:
    return {(ticker, split, name, c.check_id, c.fiscal_year):
            {"status": c.status, "residual": repr(c.residual), "detail": c.detail}
            for name, f in forecasts.items() for c in f.checks}


def compare_company(check: PathCheck, ticker: str, split: str,
                    pinned_set: Mapping[str, Any] | None, statement_set: StatementSet,
                    pinned_projections: Mapping[ProjectionKey, str],
                    pinned_checks: Mapping[CheckKey, Mapping[str, str]],
                    forecasts: Mapping[str, Forecast]) -> None:
    """Add one company's comparison to `check`."""
    current = json_record(statement_set, split)
    if pinned_set is None:
        check.lines.append(f"{ticker} not in the pinned statements run")
        check.observation_differences += 1
    else:
        keys, fields = compare_observations(pinned_set["observations"], current["observations"])
        check.observations += len(current["observations"])
        check.observation_differences += len(keys) + len(fields)
        check.lines.append(f"{ticker} {len(current['observations'])} observations; "
                           f"{len(keys)} key differences; {len(fields)} field differences "
                           f"{keys[:5]} {fields[:5]}")
    produced = projection_values(ticker, split, forecasts)
    mine = {k: v for k, v in pinned_projections.items() if k[:2] == (ticker, split)}
    check.projected += len(set(mine) & set(produced))
    check.projection_differences += [f"{k}: {mine[k]} -> {produced[k]}"
                                     for k in sorted(set(mine) & set(produced))
                                     if mine[k] != produced[k]]
    check.not_produced += [str(k) for k in sorted(set(mine) - set(produced))]
    check.new_lines += [str(k) for k in sorted(set(produced) - set(mine))]
    rebuilt = check_values(ticker, split, forecasts)
    pinned = {k: v for k, v in pinned_checks.items() if k[:2] == (ticker, split)}
    check.checks += len(set(pinned) & set(rebuilt))
    for k in sorted(set(pinned) | set(rebuilt)):
        a, b = pinned.get(k), rebuilt.get(k)
        if a is None or b is None or (a["status"], a["residual"]) != (b["status"], b["residual"]):
            check.check_differences.append(f"{k}: {a and (a['status'], a['residual'])} -> "
                                           f"{b and (b['status'], b['residual'])}")
        elif a["detail"] != b["detail"]:
            change = f"{k[3]}: {a['detail']!r} -> {b['detail']!r}"
            check.detail_changes[change] = check.detail_changes.get(change, 0) + 1


def summary(check: PathCheck, statements_run: str, forecast_run: str) -> list[str]:
    lines = list(check.lines)
    lines.append(f"STATEMENTS {check.observations} observations; "
                 f"{check.observation_differences} differences (vs {statements_run})")
    lines.append(f"FORECAST {check.projected} projected values compared; "
                 f"{len(check.projection_differences)} differences; {len(check.not_produced)} "
                 f"{forecast_run} values not produced {check.not_produced[:5]}; "
                 f"{len(check.new_lines)} new lines {check.new_lines[:5]}")
    lines.append(f"CHECKS {check.checks} checks compared; {len(check.check_differences)} status "
                 f"or residual differences {check.check_differences[:5]}")
    for change, n in sorted(check.detail_changes.items()):
        lines.append(f"CHECK DETAIL TEXT ({n} checks; status and residual unchanged) {change}")
    lines.append("UNCHANGED" if check.unchanged else "CHANGED")
    return lines


# ---------------------------------------------------------------- command line

def rebuild(tickers: Iterable[tuple[str, str]], as_of: date, statements_run: str,
            forecast_run: str) -> PathCheck:
    """Rebuild each (ticker, split) with the default configuration and compare."""
    from finagent.config import (load_agent_config, load_forecast_config, load_scenarios,
                                 load_universe)
    from finagent.evidence import EvidenceStore
    from finagent.model.forecast import build_forecast
    from finagent.training.m1 import load_model
    from finagent.statements.build import StatementConfigs
    from finagent.verify.reconcile import company_statements

    store = EvidenceStore(ROOT / "evidence")
    universe = load_universe()
    classifier = load_model(ROOT / "models", "v3", ROOT / "datasets" / "rows_v3.meta.json")
    configs, agent = StatementConfigs.load(), load_agent_config()
    config, scenarios = load_forecast_config(), load_scenarios()
    pinned_sets = load_statement_run(ROOT / "runs" / statements_run / "statements.json")
    projections = load_projections(ROOT / "runs" / forecast_run / "projections.csv")
    checks = load_checks(ROOT / "runs" / forecast_run / "checks.csv")
    check = PathCheck()
    for ticker, split in tickers:
        s, _ = company_statements(store, ticker, as_of, classifier, universe, configs, agent)
        forecasts = {spec.name: build_forecast(s, as_of, config, spec) for spec in scenarios}
        compare_company(check, ticker, split, pinned_sets.get((ticker, split)), s, projections,
                        checks, forecasts)
    return check


def main(argv: list[str] | None = None) -> int:
    from finagent.config import load_universe
    from finagent.evaluation.e1 import pinned_tickers
    from finagent.evidence import EvidenceStore

    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 11, 24))
    parser.add_argument("--statements-run", default="statements-0008")
    parser.add_argument("--forecast-run", default="forecast-0006")
    parser.add_argument("--splits", default="validation,train")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    store, universe = EvidenceStore(ROOT / "evidence"), load_universe()
    tickers = [(t, split) for split in args.splits.split(",")
               for t in pinned_tickers(store, universe, split, args.as_of)]  # refuses held_out
    check = rebuild(tickers, args.as_of, args.statements_run, args.forecast_run)
    text = "\n".join(summary(check, args.statements_run, args.forecast_run)) + "\n"
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0 if check.unchanged else 1


if __name__ == "__main__":
    raise SystemExit(main())
