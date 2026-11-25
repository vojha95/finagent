"""Liquidity under scenarios (blueprint 8.12; #154, D1 pending Vineet).

For each scenario and projected year, both headroom measures side by side, whatever the
configured basis (configs/forecast.yaml switches.headroom_basis):
- cash headroom = projected cash - minimum cash floor (8.12, F5); shortfall = max(0, -headroom);
- the conditional securities reserve = short-term investments x (1 - haircut), shown separately
  (8.12: never counted as cash), and the headroom including it = cash + reserve - floor (4c).
A filer with no short-term investments has no reserve (absent, never 0): its headroom including
the reserve is the cash headroom. When the investments are reported but not accepted (left in
other assets; the forecast then has no liquidity line), the reserve and the headroom including
it are not computable (None).

The forecast's own liquidity_headroom and liquidity_shortfall lines (the configured basis) are
checked against the matching measure here: a difference above the dollar tolerance is INVALID.
A shortfall is labelled MODELLED CASH SHORTFALL and is conditional on the scenario (8.12).
The scheduled long-term principal, funded interest and finance-lease principal and interest
of the year are carried alongside (absent when the filer has no debt or finance lease).
"""

from collections.abc import Mapping
from dataclasses import dataclass

from finagent.config import CASH_ONLY, CASH_PLUS_SECURITIES, ScenarioSpec
from finagent.records import Forecast, ProjectedYear

VERIFIED, GAP, INVALID = "VERIFIED", "GAP", "INVALID"
MODELLED_CASH_SHORTFALL = "MODELLED CASH SHORTFALL"
NO_MODELLED_SHORTFALL = "NO MODELLED SHORTFALL"
NOT_COMPUTED = "NOT COMPUTED"


@dataclass(frozen=True)
class LiquidityRow:
    """One scenario and projected year: both headroom measures (#154) and the obligations."""

    issuer: str
    scenario: str
    fiscal_year: int
    cash: float
    short_term_investments: float | None  # None: the filer has none, or they are not accepted
    securities_reserve: float | None  # investments x (1 - haircut); never counted as cash
    minimum_cash_floor: float
    securities_haircut: float
    cash_headroom: float  # cash - floor (8.12)
    cash_shortfall: float
    headroom_with_reserve: float | None  # cash + reserve - floor (4c); None: not computable
    shortfall_with_reserve: float | None
    label: str  # on the cash measure (8.12)
    label_with_reserve: str
    scheduled_principal: float | None
    funded_interest: float | None
    finance_lease_principal: float | None
    finance_lease_interest: float | None
    basis: str  # the configured basis of the forecast's own headroom lines
    status: str  # VERIFIED: the forecast's lines equal their measure; INVALID; GAP: not computed
    detail: str = ""


def shortfall_label(shortfall: float | None) -> str:
    if shortfall is None:
        return NOT_COMPUTED
    return MODELLED_CASH_SHORTFALL if shortfall > 0 else NO_MODELLED_SHORTFALL


def selected_check(lines: Mapping[str, float], basis: str, cash_headroom: float,
                   headroom_with_reserve: float | None,
                   tolerance: float) -> tuple[str, str]:
    """(status, detail): the forecast's configured-basis lines against the measure here."""
    expected = cash_headroom if basis == CASH_ONLY else headroom_with_reserve
    if "liquidity_headroom" not in lines:
        if expected is None:
            return GAP, f"{basis} headroom not computable (short-term investments not accepted)"
        return INVALID, f"the forecast has no headroom line on the {basis} basis"
    if expected is None:
        return INVALID, f"the forecast has a {basis} headroom the inputs here cannot give"
    residual = max(abs(lines["liquidity_headroom"] - expected),
                   abs(lines["liquidity_shortfall"] - max(0.0, -expected)))
    if residual > tolerance:
        return INVALID, f"forecast {basis} headroom differs by {residual:,.2f}"
    return VERIFIED, f"forecast {basis} headroom and shortfall recomputed"


def liquidity_row(issuer: str, scenario: ScenarioSpec, year: ProjectedYear, basis: str,
                  tolerance: float) -> LiquidityRow:
    lines = year.lines
    floor, haircut = scenario.minimum_cash_floor, scenario.securities_haircut
    cash_headroom = lines["cash"] - floor
    known = "liquidity" in lines  # the forecast computes liquidity only on accepted inputs
    investments = lines.get("short_term_investments") if known else None
    reserve = investments * (1 - haircut) if investments is not None else None
    with_reserve = (cash_headroom + (reserve or 0.0)) if known else None
    status, detail = selected_check(lines, basis, cash_headroom, with_reserve, tolerance)
    shortfall_with = max(0.0, -with_reserve) if with_reserve is not None else None
    return LiquidityRow(
        issuer, scenario.name, year.fiscal_year, lines["cash"], investments, reserve, floor,
        haircut, cash_headroom, max(0.0, -cash_headroom), with_reserve, shortfall_with,
        shortfall_label(max(0.0, -cash_headroom)), shortfall_label(shortfall_with),
        lines.get("debt_principal"), lines.get("debt_interest"),
        lines.get("finance_lease_principal"), lines.get("finance_lease_interest"),
        basis, status, detail)


def liquidity_table(forecast: Forecast, scenario: ScenarioSpec,
                    tolerance: float) -> list[LiquidityRow]:
    """Every projected year of a VERIFIED forecast (none for a forecast without years)."""
    if forecast.scenario != scenario.name:
        raise ValueError(f"forecast scenario {forecast.scenario!r} is not {scenario.name!r}")
    basis = str(forecast.switches.get("headroom_basis", CASH_PLUS_SECURITIES))
    if forecast.status != VERIFIED:
        return []
    return [liquidity_row(forecast.issuer, scenario, y, basis, tolerance)
            for y in forecast.years]
