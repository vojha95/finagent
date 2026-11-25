"""Credit and operating ratios (blueprint Appendix C.1, 8.12; Phase 4d).

One definition table (RATIOS) serves two uses: compute() evaluates a ratio from statement values
in Python (the independent recomputation behind the workbook's Ratios sheet, F6), and the
workbook turns the same definition into a live Excel formula over cell references.

Values are floats (model calculations). Inputs are a mapping key -> value; `absent` names the
optional keys the filer does not have (left out of a sum, never counted as 0). A required key
that is neither present nor absent makes the ratio a GAP. A non-positive denominator gives
"not meaningful" (NM), never a number (C.1).

The operating working-capital change (8.12, #156) is a "balance_change" row in dollars:
(receivables + inventory - payables) at the close minus the same at the opening; a line the filer
does not report is left out on both dates (never 0); a reported line without its opening balance
makes it a GAP; a filer with none of the three lines has NOT_APPLIED.

Equity is the balance-sheet equity of identity I9 (every equity operand: total equity, or parent
equity plus NCI, plus temporary equity shown on the face), the same equity the integrated model
carries as retained earnings + other equity, so historical and projected ratios compare.
Funded debt is the statement set's funded_debt (long-term debt + current portion + short-term
borrowings + commercial paper; finance leases only when the filer's debt concept includes them).
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Literal

RatioKind = Literal["ratio", "average_days", "difference", "growth", "balance_change"]
RatioStatus = Literal["OK", "NM", "GAP", "NOT_APPLIED"]
NM_TEXT = "n.m."

Term = tuple[int, str, bool]  # (sign, key, optional)


@dataclass(frozen=True)
class RatioSpec:
    ratio_id: str
    label: str
    kind: RatioKind
    numerator: tuple[Term, ...] = ()
    denominator: tuple[Term, ...] = ()
    fmt: str = "ratio"  # ratio | percent | days | dollars
    group: str = "credit"  # credit | operating


@dataclass(frozen=True)
class RatioValue:
    ratio_id: str
    value: float | None
    status: RatioStatus
    detail: str = ""


def _t(key: str, sign: int = 1, optional: bool = False) -> Term:
    return (sign, key, optional)


RATIOS: tuple[RatioSpec, ...] = (
    RatioSpec("current_ratio", "Current ratio", "ratio",
              (_t("current_assets"),), (_t("current_liabilities"),)),
    RatioSpec("quick_ratio", "Quick ratio", "ratio",
              (_t("cash"), _t("short_term_investments", optional=True),
               _t("receivables", optional=True)), (_t("current_liabilities"),)),
    RatioSpec("funded_debt_to_equity", "Funded debt to equity", "ratio",
              (_t("funded_debt"),), (_t("equity"),)),
    RatioSpec("debt_plus_leases_to_equity", "Debt plus leases to equity", "ratio",
              (_t("funded_debt"), _t("operating_lease", optional=True),
               _t("finance_lease", optional=True)), (_t("equity"),)),
    RatioSpec("total_liabilities_to_assets", "Total liabilities to assets", "ratio",
              (_t("total_liabilities"),), (_t("total_assets"),)),
    RatioSpec("ebit_to_gross_interest", "EBIT to gross interest", "ratio",
              (_t("ebit"),), (_t("gross_interest"),)),
    RatioSpec("cfo_to_current_liabilities", "CFO to current liabilities", "ratio",
              (_t("cfo"),), (_t("current_liabilities"),)),
    RatioSpec("fcf_to_funded_debt", "FCF to funded debt", "ratio",
              (_t("cfo"), _t("capex", -1)), (_t("funded_debt"),)),
    RatioSpec("dso", "DSO (days)", "average_days", (_t("receivables"),), (_t("revenue"),),
              fmt="days"),
    RatioSpec("dio", "DIO (days)", "average_days", (_t("inventory"),), (_t("cogs"),),
              fmt="days"),
    RatioSpec("dpo", "DPO (days)", "average_days", (_t("payables"),), (_t("cogs"),),
              fmt="days"),
    RatioSpec("cash_conversion_cycle", "Cash conversion cycle (days)", "difference",
              (_t("dso"), _t("dio", optional=True), _t("dpo", -1, optional=True)), fmt="days"),
    RatioSpec("operating_working_capital_change",
              "Operating working-capital change (receivables + inventory - payables, $)",
              "balance_change", (_t("receivables", optional=True),
                                 _t("inventory", optional=True),
                                 _t("payables", -1, optional=True)), fmt="dollars"),
    RatioSpec("revenue_growth", "Revenue growth", "growth", (_t("revenue"),),
              (_t("revenue_prior"),), fmt="percent", group="operating"),
    RatioSpec("gross_margin", "Gross margin", "ratio", (_t("gross_profit"),),
              (_t("revenue"),), fmt="percent", group="operating"),
    RatioSpec("ebit_margin", "EBIT margin", "ratio", (_t("ebit"),), (_t("revenue"),),
              fmt="percent", group="operating"),
    RatioSpec("net_margin", "Net margin", "ratio", (_t("net_income"),), (_t("revenue"),),
              fmt="percent", group="operating"),
    RatioSpec("capex_to_revenue", "Capex to revenue", "ratio", (_t("capex"),),
              (_t("revenue"),), fmt="percent", group="operating"),
    RatioSpec("fcf_margin", "FCF margin", "ratio", (_t("cfo"), _t("capex", -1)),
              (_t("revenue"),), fmt="percent", group="operating"),
)
BY_ID = {r.ratio_id: r for r in RATIOS}


def _sum(terms: tuple[Term, ...], values: Mapping[str, float],
         absent: Collection[str]) -> tuple[float | None, list[str], bool]:
    """(signed sum, missing keys, all-absent) over the terms; absent optional keys left out."""
    total, missing, used = 0.0, [], 0
    for sign, key, optional in terms:
        if key in values:
            total += sign * values[key]
            used += 1
        elif not (optional and key in absent):
            missing.append(key)
    return (None if missing else total), missing, used == 0


def compute(spec: RatioSpec, values: Mapping[str, float], absent: Collection[str] = (),
            days: float | None = None, opening: Mapping[str, float] | None = None) -> RatioValue:
    """One ratio. For average_days, `opening` holds the opening balances and `days` the period
    days; for difference, `values` holds the component ratios already computed."""
    rid = spec.ratio_id
    if spec.kind == "balance_change":
        return balance_change(spec, values, absent, opening or {})
    if spec.kind == "difference":
        total, missing, _ = _sum(spec.numerator, values, absent)
        if missing:
            return RatioValue(rid, None, "GAP", f"components without a value: {missing}")
        return RatioValue(rid, total, "OK")
    balance_key = spec.numerator[0][1]
    if spec.kind == "average_days" and balance_key in absent:
        return RatioValue(rid, None, "NOT_APPLIED", f"{balance_key} is not reported")
    den, den_missing, _ = _sum(spec.denominator, values, absent)
    if spec.kind == "average_days":
        opening = opening or {}
        missing = ([balance_key] if balance_key not in values else []) + \
            ([f"opening {balance_key}"] if balance_key not in opening else []) + den_missing
        if days is None:
            missing.append("period days")
        if missing:
            return RatioValue(rid, None, "GAP", f"inputs without a value: {missing}")
        assert den is not None and days is not None
        if den <= 0:
            return RatioValue(rid, None, "NM", "non-positive denominator")
        average = (opening[balance_key] + values[balance_key]) / 2
        return RatioValue(rid, average * days / den, "OK")
    num, num_missing, _ = _sum(spec.numerator, values, absent)
    if num_missing or den_missing:
        return RatioValue(rid, None, "GAP", f"inputs without a value: {num_missing + den_missing}")
    assert num is not None and den is not None
    if den <= 0:
        return RatioValue(rid, None, "NM", "non-positive denominator")
    if spec.kind == "growth":
        return RatioValue(rid, num / den - 1, "OK")
    return RatioValue(rid, num / den, "OK")


def balance_change(spec: RatioSpec, values: Mapping[str, float], absent: Collection[str],
                   opening: Mapping[str, float]) -> RatioValue:
    """Signed sum of (closing - opening) over the terms; absent lines left out (module doc)."""
    rid = spec.ratio_id
    used = [(sign, key) for sign, key, _ in spec.numerator if key not in absent]
    opening_only = [key for _, key, _ in spec.numerator if key in absent and key in opening]
    if opening_only:  # reported at the opening, not at the close: the change is not known
        return RatioValue(rid, None, "GAP", f"reported at the opening only: {opening_only}")
    if not used:
        return RatioValue(rid, None, "NOT_APPLIED", "none of the lines is reported")
    missing = [key for _, key in used if key not in values] + \
        [f"opening {key}" for _, key in used if key not in opening]
    if missing:
        return RatioValue(rid, None, "GAP", f"inputs without a value: {missing}")
    return RatioValue(rid, sum(sign * (values[key] - opening[key]) for sign, key in used), "OK")


def compute_all(values: Mapping[str, float], absent: Collection[str] = (),
                days: float | None = None,
                opening: Mapping[str, float] | None = None) -> dict[str, RatioValue]:
    """Every ratio of RATIOS for one period; the cash conversion cycle from DSO, DIO, DPO."""
    found: dict[str, RatioValue] = {}
    for spec in RATIOS:
        if spec.kind == "difference":
            parts = {k: found[k].value for _, k, _ in spec.numerator
                     if found[k].status == "OK" and found[k].value is not None}
            gone = {k for _, k, _ in spec.numerator if found[k].status == "NOT_APPLIED"}
            blocked = [k for _, k, _ in spec.numerator if found[k].status in ("NM",)]
            if blocked:
                found[spec.ratio_id] = RatioValue(spec.ratio_id, None, "NM",
                                                  f"not meaningful: {blocked}")
                continue
            found[spec.ratio_id] = compute(spec, parts, gone)  # type: ignore[arg-type]
        else:
            found[spec.ratio_id] = compute(spec, values, absent, days, opening)
    return found
