"""Build the Excel workbook for one company (blueprint 8.9; Phase 4d; BC21).

    python -m finagent.workbook.build [--as-of 2026-11-24] [--run-id workbooks-0003] [--no-agent]
        [--splits validation] [--tickers MSFT,CVX]

Sheets: Cover; Income Statement; Balance Sheet; Cash Flow; Ratios; Forecast; Verification;
Sources. Each statement sheet is one continuous table (configs/workbook.yaml): every line item
is one row across up to five historical (A) and three projected (E) years. History holds the
reported values (blue REPORTED_FACT inputs, CALCULATION formulas, GAP cells empty with a short
note); the projected cells are the integrated model's formulas, driven by the Forecast sheet and
its scenario selector, starting from the FY0 cells of the same rows. A company whose Base
forecast is GAP or INVALID gets history, ratios, verification and sources, with empty projected
columns and the reason shown.

Each workbook is checked by evaluating every formula independently (finagent.workbook.evaluate)
under each scenario against the Python model; LibreOffice recalculation is a separate step
(finagent.workbook.recalc). Writes runs/{run_id}/<TICKER>.xlsx and manifest.json (expected
values per formula cell and scenario). Held-out companies are refused (9.2).
"""

import argparse
import hashlib
import json
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from finagent.config import (ForecastConfig, ScenarioSpec, load_concept_map,
                             load_forecast_config, load_model_inputs, load_scenarios)
from finagent.model.forecast import VERIFIED
from finagent.model.forecast import no_cogs as no_cogs_line
from finagent.model.history import lookup
from finagent.records import Forecast
from finagent.statements.build import StatementSet
from finagent.verify.reconcile import Reconciliation
from finagent.workbook import info
from finagent.workbook.evaluate import Evaluator, FormulaError
from finagent.workbook.grid import ALL, Columns, Grid, divider, title, widths, year_header
from finagent.workbook.layout import WorkbookLayout, load_workbook_layout
from finagent.workbook.model import (BS, CF, FORECAST, IS, ModelContext, ModelWriter, banner,
                                     statement_keys)
from finagent.workbook.ratios_sheet import RATIOS_SHEET, RatiosWriter
from finagent.workbook.statements import FIRST_ROW, StatementSheets

SHEETS = ("Cover", IS, BS, CF, RATIOS_SHEET, FORECAST, "Verification", "Sources")
TOLERANCES = {"dollar": 0.01, "ratio": 1e-9, "exact": 0.0}
ROOT = Path(__file__).resolve().parents[2]


@dataclass
class BuiltWorkbook:
    ticker: str
    workbook: Workbook
    grid: Grid
    status: str  # Base forecast status
    banner: str
    selector: tuple[str, str] | None  # (sheet, cell) of the scenario selector
    scenarios: list[str] = field(default_factory=list)  # scenarios with expected projections


def labels() -> dict[str, str]:
    found = {k: v.label for k, v in load_concept_map().items()}
    found.update({k: v.label for k, v in load_model_inputs().inputs.items()})
    return found


def status_banner(base: Forecast, disagree: Sequence[str]) -> str:
    if base.status == VERIFIED:
        return (f"Forecast VERIFIED (Base, FY{base.origin_year} origin): projections FY+1..FY+3 "
                f"are live formulas; checks F1-F5 hold. Flags: {', '.join(base.flags) or '-'}")
    text = f"Forecast {base.status}: projected columns are empty. Reasons: " \
           + "; ".join(base.reasons)[:600]
    if disagree:
        text += (f". BC16: blocked only by DISAGREE on forecast inputs ({', '.join(disagree)}); "
                 "a documented GAP until the Correlation Agent (Phase 5) resolves it.")
    return text


