"""Phase 4c scenario set (blueprint 8.11, Appendix B F5): one test per shock, Base = 4b,
compounding, NO_COGS mapping (#82), F5 recompute."""

from dataclasses import replace

import pytest
from model_support import AS_OF, make_set

from finagent.config import BASE_SCENARIO, ConfigError, load_scenarios
from finagent.model.checks import f5_headroom
from finagent.model.forecast import GAP, HEADROOM_SHORTFALL, VERIFIED, build_forecast

M = 1e6
G = (1000 / 800) ** (1 / 3) - 1  # synthetic base revenue growth
PARAMETERS = {"securities_haircut", "minimum_cash_floor"}
BALANCE = ("cash", "receivables", "inventory", "payables", "ppe", "debt", "retained_earnings")


def _spec(**shock):
    return replace(BASE_SCENARIO, name="Test", **shock)


def _drivers(forecast):
    return {a.driver_id: a for a in forecast.assumptions}


def _changed_drivers(base, shocked):
    b, s = _drivers(base), _drivers(shocked)
    return {k for k in s if k not in PARAMETERS and s[k].value != b[k].value}


def _pair(spec, statement_set=None):
    statement_set = statement_set or make_set()
    return build_forecast(statement_set, AS_OF), build_forecast(statement_set, AS_OF,
                                                                scenario=spec)


def test_configured_scenarios_match_the_blueprint_table():
    found = {s.name: s for s in load_scenarios()}
    assert list(found) == ["Base", "Moderate", "Severe"]
    moderate, severe = found["Moderate"], found["Severe"]
    assert (moderate.revenue_growth_shift, severe.revenue_growth_shift) == (-0.05, -0.12)
    assert (moderate.cogs_share_shift, severe.cogs_share_shift) == (0.015, 0.035)
    assert (moderate.dso_days, moderate.dio_days, moderate.dpo_days) == (5, 5, -3)
    assert (severe.dso_days, severe.dio_days, severe.dpo_days) == (12, 12, -8)
    assert (moderate.funded_debt_rate_shift, severe.funded_debt_rate_shift) == (0.02, 0.04)
    assert {s.capex_multiplier for s in found.values()} == {1.0}
    assert [s.securities_haircut for s in found.values()] == [0.0, 0.20, 0.50]
    assert {s.minimum_cash_floor for s in found.values()} == {0}
    assert found["Base"].is_neutral


def test_base_scenario_equals_the_4b_forecast_exactly():
    base = next(s for s in load_scenarios() if s.name == "Base")
    plain, scenario = _pair(base)
    assert scenario.status == plain.status == VERIFIED and scenario.scenario == "Base"
    assert [y.lines for y in scenario.years] == [y.lines for y in plain.years]
    assert scenario.checks == plain.checks
    assert _changed_drivers(plain, scenario) == set()


def test_revenue_shift_moves_growth_and_compounds():
    plain, shocked = _pair(_spec(revenue_growth_shift=-0.05))
    assert _changed_drivers(plain, shocked) == {"revenue_growth"}
    growth = _drivers(shocked)["revenue_growth"]
    assert growth.value == pytest.approx(G - 0.05) and growth.raw_value == pytest.approx(G)
    revenues = [y.lines["revenue"] for y in shocked.years]
    for n, revenue in enumerate(revenues, start=1):  # shifted rate each year: (1+g-0.05)^n
        assert revenue == pytest.approx(1000 * M * (1 + G - 0.05) ** n)
    assert revenues[2] != pytest.approx(1000 * M * (1 + G) ** 3 * (1 - 0.05))  # not a level cut
    assert all(c.status == "PASS" for c in shocked.checks)


def test_cogs_shift_moves_only_cogs():
    plain, shocked = _pair(_spec(cogs_share_shift=0.015))
    assert _changed_drivers(plain, shocked) == {"cogs_share"}
    y0, y1 = plain.years[0].lines, shocked.years[0].lines
    assert y1["revenue"] == y0["revenue"] and y1["other_cash_costs"] == y0["other_cash_costs"]
    assert y1["cogs"] - y0["cogs"] == pytest.approx(0.015 * y0["revenue"])


