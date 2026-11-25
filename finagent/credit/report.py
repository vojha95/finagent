"""Credit and liquidity run (blueprint 8.12; Phase 7.1).

    python -m finagent.credit.report --run-id credit-0001 [--as-of 2026-11-24]

Validation companies only (held-out companies are refused). For each company, built once after
the Correlation Agent:
- the historical credit ratios of every fiscal year, with the operating working-capital change
  (#156);
- per scenario and projected year, both liquidity headroom measures side by side (#154) and the
  scoped debt-service coverage with its components (finagent.credit.debt_service);
- the D1 comparison (#154): the forecast rebuilt under each headroom basis, the scenarios and
  years with a shortfall under each, and so the target years for which the conditional debt
  goal would run (its condition is the liquidity goal's shortfall > 0 in the target year).

Writes runs/<run>/credit.json, liquidity.csv, debt_service.csv, ratios.csv and docs/credit_v1.md.
The default-path check (finagent.evaluation.default_path) is quoted from
runs/<run>/default_path_check.txt when it exists.
"""

import argparse
import csv
import hashlib
import json
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from finagent.config import (CASH_ONLY, CASH_PLUS_SECURITIES, HEADROOM_BASES, ScenarioSpec,
                             load_forecast_config)
from finagent.credit.debt_service import (EXCLUDED_OBLIGATIONS, FORMULA, NEGATIVE_LABEL,
                                          WITHHELD_REASONS, DebtService, debt_service_table)
from finagent.credit.liquidity import LiquidityRow, liquidity_table
from finagent.model.forecast import VERIFIED, build_forecast
from finagent.model.ratio_inputs import historical_ratios
from finagent.model.ratios import RATIOS, RatioValue
from finagent.model.report import ROOT, _git, _m, _table, write_report
from finagent.records import Forecast
from finagent.statements.build import StatementSet

BASIS_LABEL = {CASH_PLUS_SECURITIES: "4c: cash + securities x (1 - haircut) - floor",
               CASH_ONLY: "8.12: cash - floor"}


@dataclass
class CompanyCredit:
    """One company's credit run."""

    ticker: str
    split: str
    origin_year: int | None = None
    status: dict[str, str] = field(default_factory=dict)  # scenario -> forecast status
    ratios: dict[int, dict[str, RatioValue]] = field(default_factory=dict)
    liquidity: dict[str, list[LiquidityRow]] = field(default_factory=dict)
    debt_service: dict[str, list[DebtService]] = field(default_factory=dict)
    # basis -> scenario -> projected years with a shortfall / without a headroom line
    shortfalls: dict[str, dict[str, list[int]]] = field(default_factory=dict)
    not_computed: dict[str, dict[str, list[int]]] = field(default_factory=dict)
    inconsistencies: list[str] = field(default_factory=list)
    error: str | None = None


def shortfall_years(f: Forecast) -> tuple[list[int], list[int]]:
    """(years with liquidity_shortfall > 0, years without a headroom line) of a forecast."""
    found = [y.fiscal_year for y in f.years if y.lines.get("liquidity_shortfall", 0.0) > 0]
    missing = [y.fiscal_year for y in f.years if "liquidity_shortfall" not in y.lines]
    return found, missing


def basis_consistency(rows: Sequence[LiquidityRow], f: Forecast, basis: str,
                      tolerance: float) -> list[str]:
    """The forecast built on `basis` against the side-by-side measure of the same basis."""
    found = []
    for row, y in zip(rows, f.years):
        want = row.cash_shortfall if basis == CASH_ONLY else row.shortfall_with_reserve
        got = y.lines.get("liquidity_shortfall")
        if (want is None) != (got is None) or (want is not None and got is not None
                                                and abs(want - got) > tolerance):
            found.append(f"{row.scenario} FY{row.fiscal_year} {basis}: forecast {got} vs "
                         f"measure {want}")
    return found


