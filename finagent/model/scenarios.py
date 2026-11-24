"""Run the scenario set (blueprint 8.11, F5; Phase 4c) and write the report.

    python -m finagent.model.scenarios [--as-of 2026-11-24] [--run-id forecast-0003]

Each company's validated statement set is built once (Phase 4a); the integrated forecast then
runs once per scenario of configs/scenarios.yaml, each through checks F1-F5. Writes
runs/{run_id}/scenarios.json, drivers.csv, projections.csv, checks.csv and
docs/scenarios_v1.md (validation in full, train as a summary). Held-out companies are refused
(9.2). Phase 4c exit: every BC16-counted validation company is VERIFIED in every scenario.
"""

import argparse
import csv
import hashlib
import json
import traceback
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from finagent.config import ForecastConfig, ScenarioSpec, load_forecast_config, \
    load_forecast_inputs, load_scenarios, load_universe
from finagent.model.forecast import VERIFIED, build_forecast
from finagent.model.report import BS_LINES, CF_LINES, IS_LINES, ROOT, _git, _m, _table, \
    disagree_inputs, write_report
from finagent.records import Forecast
from finagent.statements.build import StatementConfigs, build_statement_set

CHECK_IDS = ("F1", "F2", "F3", "F4", "F5")
HEADROOM_LINES = ("liquidity", "liquidity_headroom", "liquidity_shortfall")


@dataclass
class ScenarioRun:
    ticker: str
    split: str
    forecasts: dict[str, Forecast] = field(default_factory=dict)  # scenario name -> forecast
    disagree_inputs: list[str] = field(default_factory=list)
    error: str | None = None

    def status(self, scenario: str) -> str:
        if self.error:
            return "ERROR"
        f = self.forecasts.get(scenario)
        return f.status if f else "—"


def run_company(store, ticker: str, split: str, as_of: date, classifier,
                configs: StatementConfigs, universe, needs, config: ForecastConfig,
                scenarios: Sequence[ScenarioSpec]) -> ScenarioRun:
    run = ScenarioRun(ticker, split)
    try:
        statement_set = build_statement_set(store, ticker, as_of, classifier, universe=universe,
                                            configs=configs)
        run.disagree_inputs = disagree_inputs(statement_set, needs)
        for spec in scenarios:
            run.forecasts[spec.name] = build_forecast(statement_set, as_of, config, spec)
    except Exception as exc:  # reported, never hidden
        run.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
    return run


def exit_check(runs: Iterable[ScenarioRun], names: Sequence[str]) -> dict[str, list[str]]:
    """Phase 4c: BC16-counted validation companies VERIFIED in every scenario."""
    counted, passed, excluded = [], [], []
    for r in runs:
        if r.split != "validation":
            continue
        if r.disagree_inputs:
            excluded.append(r.ticker)
            continue
        counted.append(r.ticker)
        if all(r.status(n) == VERIFIED for n in names):
            passed.append(r.ticker)
    return {"counted": counted, "passed": passed, "excluded_disagree": excluded}


def max_residuals(runs: Iterable[ScenarioRun]) -> dict[str, tuple[float, str]]:
    """Largest |residual| per check over every company, scenario and year (with where)."""
    found: dict[str, tuple[float, str]] = {}
    for r in runs:
        for name, f in r.forecasts.items():
            for c in f.checks:
                if abs(c.residual) > found.get(c.check_id, (-1.0, ""))[0]:
                    found[c.check_id] = (abs(c.residual), f"{r.ticker} {name} FY{c.fiscal_year}")
    return found


# ---------------------------------------------------------------- outputs

