"""Run the integrated forecast for train and validation companies and write the report (8.10).

    python -m finagent.model.report [--as-of 2026-11-24] [--run-id forecast-0001]

Builds each company's validated statement set as of the date (Phase 4a), then the forecast.
Writes runs/{run_id}/forecast.json, drivers.csv, projections.csv and checks.csv, and
docs/forecast_v1.md (validation in full, train as a summary; train document readings are
in-sample for M1). Held-out companies are refused (9.2). Phase 4 exit (BC16): every validation
company with no DISAGREE on a forecast input has a VERIFIED forecast.
"""

import argparse
import csv
import hashlib
import json
import subprocess
import traceback
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from finagent.config import ForecastConfig, load_forecast_config, load_forecast_inputs, \
    load_universe
from finagent.model.forecast import VERIFIED, build_forecast
from finagent.records import Forecast
from finagent.statements.build import StatementConfigs, StatementSet, build_statement_set
from finagent.statements.coverage import DISAGREE, slots

ROOT = Path(__file__).resolve().parents[2]
NOTES_MARKER = "<!-- notes: text below this line is kept when the report is regenerated -->"
IS_LINES = ("revenue", "cogs", "other_cash_costs", "depreciation", "operating_lease_cost", "ebit",
            "debt_interest", "finance_lease_interest", "interest", "pretax", "tax", "net_income")
BS_LINES = ("cash", "receivables", "inventory", "ppe", "operating_rou", "other_assets",
            "total_assets", "payables", "debt", "operating_lease", "finance_lease",
            "other_liabilities", "total_liabilities", "retained_earnings", "other_equity",
            "total_equity")
CF_LINES = ("net_income", "depreciation", "working_capital_cf", "cfo", "capex", "cfi",
            "debt_principal", "new_debt", "finance_lease_principal", "dividends", "repurchases",
            "cff")


@dataclass
class CompanyRun:
    ticker: str
    split: str
    forecast: Forecast | None = None
    disagree_inputs: list[str] = field(default_factory=list)
    error: str | None = None


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT).stdout.strip()


def disagree_inputs(statement_set: StatementSet, needs) -> list[str]:
    """Forecast inputs (forecast_inputs.yaml) read as DISAGREE: the BC16 exit exclusion."""
    return [f"{s.need_id} FY{s.fiscal_year}" for s in slots(statement_set, needs)
            if s.outcome == DISAGREE]


def run_company(store, ticker: str, split: str, as_of: date, classifier,
                configs: StatementConfigs, universe, needs,
                config: ForecastConfig) -> CompanyRun:
    run = CompanyRun(ticker, split)
    try:
        statement_set = build_statement_set(store, ticker, as_of, classifier, universe=universe,
                                            configs=configs)
        run.disagree_inputs = disagree_inputs(statement_set, needs)
        run.forecast = build_forecast(statement_set, as_of, config)
    except Exception as exc:  # reported, never hidden
        run.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
    return run


def exit_check(runs: Iterable[CompanyRun]) -> dict[str, list[str]]:
    """BC16: validation companies counted (no DISAGREE input), those passing, and the rest."""
    counted, passed, excluded = [], [], []
    for r in runs:
        if r.split != "validation":
            continue
        if r.disagree_inputs:
            excluded.append(r.ticker)
            continue
        counted.append(r.ticker)
        if r.forecast is not None and r.forecast.status == VERIFIED:
            passed.append(r.ticker)
    return {"counted": counted, "passed": passed, "excluded_disagree": excluded}


# ---------------------------------------------------------------- outputs

def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def write_outputs(out: Path, runs: Sequence[CompanyRun], header: dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    document = {"header": header, "exit_check": exit_check(runs), "companies": [
        {"ticker": r.ticker, "split": r.split, "disagree_inputs": r.disagree_inputs,
         "error": r.error, "forecast": _jsonable(asdict(r.forecast)) if r.forecast else None}
        for r in runs]}
    (out / "forecast.json").write_text(json.dumps(document, indent=1), encoding="utf-8")
    with (out / "drivers.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ticker", "split", "driver", "value", "raw_value", "basis", "status",
                         "flags", "inputs", "detail"])
        for r in runs:
            for a in r.forecast.assumptions if r.forecast else ():
                writer.writerow([r.ticker, r.split, a.driver_id, a.value, a.raw_value, a.basis,
                                 a.status, ";".join(a.flags),
                                 ";".join(f"{i.metric}@FY{i.fiscal_year}" for i in a.inputs),
                                 a.detail])
    with (out / "projections.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ticker", "split", "fiscal_year", "line", "value"])
        for r in runs:
            for y in r.forecast.years if r.forecast else ():
                for line, value in y.lines.items():
                    writer.writerow([r.ticker, r.split, y.fiscal_year, line, repr(value)])
    with (out / "checks.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ticker", "split", "check", "fiscal_year", "status", "residual",
                         "tolerance", "detail"])
        for r in runs:
            for c in r.forecast.checks if r.forecast else ():
                writer.writerow([r.ticker, r.split, c.check_id, c.fiscal_year, c.status,
                                 repr(c.residual), c.tolerance, c.detail])