def company_credit(ticker: str, split: str, s: StatementSet, as_of: date,
                   scenarios: Sequence[ScenarioSpec]) -> CompanyCredit:
    """Every measure of the run for one statement set (module docstring)."""
    c = CompanyCredit(ticker, split)
    c.ratios = {y: historical_ratios(s, y) for y in s.years()}
    default = load_forecast_config().headroom_basis
    for basis in (default,) + tuple(b for b in HEADROOM_BASES if b != default):
        config = load_forecast_config(headroom_basis=basis)
        c.shortfalls[basis], c.not_computed[basis] = {}, {}
        for spec in scenarios:
            f = build_forecast(s, as_of, config, spec)
            c.shortfalls[basis][spec.name], c.not_computed[basis][spec.name] = \
                shortfall_years(f)
            if basis == default:  # the measures shown come from the default forecast
                c.origin_year = f.origin_year
                c.status[spec.name] = f.status
                c.liquidity[spec.name] = liquidity_table(f, spec, config.tolerance)
                c.debt_service[spec.name] = debt_service_table(f)
            c.inconsistencies += basis_consistency(c.liquidity[spec.name], f, basis,
                                                   config.tolerance)
    c.inconsistencies += [f"{r.scenario} FY{r.fiscal_year}: {r.status} {r.detail}"
                          for rows in c.liquidity.values() for r in rows
                          if r.status not in (VERIFIED, "GAP")]
    return c


def condition_runs(c: CompanyCredit, basis: str, scenario: str, horizon: int) -> list[str]:
    """The targets (+1..+horizon) for which the conditional debt goal runs, is skipped or has
    an unknown condition, under one basis."""
    if c.origin_year is None or c.status.get(scenario) != VERIFIED:
        return ["unknown (forecast not VERIFIED)"]
    out = []
    for n in range(1, horizon + 1):
        year = c.origin_year + n
        if year in c.not_computed[basis][scenario]:
            out.append(f"+{n} unknown")
        elif year in c.shortfalls[basis][scenario]:
            out.append(f"+{n} runs")
    return out or ["skipped for every target"]


# ---------------------------------------------------------------- outputs