@pytest.mark.parametrize("field,line,base_share,basis", [
    ("dso_days", "receivables", 80 / 1000, "revenue"),
    ("dio_days", "inventory", 50 / 600, "cogs"),
    ("dpo_days", "payables", 70 / 600, "cogs"),
])
def test_days_shocks_move_only_their_balance(field, line, base_share, basis):
    days = -3 if field == "dpo_days" else 5
    plain, shocked = _pair(_spec(**{field: days}))
    driver = {"receivables": "receivables_share", "inventory": "inventory_share",
              "payables": "payables_share"}[line]
    assert _changed_drivers(plain, shocked) == {driver}
    y1 = shocked.years[0].lines
    assert y1[line] == pytest.approx(y1[basis] * (base_share + days / 365))
    others = [n for n in ("receivables", "inventory", "payables") if n != line]
    assert all(y1[n] == plain.years[0].lines[n] for n in others)
    assert all(c.status == "PASS" for c in shocked.checks)


def test_rate_shift_moves_funded_debt_interest_only():
    plain, shocked = _pair(_spec(funded_debt_rate_shift=0.02))
    assert _changed_drivers(plain, shocked) == {"interest_rate"}
    y0, y1 = plain.years[0].lines, shocked.years[0].lines
    assert y1["debt_interest"] - y0["debt_interest"] == pytest.approx(0.02 * 200 * M)
    assert y1["finance_lease_interest"] == y0["finance_lease_interest"]
    assert y1["ebit"] == y0["ebit"]


def test_capex_multiplier_moves_capex_only():
    plain, shocked = _pair(_spec(capex_multiplier=1.5))
    assert _changed_drivers(plain, shocked) == {"capex_share"}
    y0, y1 = plain.years[0].lines, shocked.years[0].lines
    assert y1["capex"] == pytest.approx(1.5 * y0["capex"]) and y1["ebit"] == y0["ebit"]


def test_haircut_and_floor_move_headroom_only():
    statement_set = make_set({(2025, "short_term_investments"): 40})
    plain, shocked = _pair(_spec(securities_haircut=0.5, minimum_cash_floor=10 * M),
                           statement_set)
    assert _changed_drivers(plain, shocked) == set()
    for p, s in zip(plain.years, shocked.years):
        assert {k: v for k, v in s.lines.items() if not k.startswith("liquidity_")} == \
            {k: v for k, v in p.lines.items() if not k.startswith("liquidity_")}
        assert p.lines["liquidity_headroom"] == pytest.approx(p.lines["cash"] + 40 * M)
        assert s.lines["liquidity_headroom"] == pytest.approx(
            s.lines["cash"] + 40 * M * 0.5 - 10 * M)
    params = _drivers(shocked)
    assert (params["securities_haircut"].value, params["minimum_cash_floor"].value) == \
        (0.5, 10 * M)


def test_shortfall_is_the_negative_headroom():
    spec = _spec(minimum_cash_floor=10_000 * M)
    f = build_forecast(make_set(), AS_OF, scenario=spec)
    assert f.status == VERIFIED and HEADROOM_SHORTFALL in f.flags
    for y in f.years:
        assert y.lines["liquidity_shortfall"] == pytest.approx(-y.lines["liquidity_headroom"])
        assert y.lines["liquidity_shortfall"] > 0


def _no_cogs_set():
    return make_set({(2025, "operating_income"): 150},
                    {(2025, "cost_of_revenue"): "NOT_REPORTED",
                     (2025, "operating_expense_after_cost_of_sales"): "NOT_REPORTED"})