# ---------------------------------------------------------------- markdown

def _table(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return lines


def _m(value: float | None) -> str:
    """Millions with one decimal; '—' for an absent line (never 0)."""
    return "—" if value is None else f"{value / 1e6:,.1f}"


def _driver_value(a) -> str:
    if a.value is None:
        return "—"
    if a.driver_id in ("repurchases",) or a.basis == "SCHEDULE":
        return _m(a.value)
    return f"{a.value:.4f}"


def _status_text(r: CompanyRun) -> str:
    if r.error:
        return "ERROR"
    return r.forecast.status if r.forecast else "—"


def company_section(r: CompanyRun) -> list[str]:
    f = r.forecast
    lines = [f"### {r.ticker}", ""]
    if f is None:
        return lines + [f"Error: {r.error.splitlines()[0] if r.error else 'none'}", ""]
    lines += [f"Status **{f.status}**, origin FY{f.origin_year}, as of {f.as_of}. "
              f"DISAGREE inputs: {', '.join(r.disagree_inputs) or 'none'}. "
              f"Flags: {', '.join(f.flags) or 'none'}.", ""]
    if f.reasons:
        lines += ["Reasons:", ""] + [f"- {reason}" for reason in f.reasons] + [""]
    if f.assumptions:
        lines += ["Drivers (MODEL_ASSUMPTION):", ""]
        lines += _table(["driver", "value", "raw", "basis", "status", "flags", "detail"],
                        [[a.driver_id, _driver_value(a),
                          "—" if a.raw_value is None else f"{a.raw_value:.4f}", a.basis,
                          a.status, ", ".join(a.flags) or "—", a.detail]
                         for a in f.assumptions]) + [""]
    if f.opening:
        lines += ["Opening balance sheet (FY{}; $ millions):".format(f.origin_year), ""]
        lines += _table(["line", "value", "classification", "detail"],
                        [[o.line, _m(o.value), o.classification, o.detail or "—"]
                         for o in f.opening]) + [""]
    if f.years:
        years = [y.fiscal_year for y in f.years]
        for title, names in (("Income statement", IS_LINES), ("Balance sheet", BS_LINES),
                             ("Cash flow", CF_LINES)):
            rows = [[n] + [_m(y.lines.get(n)) for y in f.years] for n in names
                    if any(n in y.lines for y in f.years)]
            lines += [f"{title} ($ millions):", ""]
            lines += _table(["line"] + [f"FY{y}" for y in years], rows) + [""]
    if f.checks:
        lines += ["Checks F1-F4 (residual in dollars):", ""]
        lines += _table(["check", "FY", "status", "residual", "tolerance", "detail"],
                        [[c.check_id, c.fiscal_year, c.status, f"{c.residual:.6g}",
                          c.tolerance, c.detail] for c in f.checks]) + [""]
    return lines


def render_report(runs: Sequence[CompanyRun], header: dict[str, Any]) -> str:
    validation = [r for r in runs if r.split == "validation"]
    train = [r for r in runs if r.split == "train"]
    exit_ = exit_check(runs)
    lines = [
        "# Integrated forecast v1 (Phase 4b)", "",
        f"Generated by `python -m finagent.model.report` (run `{header['run_id']}`, commit "
        f"`{header['git_commit']}`{' dirty' if header['git_dirty'] else ''}, evidence manifest "
        f"`{header['evidence_manifest_sha256'][:12]}`, model `{header['model_version']}`, as of "
        f"{header['as_of']}). Outputs: `runs/{header['run_id']}/forecast.json`, `drivers.csv`, "
        "`projections.csv`, `checks.csv`. Blueprint 8.10, Appendix B (F1-F4); "
        "`configs/forecast.yaml`; switches "
        f"{header['switches']}.", "",
        "Amounts in $ millions. '—' is a line the filer does not have or a driver that does not "
        "apply (never 0). A forecast that is INVALID or GAP shows no projected numbers.", "",
        "## 1. Exit check (BC16)", "",
        f"Validation companies counted (no DISAGREE on a forecast input): "
        f"{len(exit_['counted'])}; VERIFIED: {len(exit_['passed'])} "
        f"({', '.join(exit_['passed']) or 'none'}). Excluded as documented DISAGREE GAPs: "
        f"{', '.join(exit_['excluded_disagree']) or 'none'}.", "",
    ]
    lines += _table(["company", "status", "origin", "DISAGREE inputs", "reasons / flags"],
                    [[r.ticker, _status_text(r), r.forecast.origin_year if r.forecast else "—",
                      ", ".join(r.disagree_inputs) or "—",
                      "; ".join(r.forecast.reasons) if r.forecast and r.forecast.reasons
                      else (", ".join(r.forecast.flags) if r.forecast else (r.error or "")
                            .splitlines()[0])]
                     for r in validation]) + [""]
    lines += ["## 2. Validation companies", ""]
    for r in validation:
        lines += company_section(r)
    lines += ["## 3. Train companies (summary)", "",
              "Train document readings are in-sample for M1.", ""]
    lines += _table(["company", "status", "origin", "DISAGREE inputs", "reasons / flags"],
                    [[r.ticker, _status_text(r), r.forecast.origin_year if r.forecast else "—",
                      ", ".join(r.disagree_inputs) or "—",
                      "; ".join(r.forecast.reasons) if r.forecast and r.forecast.reasons
                      else (", ".join(r.forecast.flags) if r.forecast else (r.error or "")
                            .splitlines()[0])]
                     for r in train]) + [""]
    counts: dict[str, int] = {}
    for r in train:
        counts[_status_text(r)] = counts.get(_status_text(r), 0) + 1
    lines += ["Train status counts: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
              + ".", ""]
    return "\n".join(lines) + "\n"


def write_report(path: Path, text: str) -> None:
    """Write the generated report, keeping any notes after NOTES_MARKER in the old file."""
    notes = ""
    if path.exists():
        old = path.read_text(encoding="utf-8")
        if NOTES_MARKER in old:
            notes = old.split(NOTES_MARKER, 1)[1]
    path.write_text(text + "\n" + NOTES_MARKER + notes, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    from finagent.evaluation.e1 import pinned_tickers
    from finagent.evidence import EvidenceStore
    from finagent.training.m1 import load_model

    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 11, 24))
    parser.add_argument("--run-id", default="forecast-0001")
    parser.add_argument("--dataset-version", default="v3")
    parser.add_argument("--splits", default="validation,train")
    parser.add_argument("--report", default=str(ROOT / "docs" / "forecast_v1.md"))
    args = parser.parse_args(argv)

    store = EvidenceStore(ROOT / "evidence")
    universe = load_universe()
    classifier = load_model(ROOT / "models", args.dataset_version,
                            ROOT / "datasets" / f"rows_{args.dataset_version}.meta.json")
    configs = StatementConfigs.load()
    needs = load_forecast_inputs()
    config = load_forecast_config()
    runs: list[CompanyRun] = []
    for split in args.splits.split(","):
        for ticker in pinned_tickers(store, universe, split, args.as_of):  # refuses held_out
            runs.append(run_company(store, ticker, split, args.as_of, classifier, configs,
                                    universe, needs, config))
    header = {
        "run_id": args.run_id, "git_commit": _git("rev-parse", "--short", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "evidence_manifest_sha256": hashlib.sha256(
            (ROOT / "evidence" / "manifest.json").read_bytes()).hexdigest(),
        "model_version": classifier.version, "as_of": args.as_of.isoformat(),
        "switches": {"working_capital_basis": config.working_capital_basis,
                     "refinance_maturities": config.refinance_maturities},
    }
    write_outputs(ROOT / "runs" / args.run_id, runs, header)
    write_report(Path(args.report), render_report(runs, header))
    for r in runs:
        print(r.ticker, r.split, _status_text(r),
              "; ".join(r.forecast.reasons)[:200] if r.forecast else (r.error or "")[:200])
    print(exit_check(runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
