"""Scenario shocks on the 4b drivers (blueprint 8.11; Phase 4c).

shock_drivers applies one scenario (configs/scenarios.yaml) to the drivers of the integrated
forecast, once. Each shocked driver stays a MODEL_ASSUMPTION record: the shocked value, the base
value kept as raw_value, the scenario in its basis and flags. Mapping:

- revenue growth: + shift, every projected year (the growth rate moves, so the effect compounds)
- COGS share: + shift; for a NO_COGS filer (BC17) the total cash operating cost share (#82)
- receivables share (on revenue): + DSO days / days in year
- inventory and payables shares (on COGS, or on revenue for NO_COGS): + DIO / DPO days / days in
  year
- interest rate on funded debt: + shift (finance leases keep their own, unshocked rate record)
- capex share: x multiplier
- securities haircut and minimum cash floor: two records used by liquidity headroom (F5)

A driver that does not apply (NOT_APPLIED: e.g. no inventory) is not shocked. A share or rate
that the shock takes below zero is a problem, never floored: the scenario is then a GAP.
"""

from collections.abc import Callable, Mapping
from dataclasses import replace

from finagent.config import ScenarioSpec
from finagent.model import drivers as d
from finagent.records import ModelAssumption

SCENARIO_FLAG = "SCENARIO"


def _shift(a: ModelAssumption, spec: ScenarioSpec, change: Callable[[float], float],
           describe: str) -> ModelAssumption:
    assert a.value is not None
    return replace(a, value=change(a.value), raw_value=a.value,
                   basis=f"{a.basis};{spec.name}",
                   flags=a.flags + (f"{SCENARIO_FLAG}:{spec.name}",),
                   detail=f"{a.detail}; {spec.name}: base {a.value:.6f} {describe}")


def shock_plan(spec: ScenarioSpec, no_cogs: bool) -> dict[str, tuple[Callable[[float], float],
                                                                     str]]:
    """{driver_id: (change, description)} for every non-zero shock of the scenario."""
    days = spec.days_in_year
    plan: dict[str, tuple[Callable[[float], float], str]] = {}

    def add(driver_id: str, amount: float, text: str, multiply: bool = False) -> None:
        if (amount != 1.0) if multiply else (amount != 0):
            plan[driver_id] = ((lambda v: v * amount) if multiply else (lambda v: v + amount),
                               text)

    add("revenue_growth", spec.revenue_growth_shift,
        f"{spec.revenue_growth_shift:+.4f} growth shift (each year; compounds)")
    add("cash_operating_cost_share" if no_cogs else "cogs_share", spec.cogs_share_shift,
        f"{spec.cogs_share_shift:+.4f} cost share shift" + (" (NO_COGS, #82)" if no_cogs else ""))
    add("receivables_share", spec.dso_days / days, f"{spec.dso_days:+g} DSO days / {days}")
    add("inventory_share", spec.dio_days / days, f"{spec.dio_days:+g} DIO days / {days}")
    add("payables_share", spec.dpo_days / days, f"{spec.dpo_days:+g} DPO days / {days}")
    add("interest_rate", spec.funded_debt_rate_shift,
        f"{spec.funded_debt_rate_shift:+.4f} funded debt rate shift")
    add("capex_share", spec.capex_multiplier, f"x {spec.capex_multiplier:g} capex multiplier",
        multiply=True)
    return plan


def scenario_parameters(spec: ScenarioSpec) -> tuple[ModelAssumption, ModelAssumption]:
    return (ModelAssumption("securities_haircut", spec.securities_haircut, spec.name, d.SET,
                            detail="haircut on short-term investments in liquidity headroom"),
            ModelAssumption("minimum_cash_floor", spec.minimum_cash_floor, spec.name, d.SET,
                            detail="minimum cash deducted in liquidity headroom (dollars)"))


def shock_drivers(assumptions: Mapping[str, ModelAssumption], spec: ScenarioSpec,
                  no_cogs: bool) -> tuple[dict[str, ModelAssumption], list[str]]:
    """The scenario's drivers and any problems (a share or rate below zero after the shock)."""
    shocked = dict(assumptions)
    problems: list[str] = []
    for driver_id, (change, text) in shock_plan(spec, no_cogs).items():
        a = assumptions.get(driver_id)
        if a is None or a.status in (d.NOT_APPLIED, d.GAP) or a.value is None:
            continue
        shocked[driver_id] = _shift(a, spec, change, text)
        if driver_id != "revenue_growth" and shocked[driver_id].value < 0:
            problems.append(f"{driver_id} {shocked[driver_id].value:.6f} below zero after the "
                            f"{spec.name} shock (never floored)")
    for parameter in scenario_parameters(spec):
        shocked[parameter.driver_id] = parameter
    return shocked, problems