def build_workbook(s: StatementSet, forecasts: Mapping[str, Forecast],
                   specs: Sequence[ScenarioSpec], config: ForecastConfig, as_of: date,
                   header: Mapping[str, Any] | None = None, disagree: Sequence[str] = (),
                   layout: WorkbookLayout | None = None,
                   item_labels: Mapping[str, str] | None = None,
                   reconciliation: Reconciliation | None = None) -> BuiltWorkbook:
    """The workbook of one company; reconciliation (Phase 5) adds the Correlation Agent's
    values and the final check to the Verification sheet."""
    layout = layout or load_workbook_layout()
    item_labels = dict(item_labels if item_labels is not None else labels())
    header = dict(header or {})
    names = [spec.name for spec in specs]
    base = forecasts[names[0]]
    hist = sorted(s.years())
    origin = base.origin_year if base.origin_year is not None else (max(hist) if hist else 0)
    cols = Columns(hist, [origin + i for i in range(1, config.horizon_years + 1)])
    grid = Grid(cols, names, layout.number_formats)
    wb = Workbook()
    wb.remove(wb.active)
    ws = {name: wb.create_sheet(name) for name in SHEETS}
    text = status_banner(base, disagree)

    ctx = None
    if base.origin_year is not None:
        revenue = lookup(s, origin, "revenue", as_of)
        ctx = ModelContext(base, forecasts, specs, config,
                           revenue.value if revenue.found else float("nan"))
    verified = base.status == VERIFIED and ctx is not None

    # 1. The statement templates: rows, historical cells, and the FY0 model keys.
    keys = statement_keys(ctx, cols) if verified else set()
    no_cogs = ctx.no_cogs if ctx is not None else (no_cogs_line(s, origin) if hist else False)
    sheets = StatementSheets(grid, s, layout, item_labels, no_cogs, keys, origin)
    sheets.plan()
    model_rows: dict[tuple[str, str], tuple[int, str]] = {}
    sheets.register(origin if verified else None, model_rows)

    # 2. The Forecast inputs (d:* keys), then every model line.
    title(ws[FORECAST], f"{s.issuer}: forecast drivers, scenarios and schedules",
          "MODEL_ASSUMPTION drivers in blue; everything projected is a formula")
    banner(ws[FORECAST], 2, text, cols.note)
    writer = ModelWriter(grid, ctx, ws, model_rows) if ctx is not None else None
    forecast_row = writer.forecast_inputs(4) if writer is not None else 4
    if writer is None:
        ws[FORECAST].cell(row=4, column=1, value="Check tolerance ($)")
        grid.value(ws[FORECAST], 4, 2, float(config.tolerance), key="d:tolerance", fmt="days")
    if verified:
        assert writer is not None
        writer.row[FORECAST] = forecast_row
        writer.plan()
        written = {line.key for line in writer.lines if line.sheet != FORECAST}
        if written != keys:
            raise ValueError(f"model lines changed between planning passes: {written ^ keys}")
        writer.write_lines()
        writer.all_checks()
    else:
        banner(ws[FORECAST], forecast_row, "Schedules: none. " + text, cols.note)
    if writer is not None:
        writer.scenario_status()

    # 3. The statement sheets: history, percent rows, styles.
    for sheet, statement in ((IS, "income statement"), (BS, "balance sheet"),
                             (CF, "cash flow statement")):
        title(ws[sheet], f"{s.issuer}: {statement} ($ millions)",
              f"As of {as_of}; actuals FY{hist[0]}-FY{hist[-1]} (A), projections "
              f"FY{cols.proj[0]}-FY{cols.proj[-1]} (E) under the scenario selected on the "
              f"Forecast sheet" if hist else "")
        sheets.write(ws[sheet])
        sheets.write_pct(ws[sheet], grid.scenarios if verified else [])
        sheets.style_projection(ws[sheet])
        if not verified:
            banner(ws[sheet], 3, "Projection FY+1..FY+3: " + text, cols.note)
            for y in cols.proj:
                grid.empty(ws[sheet], 3, cols.col(y), text[:250])
    grid.problems.extend(sheets.problems)
    if verified:
        assert writer is not None
        grid.problems.extend(writer.check_opening())  # after the history cells exist

    title(ws[RATIOS_SHEET], f"{s.issuer}: credit and operating ratios",
          "Formulas over the statement sheets (Appendix C.1); projected columns follow the "
          "scenario selector")
    year_header(ws[RATIOS_SHEET], 4, cols, label="Ratio")
    RatiosWriter(grid, ws[RATIOS_SHEET], s, ctx if verified else None).write(5)
    divider(ws[RATIOS_SHEET], cols, 4, ws[RATIOS_SHEET].max_row)
    rec = reconciliation
    info.verification(ws["Verification"], s, forecasts, layout.number_formats,
                      [v for r in rec.results for v in r.values] if rec else (),
                      rec.checks if rec else None)
    info.sources(ws["Sources"], s)
    info.cover(ws["Cover"], grid, s, forecasts, header, disagree, text)

    for sheet in (IS, BS, CF):
        widths(ws[sheet], cols)
        ws[sheet].freeze_panes = ws[sheet].cell(row=FIRST_ROW, column=cols.first)
    widths(ws[RATIOS_SHEET], cols)
    ws[RATIOS_SHEET].freeze_panes = ws[RATIOS_SHEET].cell(row=5, column=cols.first)
    widths(ws[FORECAST], cols, label=62)
    ws[FORECAST].freeze_panes = "B3"
    _text_not_formulas(wb, grid)
    wb.calculation.fullCalcOnLoad = True
    selector = grid.refs.get(("d:selector", None))
    return BuiltWorkbook(s.issuer, wb, grid, base.status, text, selector,
                         [n for n, f in forecasts.items() if f.status == VERIFIED]
                         if verified else [])


