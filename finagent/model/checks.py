"""Model checks F1-F4 on every projected year (blueprint 8.10, Appendix B).

Recomputed from the projected lines, independently of the projection code:
- F1 assets = liabilities + equity, summing the balance sheet lines afresh ($0.01; the opening
  balance sheet is checked too, as year 0).
- F2 closing cash = opening cash + EBIT + depreciation + working-capital change - cash tax -
  interest - capex - principal - dividends + new debt - repurchases ($0.01).
- F3 working-capital cash flow = -(change in receivables + inventory - payables) ($0.01).
- F4 0 <= scheduled debt principal <= opening debt; 0 <= lease principal <= opening lease
  liability (exact).
Absent lines (items the filer does not have) are left out of the sums, never counted as 0.
"""

from collections.abc import Mapping, Sequence

from finagent.records import ModelCheck, ProjectedYear

ASSETS = ("cash", "short_term_investments", "receivables", "inventory", "ppe", "operating_rou",
          "other_assets")
LIABILITIES = ("payables", "debt", "operating_lease", "finance_lease", "other_liabilities")
EQUITY = ("retained_earnings", "other_equity")
WORKING_CAPITAL = (("receivables", -1), ("inventory", -1), ("payables", 1))


def _sum(lines: Mapping[str, float], names: Sequence[str]) -> float:
    return sum(lines[n] for n in names if n in lines)


def _check(check_id: str, year: int, residual: float, tolerance: float,
           detail: str) -> ModelCheck:
    return ModelCheck(check_id, year, "PASS" if abs(residual) <= tolerance else "FAIL",
                      residual, tolerance, detail)


def f1_balance(lines: Mapping[str, float], year: int, tolerance: float) -> ModelCheck:
    residual = _sum(lines, ASSETS) - _sum(lines, LIABILITIES) - _sum(lines, EQUITY)
    return _check("F1", year, residual, tolerance, "assets - liabilities - equity")


def f2_cash(opening: Mapping[str, float], lines: Mapping[str, float], year: int,
            tolerance: float) -> ModelCheck:
    g = lambda name: lines.get(name, 0.0)  # noqa: E731  absent flows (no dividends) add nothing
    recomputed = (opening["cash"] + g("ebit") + g("depreciation") + g("working_capital_cf")
                  - g("cash_tax") - g("interest") - g("capex") - g("principal")
                  - g("dividends") + g("new_debt") - g("repurchases"))
    return _check("F2", year, lines["cash"] - recomputed, tolerance,
                  "closing cash - independent cash recomputation")


def f3_working_capital(opening: Mapping[str, float], lines: Mapping[str, float], year: int,
                       tolerance: float) -> ModelCheck:
    recomputed = sum(sign * (lines[n] - opening[n]) for n, sign in WORKING_CAPITAL
                     if n in lines and n in opening)
    return _check("F3", year, lines["working_capital_cf"] - recomputed, tolerance,
                  "working-capital cash flow + change in (receivables + inventory - payables)")


def f4_principal(opening: Mapping[str, float], lines: Mapping[str, float],
                 year: int) -> ModelCheck:
    """Residual: the largest breach (0 when every bound holds); exact."""
    breaches = []
    for amount, balance in (("debt_principal", "debt"),
                            ("operating_lease_principal", "operating_lease"),
                            ("finance_lease_principal", "finance_lease")):
        if amount not in lines:
            continue
        value, cap = lines[amount], opening[balance]
        breach = max(-value, value - cap, 0.0)
        if breach > 0:
            breaches.append((breach, f"{amount} {value:.0f} outside [0, {balance} {cap:.0f}]"))
    worst = max(breaches, default=(0.0, "all principal within [0, opening balance]"))
    return ModelCheck("F4", year, "FAIL" if breaches else "PASS", worst[0], 0.0, worst[1])


def run_checks(opening, years: Sequence[ProjectedYear], tolerance: float) -> list[ModelCheck]:
    """F1 on the opening balance sheet, then F1-F4 on every projected year."""
    previous = {line: o.value for line, o in opening.lines.items()}
    checks = [f1_balance(previous, years[0].fiscal_year - 1 if years else 0, tolerance)]
    for year in years:
        checks += [f1_balance(year.lines, year.fiscal_year, tolerance),
                   f2_cash(previous, year.lines, year.fiscal_year, tolerance),
                   f3_working_capital(previous, year.lines, year.fiscal_year, tolerance),
                   f4_principal(previous, year.lines, year.fiscal_year)]
        previous = year.lines
    return checks