def test_no_cogs_mapping_of_cost_and_days_shocks():
    statement_set = _no_cogs_set()
    plain, shocked = _pair(_spec(cogs_share_shift=0.015, dio_days=5, dpo_days=-3),
                           statement_set)
    assert shocked.status == VERIFIED
    assert _changed_drivers(plain, shocked) == {"cash_operating_cost_share", "inventory_share",
                                                "payables_share"}
    y0, y1 = plain.years[0].lines, shocked.years[0].lines
    assert y1["cash_operating_costs"] - y0["cash_operating_costs"] == pytest.approx(
        0.015 * y0["revenue"])
    assert y1["inventory"] == pytest.approx(y1["revenue"] * (50 / 1000 + 5 / 365))
    assert y1["payables"] == pytest.approx(y1["revenue"] * (70 / 1000 - 3 / 365))
    assert "NO_COGS" in _drivers(shocked)["cash_operating_cost_share"].detail


def test_not_applied_driver_is_not_shocked():
    f = build_forecast(make_set(gaps={(2025, "inventory"): "NOT_REPORTED"}), AS_OF,
                       scenario=_spec(dio_days=12))
    assert f.status == VERIFIED and _drivers(f)["inventory_share"].value is None
    assert all("inventory" not in y.lines for y in f.years)


def test_share_below_zero_after_a_shock_is_a_gap_never_floored():
    f = build_forecast(make_set(), AS_OF, scenario=_spec(dpo_days=-100))
    assert f.status == GAP and f.years == ()
    assert any("payables_share" in r and "below zero" in r for r in f.reasons)


def test_f5_recomputes_headroom_and_catches_an_injected_error():
    lines = {"cash": -50.0, "short_term_investments": 100.0, "liquidity_headroom": -10.0,
             "liquidity_shortfall": 10.0}
    assert f5_headroom(lines, 2026, 0.6, 0.0, 0.01).status == "PASS"  # -50 + 40
    check = f5_headroom(dict(lines, liquidity_headroom=-9.0), 2026, 0.6, 0.0, 0.01)
    assert check.status == "FAIL" and check.residual == pytest.approx(1.0)
    check = f5_headroom(dict(lines, liquidity_shortfall=0.0), 2026, 0.6, 0.0, 0.01)
    assert check.status == "FAIL"


def test_injected_headroom_error_makes_the_scenario_invalid(monkeypatch):
    from finagent.model import forecast as module
    original = module.project

    def broken(*args, **kwargs):
        years = original(*args, **kwargs)
        lines = dict(years[1].lines, liquidity_headroom=years[1].lines["liquidity_headroom"] + 1)
        return [years[0], replace(years[1], lines=lines)] + list(years[2:])

    monkeypatch.setattr(module, "project", broken)
    f = build_forecast(make_set(), AS_OF, scenario=_spec(securities_haircut=0.2))
    assert f.status == "INVALID" and f.years == ()
    assert any(r.startswith("F5 FY2027") for r in f.reasons)


def test_scenario_config_validation(tmp_path):
    path = tmp_path / "s.yaml"
    fields = ("revenue_growth_shift: 0\ncogs_share_shift: 0\ndso_days: 0\ndio_days: 0\n"
              "dpo_days: 0\nfunded_debt_rate_shift: 0\ncapex_multiplier: 1\n"
              "minimum_cash_floor: 0\n")
    indent = lambda text: "".join(f"    {line}\n" for line in text.splitlines())  # noqa: E731
    path.write_text("scenarios:\n  Base:\n" + indent(fields + "securities_haircut: 0"))
    assert [s.name for s in load_scenarios(path)] == ["Base"]
    path.write_text("scenarios:\n  Base:\n" + indent(fields + "securities_haircut: 1.5"))
    with pytest.raises(ConfigError):
        load_scenarios(path)
    path.write_text("scenarios:\n  Base:\n" + indent(fields.replace("dso_days: 0", "dso_days: 5")
                                                    + "securities_haircut: 0"))
    with pytest.raises(ConfigError):
        load_scenarios(path)