def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def write_outputs(out: Path, companies: Sequence[CompanyCredit], header: Mapping[str, Any]
                  ) -> None:
    out.mkdir(parents=True, exist_ok=True)
    document = {"header": header, "companies": [{
        "ticker": c.ticker, "split": c.split, "origin_year": c.origin_year, "error": c.error,
        "status": c.status, "shortfalls": c.shortfalls, "not_computed": c.not_computed,
        "inconsistencies": c.inconsistencies,
        "liquidity": {n: [asdict(r) for r in rows] for n, rows in c.liquidity.items()},
        "debt_service": {n: [dict(asdict(r), finance_lease_payments=r.finance_lease_payments)
                             for r in rows] for n, rows in c.debt_service.items()},
        "ratios": {str(y): {k: asdict(v) for k, v in r.items()} for y, r in c.ratios.items()},
    } for c in companies]}
    (out / "credit.json").write_text(json.dumps(_plain(document), indent=1), encoding="utf-8")
    tables: dict[str, tuple[list[str], list[list[Any]]]] = {
        "liquidity.csv": (["ticker", "split"] + list(LiquidityRow.__dataclass_fields__), []),
        "debt_service.csv": (["ticker", "split"] + list(DebtService.__dataclass_fields__)
                             + ["finance_lease_payments"], []),
        "ratios.csv": (["ticker", "split", "fiscal_year", "ratio_id", "value", "status",
                        "detail"], [])}
    for c in companies:
        for rows in c.liquidity.values():
            tables["liquidity.csv"][1].extend([c.ticker, c.split] + list(asdict(r).values())
                                              for r in rows)
        for rows in c.debt_service.values():
            tables["debt_service.csv"][1].extend(
                [c.ticker, c.split] + list(asdict(r).values()) + [r.finance_lease_payments]
                for r in rows)
        for y, ratios in c.ratios.items():
            tables["ratios.csv"][1].extend([c.ticker, c.split, y, k, v.value, v.status, v.detail]
                                           for k, v in ratios.items())
    for name, (head, body) in tables.items():
        with (out / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(head)
            writer.writerows(body)


def _x(value: float | None, status: str = VERIFIED) -> str:
    if status == "NOT_MEANINGFUL":
        return "n.m."
    return "—" if value is None else f"{value:.2f}x"


def _years(years: Sequence[int]) -> str:
    return ", ".join(f"FY{y}" for y in years) if years else "none"


def headline(companies: Sequence[CompanyCredit], names: Sequence[str]) -> list[str]:
    """Per company and scenario: scoped DSCR by projected year, FY+1 headroom both ways, and
    the shortfall years under each basis."""
    rows = []
    for c in companies:
        if c.error:
            rows.append([c.ticker, "ERROR", c.error.splitlines()[0]] + [""] * 6)
            continue
        for n in names:
            ds, liq = c.debt_service.get(n, []), c.liquidity.get(n, [])
            if not ds:
                rows.append([c.ticker, n, c.status.get(n, "—")] + ["—"] * 6)
                continue
            first = liq[0]
            rows.append([c.ticker if n == names[0] else "", n]
                        + [_x(d.scoped_dscr, d.status) for d in ds]
                        + [_m(first.cash_headroom), _m(first.headroom_with_reserve),
                           _years(c.shortfalls[CASH_ONLY][n]),
                           _years(c.shortfalls[CASH_PLUS_SECURITIES][n])])
    origin = {c.origin_year for c in companies if c.origin_year}
    years = [f"DSCR FY+{i}" for i in (1, 2, 3)]
    return _table(["Company", "Scenario"] + years
                  + ["Cash headroom FY+1 ($m)", "Headroom incl. reserve FY+1 ($m)",
                     "Shortfall years, cash only (8.12)",
                     "Shortfall years, incl. reserve (4c, default)"], rows) + [
        "", f"Origin fiscal years: {sorted(origin)} (each company's latest 10-K)."]


def d1_section(companies: Sequence[CompanyCredit], names: Sequence[str],
               horizon: int) -> list[str]:
    rows = []
    for c in companies:
        for n in names:
            if c.error:
                continue
            rows.append([c.ticker, n] + ["; ".join(condition_runs(c, b, n, horizon))
                                         for b in (CASH_ONLY, CASH_PLUS_SECURITIES)])
    changed = [r for r in rows if r[2] != r[3]]
    return [
        "## D1 (#154): headroom basis options and their effect (decided by Vineet: the 4c "
        "basis stays, BC28)", "",
        "The basis is one switch, `configs/forecast.yaml` `switches.headroom_basis`; the default "
        "stays `cash_plus_haircut_securities` (4c); Vineet kept it (D1, recorded as BC28), so no "
        "output changes. Both measures are always shown (the tables above, `liquidity.csv`, the "
        "workbook's liquidity block).", "",
        f"- **cash_only** ({BASIS_LABEL[CASH_ONLY]}). Follows 8.12 and Appendix B F5: the "
        "conditional securities reserve is shown separately and never counted as cash. Headroom "
        "is computable for every company, including one whose short-term investments are not "
        "accepted.",
        f"- **cash_plus_haircut_securities** ({BASIS_LABEL[CASH_PLUS_SECURITIES]}). The 4c "
        "decision (liquidity = cash + short-term investments, #84). Needs accepted short-term "
        "investments, otherwise the headroom is not computed (HEADROOM_NOT_COMPUTED) and the "
        "conditional debt goal's condition is unknown.", "",
        "The conditional debt goal (\"if there is a shortfall, check debt service\") runs when "
        "the liquidity goal's `liquidity_shortfall` in the target year (default FY+1, the "
        "question may name +2 or +3) is above 0; with no shortfall it is SKIPPED; with no "
        "verified shortfall value its condition is unknown (GAP CONDITION_UNKNOWN). Targets for "
        "which it runs, per basis:", ""] + _table(
        ["Company", "Scenario", "cash_only (8.12)", "cash_plus_haircut_securities (4c, default)"],
        rows) + ["", f"Rows where the basis changes the outcome: {len(changed)} of {len(rows)}"
                 + (": " + "; ".join(f"{r[0]} {r[1]}" for r in changed) if changed else "."),
                 ""]


DscrKey = tuple[str, str, int]  # (ticker, scenario, fiscal year)


def load_dscr(path: Path) -> dict[DscrKey, str]:
    """A previous run's scoped DSCR per company, scenario and year, as shown (debt_service.csv)."""
    found = {}
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            value = float(row["scoped_dscr"]) if row["scoped_dscr"] else None
            found[(row["ticker"], row["scenario"], int(row["fiscal_year"]))] = \
                _x(value, row["status"])
    return found


def before_after(companies: Sequence[CompanyCredit], before: Mapping[DscrKey, str],
                 before_run: str, names: Sequence[str]) -> list[str]:
    """The scoped DSCR of a previous run beside this run's, per company, scenario and projected
    year (first used for #168 / BC27, where the previous run's numerator was CFO - capex)."""
    rows = []
    for c in companies:
        for n in names:
            ds = c.debt_service.get(n, [])
            if not ds:
                continue
            rows.append([c.ticker if n == names[0] else "", n] + [
                f"{before.get((c.ticker, n, d.fiscal_year), '—')} → {_x(d.scoped_dscr, d.status)}"
                for d in ds])
    return [f"## Scoped DSCR before and after: `{before_run}` → this run", "",
            f"Before: `{before_run}`. After: this run, numerator CFO + funded and finance-lease "
            "interest - capex (BC27; the model's CFO is after interest). credit-0001 → "
            "credit-0002 was the BC27 change itself (numerator CFO - capex before it).", ""
            ] + _table(
        ["Company", "Scenario"] + [f"DSCR FY+{i} (before → after)" for i in (1, 2, 3)],
        rows) + [""]


def render_report(companies: Sequence[CompanyCredit], header: Mapping[str, Any],
                  scenarios: Sequence[ScenarioSpec], default_path: str | None,
                  before: Mapping[DscrKey, str] | None = None) -> str:
    names = [s.name for s in scenarios]
    horizon = load_forecast_config().horizon_years
    lines = [
        "# Credit and liquidity v1 (blueprint 8.12; Phase 7.1)", "",
        f"Generated by `python -m finagent.credit.report` (run `{header['run_id']}`, commit "
        f"`{header['git_commit']}`{' + uncommitted changes' if header['git_dirty'] else ''}, "
        f"evidence manifest `{header['evidence_manifest_sha256'][:12]}`, as of "
        f"{header['as_of']}). Outputs: `runs/{header['run_id']}/credit.json`, `liquidity.csv`, "
        "`debt_service.csv`, `ratios.csv`. Validation companies only; the statement sets are "
        "after the Correlation Agent.", "",
        "## Headline: scoped DSCR and both headroom measures", "",
        f"Scoped scheduled debt-service coverage = {FORMULA} "
        "(CALCULATION_FROM_MODEL_ASSUMPTION; BC27: the model's CFO is after interest, so "
        "interest is added back), per scenario and projected year; scheduled principal is gross "
        "of refinancing (D6). n.m. = not meaningful (no positive debt service). A negative "
        f"coverage is shown as computed, labelled \"{NEGATIVE_LABEL}\" (#169). Excluded: "
        + "; ".join(EXCLUDED_OBLIGATIONS) + ". The comprehensive forward DSCR is withheld: "
        + " ".join(WITHHELD_REASONS), ""]
    lines += headline(companies, names) + [""]
    if before is not None:
        lines += before_after(companies, before, header.get("compare_with", ""), names)
    problems = [f"{c.ticker}: {p}" for c in companies for p in c.inconsistencies]
    lines += [f"Consistency (each basis's forecast lines against the side-by-side measure, and "
              f"each forecast headroom line recomputed): {len(problems)} problems"
              + (": " + "; ".join(problems[:10]) if problems else "."), ""]
    lines += d1_section(companies, names, horizon)
    lines += ["## Operating working-capital change (#156)", "",
              "(receivables + inventory - payables) at the close minus at the opening; lines "
              "the filer does not report are left out (never 0). Latest fiscal year:", ""]
    owc = []
    for c in companies:
        if c.ratios:
            y = max(c.ratios)
            r = c.ratios[y]["operating_working_capital_change"]
            owc.append([c.ticker, f"FY{y}", _m(r.value), r.status, r.detail or ""])
    lines += _table(["Company", "Year", "Change ($m)", "Status", "Detail"], owc) + [""]
    lines += ["## Default-path check", ""]
    lines += (["```", default_path.strip(), "```"] if default_path else
              ["Not run: `runs/<run>/default_path_check.txt` is missing."]) + [""]
    lines += ["## Credit ratio coverage (C.1)", ""]
    counts = {}
    for c in companies:
        for ratios in c.ratios.values():
            for k, v in ratios.items():
                counts.setdefault(k, {}).setdefault(v.status, 0)
                counts[k][v.status] += 1
    lines += _table(["Ratio", "OK", "NM", "GAP", "NOT_APPLIED"],
                    [[spec.ratio_id] + [counts.get(spec.ratio_id, {}).get(s, 0)
                                        for s in ("OK", "NM", "GAP", "NOT_APPLIED")]
                     for spec in RATIOS])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    from finagent.config import load_agent_config, load_scenarios, load_universe
    from finagent.evaluation.e1 import pinned_tickers
    from finagent.evidence import EvidenceStore
    from finagent.statements.build import StatementConfigs
    from finagent.training.m1 import load_model
    from finagent.verify.reconcile import company_statements

    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 11, 24))
    parser.add_argument("--run-id", default="credit-0001")
    parser.add_argument("--report", default=str(ROOT / "docs" / "credit_v1.md"))
    parser.add_argument("--compare-with", default=None,
                        help="a previous credit run whose scoped DSCR is shown beside this one")
    args = parser.parse_args(argv)
    store, universe = EvidenceStore(ROOT / "evidence"), load_universe()
    classifier = load_model(ROOT / "models", "v3", ROOT / "datasets" / "rows_v3.meta.json")
    configs, agent, scenarios = StatementConfigs.load(), load_agent_config(), load_scenarios()
    companies = []
    for ticker in pinned_tickers(store, universe, "validation", args.as_of):  # no held_out
        try:
            s, _ = company_statements(store, ticker, args.as_of, classifier, universe, configs,
                                      agent)
            companies.append(company_credit(ticker, "validation", s, args.as_of, scenarios))
        except Exception as exc:  # reported, never hidden
            companies.append(CompanyCredit(ticker, "validation",
                                           error=f"{type(exc).__name__}: {exc}\n"
                                                 f"{traceback.format_exc()}"))
    header = {
        "run_id": args.run_id, "git_commit": _git("rev-parse", "--short", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "evidence_manifest_sha256": hashlib.sha256(
            (ROOT / "evidence" / "manifest.json").read_bytes()).hexdigest(),
        "model_version": classifier.version, "as_of": args.as_of.isoformat(),
        "default_headroom_basis": load_forecast_config().headroom_basis,
        "scenarios": [asdict(s) for s in scenarios], "compare_with": args.compare_with or ""}
    out = ROOT / "runs" / args.run_id
    write_outputs(out, companies, header)
    check = out / "default_path_check.txt"
    before = (load_dscr(ROOT / "runs" / args.compare_with / "debt_service.csv")
              if args.compare_with else None)
    write_report(Path(args.report), render_report(
        companies, header, scenarios, check.read_text(encoding="utf-8") if check.exists()
        else None, before))
    for c in companies:
        print(c.ticker, c.status, c.error.splitlines()[0] if c.error else "",
              c.inconsistencies[:3])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