def write_outputs(out: Path, runs: Sequence[ScenarioRun], header: dict[str, Any],
                  names: Sequence[str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    document = {"header": header, "exit_check": exit_check(runs, names), "companies": [
        {"ticker": r.ticker, "split": r.split, "disagree_inputs": r.disagree_inputs,
         "error": r.error,
         "scenarios": {n: json.loads(json.dumps(asdict(f), default=str))
                       for n, f in r.forecasts.items()}} for r in runs]}
    (out / "scenarios.json").write_text(json.dumps(document, indent=1), encoding="utf-8")
    rows = {"drivers.csv": (["ticker", "split", "scenario", "driver", "value", "raw_value",
                             "basis", "status", "flags", "detail"], []),
            "projections.csv": (["ticker", "split", "scenario", "fiscal_year", "line", "value"],
                                []),
            "checks.csv": (["ticker", "split", "scenario", "check", "fiscal_year", "status",
                            "residual", "tolerance", "detail"], [])}
    for r in runs:
        for name, f in r.forecasts.items():
            key = [r.ticker, r.split, name]
            rows["drivers.csv"][1].extend(
                key + [a.driver_id, a.value, a.raw_value, a.basis, a.status, ";".join(a.flags),
                       a.detail] for a in f.assumptions)
            rows["projections.csv"][1].extend(
                key + [y.fiscal_year, line, repr(value)]
                for y in f.years for line, value in y.lines.items())
            rows["checks.csv"][1].extend(
                key + [c.check_id, c.fiscal_year, c.status, repr(c.residual), c.tolerance,
                       c.detail] for c in f.checks)
    for filename, (head, body) in rows.items():
        with (out / filename).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(head)
            writer.writerows(body)


# ---------------------------------------------------------------- markdown

def summary_cells(f: Forecast | None) -> list[str]:
    """FY+3 revenue, FY+3 EBIT margin, headroom path, largest shortfall."""
    if f is None or not f.years:
        return ["—", "—", "—", "—"]
    last = f.years[-1].lines
    margin = f"{last['ebit'] / last['revenue']:.1%}" if last.get("revenue") else "—"
    headroom = " → ".join(_m(y.lines.get("liquidity_headroom")) for y in f.years)
    shortfalls = [y.lines["liquidity_shortfall"] for y in f.years
                  if "liquidity_shortfall" in y.lines]
    return [_m(last["revenue"]), margin, headroom, _m(max(shortfalls)) if shortfalls else "—"]


def _reason(f: Forecast | None, error: str | None) -> str:
    if error:
        return error.splitlines()[0]
    if f is None:
        return "—"
    return "; ".join(f.reasons) if f.reasons else (", ".join(
        x for x in f.flags if not x.startswith("LEASE_COST")) or "—")


def scenario_table(runs: Sequence[ScenarioRun], names: Sequence[str]) -> list[str]:
    rows = []
    for r in runs:
        for n in names:
            f = r.forecasts.get(n)
            rows.append([r.ticker, n, r.status(n)] + summary_cells(f) + [_reason(f, r.error)])
    return _table(["company", "scenario", "status", "FY+3 revenue", "FY+3 EBIT margin",
                   "liquidity headroom FY+1 → FY+3", "max shortfall", "reasons / flags"], rows)


def shocked_drivers(f: Forecast) -> list[list[str]]:
    return [[a.driver_id, "—" if a.raw_value is None else f"{a.raw_value:.4f}",
             f"{a.value:.4f}" if a.driver_id != "minimum_cash_floor" else _m(a.value),
             a.detail.split("; ")[-1]]
            for a in f.assumptions
            if any(x.startswith("SCENARIO:") for x in a.flags)
            or a.driver_id in ("securities_haircut", "minimum_cash_floor")]


def company_section(r: ScenarioRun, names: Sequence[str]) -> list[str]:
    lines = [f"### {r.ticker}", ""]
    if r.error:
        return lines + [f"Error: {r.error.splitlines()[0]}", ""]
    lines += [f"DISAGREE inputs: {', '.join(r.disagree_inputs) or 'none'}.", ""]
    for n in names:
        f = r.forecasts[n]
        lines += [f"**{n}**: {f.status}. {_reason(f, None)}", ""]
        if f.status == VERIFIED and n != "Base":
            lines += _table(["shocked driver", "base", "scenario", "shock"],
                            shocked_drivers(f)) + [""]
    verified = {n: r.forecasts[n] for n in names if r.forecasts[n].status == VERIFIED}
    if not verified:
        return lines
    years = [y.fiscal_year for y in next(iter(verified.values())).years]
    for title, line_names in (("Income statement", IS_LINES), ("Balance sheet", BS_LINES),
                              ("Cash flow", CF_LINES), ("Liquidity (F5)", HEADROOM_LINES)):
        header = ["line"] + [f"{n} FY{y}" for n in verified for y in years]
        rows = [[name] + [_m(y.lines.get(name)) for f in verified.values() for y in f.years]
                for name in dict.fromkeys(line_names)
                if any(name in y.lines for f in verified.values() for y in f.years)]
        lines += [f"{title} ($ millions):", ""] + _table(header, rows) + [""]
    return lines


def render_report(runs: Sequence[ScenarioRun], header: dict[str, Any],
                  scenarios: Sequence[ScenarioSpec]) -> str:
    names = [s.name for s in scenarios]
    validation = [r for r in runs if r.split == "validation"]
    train = [r for r in runs if r.split == "train"]
    exit_ = exit_check(runs, names)
    residuals = max_residuals(runs)
    shocks = [[s.name, f"{s.revenue_growth_shift:+.3f}", f"{s.cogs_share_shift:+.3f}",
               f"{s.dso_days:+g} / {s.dio_days:+g}", f"{s.dpo_days:+g}",
               f"{s.funded_debt_rate_shift:+.3f}", f"{s.capex_multiplier:g}",
               f"{s.securities_haircut:.0%}", f"{s.minimum_cash_floor:,.0f}"] for s in scenarios]
    lines = [
        "# Scenario set v1 (Phase 4c)", "",
        f"Generated by `python -m finagent.model.scenarios` (run `{header['run_id']}`, commit "
        f"`{header['git_commit']}`{' dirty' if header['git_dirty'] else ''}, evidence manifest "
        f"`{header['evidence_manifest_sha256'][:12]}`, model `{header['model_version']}`, as of "
        f"{header['as_of']}). Outputs: `runs/{header['run_id']}/scenarios.json`, `drivers.csv`, "
        "`projections.csv`, `checks.csv`. Blueprint 8.11, Appendix B F1-F5; "
        "`configs/scenarios.yaml`.", "",
        "Amounts in $ millions. '—' is a line the filer does not have (never 0). A scenario "
        "that is INVALID or GAP shows no projected numbers. The revenue shock shifts the growth "
        "rate in every year, so its effect compounds. Liquidity headroom = cash + short-term "
        "investments x (1 - haircut) - minimum cash floor; shortfall = max(0, -headroom). "
        "Negative cash is never funded by a plug or a revolver.", "",
        "## 1. Scenario definitions (8.11; one definition in the system)", "",
    ]
    lines += _table(["scenario", "growth shift", "COGS / cash cost share", "DSO / DIO days",
                     "DPO days", "funded debt rate", "capex x", "securities haircut",
                     "cash floor $"], shocks) + [""]
    lines += [
        "NO_COGS filers (BC17, #82): the COGS-share shock applies to the total cash operating "
        "cost share; DIO and DPO apply to inventory and payables on revenue. The rate shock "
        "applies to funded debt; finance leases keep their effective rate.", "",
        "## 2. Exit check (Phase 4c)", "",
        f"BC16-counted validation companies: {len(exit_['counted'])}; VERIFIED in all "
        f"{len(names)} scenarios: {len(exit_['passed'])} ({', '.join(exit_['passed']) or 'none'})"
        f". Excluded as documented DISAGREE GAPs: "
        f"{', '.join(exit_['excluded_disagree']) or 'none'}. Exit "
        f"**{'met' if exit_['counted'] and exit_['passed'] == exit_['counted'] else 'not met'}**"
        ".", "",
        "Largest check residuals over every company, scenario and year (dollars):", "",
    ]
    lines += _table(["check", "max |residual|", "where"],
                    [[c, f"{residuals[c][0]:.3g}" if c in residuals else "—",
                      residuals[c][1] if c in residuals else "—"] for c in CHECK_IDS]) + [""]
    lines += ["## 3. Validation companies x scenarios", ""] + \
        scenario_table(validation, names) + [""]
    lines += ["## 4. Validation detail", ""]
    for r in validation:
        lines += company_section(r, names)
    lines += ["## 5. Train companies (summary)", "",
              "Train document readings are in-sample for M1.", ""]
    lines += _table(["company"] + names + ["FY+3 revenue (Severe)", "max shortfall (Severe)",
                                           "reason (Severe)"],
                    [[r.ticker] + [r.status(n) for n in names]
                     + [summary_cells(r.forecasts.get(names[-1]))[0],
                        summary_cells(r.forecasts.get(names[-1]))[3],
                        _reason(r.forecasts.get(names[-1]), r.error)] for r in train]) + [""]
    counts = {n: sum(1 for r in train if r.status(n) == VERIFIED) for n in names}
    lines += ["Train VERIFIED per scenario: "
              + ", ".join(f"{n} {c} of {len(train)}" for n, c in counts.items()) + ".", ""]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    from finagent.evaluation.e1 import pinned_tickers
    from finagent.evidence import EvidenceStore
    from finagent.training.m1 import load_model

    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 11, 24))
    parser.add_argument("--run-id", default="forecast-0003")
    parser.add_argument("--dataset-version", default="v3")
    parser.add_argument("--splits", default="validation,train")
    parser.add_argument("--report", default=str(ROOT / "docs" / "scenarios_v1.md"))
    args = parser.parse_args(argv)

    store = EvidenceStore(ROOT / "evidence")
    universe = load_universe()
    classifier = load_model(ROOT / "models", args.dataset_version,
                            ROOT / "datasets" / f"rows_{args.dataset_version}.meta.json")
    configs = StatementConfigs.load()
    needs = load_forecast_inputs()
    config = load_forecast_config()
    scenarios = load_scenarios()
    runs = [run_company(store, ticker, split, args.as_of, classifier, configs, universe, needs,
                        config, scenarios)
            for split in args.splits.split(",")
            for ticker in pinned_tickers(store, universe, split, args.as_of)]  # no held_out
    header = {
        "run_id": args.run_id, "git_commit": _git("rev-parse", "--short", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "evidence_manifest_sha256": hashlib.sha256(
            (ROOT / "evidence" / "manifest.json").read_bytes()).hexdigest(),
        "model_version": classifier.version, "as_of": args.as_of.isoformat(),
        "scenarios": [asdict(s) for s in scenarios],
    }
    names = [s.name for s in scenarios]
    write_outputs(ROOT / "runs" / args.run_id, runs, header, names)
    write_report(Path(args.report), render_report(runs, header, scenarios))
    for r in runs:
        print(r.ticker, r.split, [r.status(n) for n in names],
              "; ".join(r.forecasts[names[-1]].reasons)[:160] if r.forecasts
              else (r.error or "")[:160])
    print(exit_check(runs, names))
    print({k: v for k, v in max_residuals(runs).items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