def _text_not_formulas(wb: Workbook, grid: Grid) -> None:
    """Text that starts with '=' (e.g. a driver note "= interest_rate") stays text."""
    for sheet in wb.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if cell.data_type == "f" and (sheet.title, cell.coordinate) not in grid.expected:
                    cell.data_type = "s"


# ---------------------------------------------------------------- comparison

def compare(values: Mapping[tuple[str, str], Any], grid: Grid, scenario: str) -> list[str]:
    """Mismatches between computed cell values and the expected values for one scenario."""
    problems = []
    for key, expected in grid.expected.items():
        if scenario not in expected and ALL not in expected:
            continue  # no projection for this scenario (GAP / INVALID in Python)
        want = expected.get(scenario, expected.get(ALL))
        got = values.get(key)
        tol = TOLERANCES[grid.tolerance.get(key, "dollar")]
        if isinstance(want, str) or isinstance(got, str):
            if got != want:
                problems.append(f"{key[0]}!{key[1]}: {got!r} != {want!r}")
            continue
        if got is None or want is None or abs(float(got) - float(want)) > tol:
            problems.append(f"{key[0]}!{key[1]}: {got!r} != {want!r} (tolerance {tol:g})")
    return problems


def evaluate_scenarios(built: BuiltWorkbook, workbook: Workbook | None = None
                       ) -> dict[str, list[str]]:
    """Independent evaluation of every formula under each scenario (selector switched)."""
    wb = workbook or built.workbook
    found: dict[str, list[str]] = {}
    for name in built.grid.scenarios:
        overrides = {built.selector: name} if built.selector else {}
        try:
            values = Evaluator(wb, overrides).evaluate_all()
        except FormulaError as exc:
            found[name] = [f"evaluation failed: {exc}"]
            continue
        found[name] = compare(values, built.grid, name)
    return found


def manifest(built: BuiltWorkbook) -> dict[str, Any]:
    return {"ticker": built.ticker, "status": built.status, "banner": built.banner,
            "selector": list(built.selector) if built.selector else None,
            "scenarios": list(built.grid.scenarios),
            "verified_scenarios": built.scenarios,
            "cells": {f"{s}!{c}": {"expected": e, "tolerance": built.grid.tolerance[(s, c)]}
                      for (s, c), e in built.grid.expected.items()},
            "gaps": [{"cell": f"{s}!{c}", "reason": r} for s, c, r in built.grid.gaps],
            "nils": [{"cell": f"{s}!{c}", "basis": b} for s, c, b in built.grid.nils],  # #152
            "problems": built.grid.problems}


