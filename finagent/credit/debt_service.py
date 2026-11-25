"""Debt service: the scoped measure plus explicit withholding (blueprint 8.12; BC27).

Scoped scheduled debt-service coverage, per scenario and projected year
(CALCULATION_FROM_MODEL_ASSUMPTION, unit x), on the cash flow available for debt service
(BC27, #168: the model's CFO is after interest, so the interest in the denominator is added
back to the numerator):

    (CFO + funded interest + finance-lease interest - capex)
        / (scheduled long-term principal + funded interest
           + finance-lease principal + finance-lease interest)

Scheduled principal is gross: the maturities of the filed schedule, before any refinancing the
refinance switch assumes (D6). Lines the filer does not have (no debt, no finance lease) are
left out of both sides, never 0; a filer with neither has no scoped debt service
(NOT_APPLIED). A non-positive denominator makes the coverage not meaningful (value None; C.1).
A negative numerator gives a negative coverage, shown as computed (never clamped) with the
label "capex exceeds modelled operating cash flow" in the basis (#169).

Each value is recomputed a second way before it is accepted: the numerator from the cash
movement plus the model's aggregate interest line (closing cash - opening cash - CFF + interest
= CFO + CFI + interest = CFO - capex + interest) and the denominator from the model's aggregate
interest and principal lines; a relative difference above 1e-9 is INVALID.

The comprehensive forward DSCR is always withheld (EVIDENCE_GAP) with the three reasons below.
The answer states the obligations the scoped measure excludes.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from finagent.records import Forecast

VERIFIED, INVALID = "VERIFIED", "INVALID"
NOT_MEANINGFUL, NOT_APPLIED = "NOT_MEANINGFUL", "NOT_APPLIED"
RECOMPUTE_TOLERANCE = 1e-9  # relative
COMPREHENSIVE_DSCR_WITHHELD = "COMPREHENSIVE_DSCR_WITHHELD"
WITHHELD_REASONS = (
    "Forward cash interest coverage across all obligations is not reconciled.",
    "Short-term borrowing rollover and other financing obligations are not fully scheduled.",
    "A comparable forward cash flow available for inclusive debt service is not independently "
    "established.",
)
EXCLUDED_OBLIGATIONS = (
    "short-term borrowings and commercial paper (rollover not scheduled)",
    "operating-lease payments (already deducted in CFO)",
    "purchase obligations, pensions and other financing obligations (not scheduled)",
    "dividends and share repurchases (discretionary)",
)
FORMULA = ("(CFO + funded interest + finance-lease interest - capex) / (scheduled long-term "
           "principal + funded interest + finance-lease principal + finance-lease interest)")
NEGATIVE_LABEL = "capex exceeds modelled operating cash flow"  # #169
COMPONENTS = (("scheduled_principal", "debt_principal"), ("funded_interest", "debt_interest"),
              ("finance_lease_principal", "finance_lease_principal"),
              ("finance_lease_interest", "finance_lease_interest"))


@dataclass(frozen=True)
class DebtService:
    """The scoped debt service of one scenario and projected year."""

    issuer: str
    scenario: str
    fiscal_year: int
    cfo: float
    capex: float
    cfo_less_capex: float
    interest_added_back: float  # funded + finance-lease interest in scope (BC27); 0.0 with none
    cash_available: float  # CFO + interest added back - capex: the numerator (BC27)
    scheduled_principal: float | None  # gross of refinancing (D6); None: no debt
    funded_interest: float | None
    finance_lease_principal: float | None  # None: no finance lease
    finance_lease_interest: float | None
    debt_service: float | None  # None: no scheduled obligation in scope (NOT_APPLIED)
    scoped_dscr: float | None  # None: NOT_MEANINGFUL, NOT_APPLIED or INVALID
    status: str  # VERIFIED | NOT_MEANINGFUL | NOT_APPLIED | INVALID
    detail: str = ""

    @property
    def finance_lease_payments(self) -> float | None:
        parts = [v for v in (self.finance_lease_principal, self.finance_lease_interest)
                 if v is not None]
        return sum(parts) if parts else None

    @property
    def negative(self) -> bool:
        """The coverage is negative: capex exceeds the cash flow available (#169)."""
        return self.scoped_dscr is not None and self.scoped_dscr < 0


def basis_text(principal_refinanced: bool, negative: bool = False) -> str:
    """The scope statement every answer carries (8.12: which obligations are excluded), with
    the #169 label when the coverage is negative."""
    refinance = ("maturities assumed refinanced in the cash flow (refinance switch on)"
                 if principal_refinanced else "no new borrowing assumed (refinance switch off)")
    label = f"; negative coverage: {NEGATIVE_LABEL}" if negative else ""
    return (f"scoped scheduled debt-service coverage = {FORMULA} (BC27: the model's CFO is "
            f"after interest, so interest is added back){label}; principal gross of "
            f"refinancing, {refinance}; excludes " + "; ".join(EXCLUDED_OBLIGATIONS))


def withheld_reason() -> str:
    return f"{COMPREHENSIVE_DSCR_WITHHELD}: " + " ".join(WITHHELD_REASONS)


def same(a: float, b: float) -> bool:
    return abs(a - b) <= RECOMPUTE_TOLERANCE * max(1.0, abs(b))


def scoped_debt_service(issuer: str, scenario: str, fiscal_year: int,
                        opening_cash: float, lines: Mapping[str, float]) -> DebtService:
    """One projected year from its lines and the opening cash of that year."""
    parts = {name: lines.get(line) for name, line in COMPONENTS}
    present = [v for v in parts.values() if v is not None]
    cfo_less_capex = lines["cfo"] - lines["capex"]
    interest = sum(v for k in ("funded_interest", "finance_lease_interest")
                   if (v := parts[k]) is not None)
    numerator = cfo_less_capex + interest
    base = (issuer, scenario, fiscal_year, lines["cfo"], lines["capex"], cfo_less_capex,
            interest, numerator, parts["scheduled_principal"], parts["funded_interest"],
            parts["finance_lease_principal"], parts["finance_lease_interest"])
    if not present:
        return DebtService(*base, None, None, NOT_APPLIED,
                           "no funded debt or finance lease in the model")
    denominator = sum(present)
    model_interest = lines.get("interest", 0.0)
    second_numerator = lines["cash"] - opening_cash - lines["cff"] + model_interest
    second_denominator = model_interest + lines.get("principal", 0.0)
    if not same(numerator, second_numerator) or not same(denominator, second_denominator):
        return DebtService(*base, denominator, None, INVALID,
                           f"recomputation differs: numerator {numerator} vs "
                           f"{second_numerator}; debt service {denominator} vs "
                           f"{second_denominator}")
    if denominator <= 0:
        return DebtService(*base, denominator, None, NOT_MEANINGFUL,
                           "scheduled debt service is not positive in this year")
    detail = ("recomputed from the cash movement and the model's interest and principal "
              "lines")
    if numerator < 0:
        detail += f"; negative coverage: {NEGATIVE_LABEL}"
    return DebtService(*base, denominator, numerator / denominator, VERIFIED, detail)


def debt_service_table(forecast: Forecast) -> list[DebtService]:
    """Every projected year of a VERIFIED forecast (none for a forecast without years)."""
    if forecast.status != VERIFIED:
        return []
    opening = {o.line: o.value for o in forecast.opening}
    found, cash = [], opening["cash"]
    for year in forecast.years:
        found.append(scoped_debt_service(forecast.issuer, forecast.scenario, year.fiscal_year,
                                         cash, year.lines))
        cash = year.lines["cash"]
    return found