# ---------------------------------------------------------------- run

def main(argv: list[str] | None = None) -> int:
    from finagent.evaluation.e1 import pinned_tickers
    from finagent.evidence import EvidenceStore
    from finagent.config import check_not_held_out, load_agent_config, load_forecast_inputs, \
        load_universe
    from finagent.model.report import _git, disagree_inputs
    from finagent.statements.build import StatementConfigs
    from finagent.verify.reconcile import check_record, company_statements
    from finagent.model.forecast import build_forecast
    from finagent.training.m1 import load_model

    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 11, 24))
    parser.add_argument("--run-id", default="workbooks-0003")
    parser.add_argument("--dataset-version", default="v3")
    parser.add_argument("--splits", default="validation")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--no-agent", action="store_true",
                        help="skip the Correlation Agent (reproduces the Phase 4d workbooks)")
    args = parser.parse_args(argv)

    store = EvidenceStore(ROOT / "evidence")
    universe = load_universe()
    classifier = load_model(ROOT / "models", args.dataset_version,
                            ROOT / "datasets" / f"rows_{args.dataset_version}.meta.json")
    configs = StatementConfigs.load()
    needs = load_forecast_inputs()
    config = load_forecast_config()
    specs = load_scenarios()
    layout = load_workbook_layout()
    item_labels = labels()
    agent = None if args.no_agent else load_agent_config()
    header = {
        "run_id": args.run_id, "git_commit": _git("rev-parse", "--short", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "evidence_manifest_sha256": hashlib.sha256(
            (ROOT / "evidence" / "manifest.json").read_bytes()).hexdigest(),
        "model_version": classifier.version, "as_of": args.as_of.isoformat(),
        "correlation_agent": agent is not None,
    }
    wanted = [t for t in args.tickers.split(",") if t]
    out = ROOT / "runs" / args.run_id
    out.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"header": header, "companies": {}}
    for split in args.splits.split(","):
        for ticker in pinned_tickers(store, universe, split, args.as_of):  # never held_out
            if wanted and ticker not in wanted:
                continue
            check_not_held_out([ticker], universe, include_held_out=False)
            record: dict[str, Any] = {"split": split}
            try:
                s, rec = company_statements(store, ticker, args.as_of, classifier, universe,
                                            configs, agent)
                forecasts = {spec.name: build_forecast(s, args.as_of, config, spec)
                             for spec in specs}
                disagree = disagree_inputs(s, needs)
                built = build_workbook(s, forecasts, specs, config, args.as_of, header,
                                       disagree, layout, item_labels, rec)
                path = out / f"{ticker}.xlsx"
                built.workbook.save(path)
                from openpyxl import load_workbook
                evaluation = evaluate_scenarios(built, load_workbook(path))
                (out / f"{ticker}.manifest.json").write_text(
                    json.dumps(manifest(built), indent=1, default=str), encoding="utf-8")
                record.update(status=built.status, banner=built.banner,
                              scenarios={n: f.status for n, f in forecasts.items()},
                              formula_cells=len(built.grid.expected),
                              gap_cells=len(built.grid.gaps), nil_cells=len(built.grid.nils),
                              problems=built.grid.problems,
                              evaluation={n: {"mismatches": len(p), "first": p[:5]}
                                          for n, p in evaluation.items()},
                              disagree=disagree,
                              agent_values=[f"{v.status} FY{v.fiscal_year} {v.item}"
                                            for r in rec.results for v in r.values]
                              if rec else None,
                              final_check={y: check_record(c) for y, c in rec.checks.items()}
                              if rec else None)
            except Exception as exc:  # reported, never hidden
                record["error"] = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            summary["companies"][ticker] = record
            print(ticker, record.get("status"), record.get("formula_cells"),
                  {n: v["mismatches"] for n, v in record.get("evaluation", {}).items()},
                  (record.get("error") or "")[:300])
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str),
                                      encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
