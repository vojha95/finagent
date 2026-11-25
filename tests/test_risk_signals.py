"""Risk signals (finagent.risk.signals; 8.13, Appendix C.2; D2-D5)."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from model_support import make_set

from finagent.config import (ConfigError, ConditionSpec, load_planner_config,
                             load_signal_config)
from finagent.model.ratios import BY_ID, RatioValue
from finagent.risk.signals import (FIRED, GAP, NOT_APPLIED, NOT_FIRED, SignalContext, Sources,
                                   all_state, any_state, condition, evaluate, evaluate_set,
                                   float_sources, history, ratio_specs, read_input, signal,
                                   year_data)

CONFIG = load_signal_config()
NAMES = {"R1": "Receivables outpacing sales"}


def sources(ratios=None, inputs=None, absent=(), forecast=None, origin=None) -> Sources:
    ratios, inputs = ratios or {}, inputs or {}
    return Sources(lambda y, r: ratios.get((y, r)), lambda y, k: inputs.get((y, k)),
                   lambda y, k: (y, k) in set(absent), ratio_specs(CONFIG), forecast, origin)


def ok(rid, value):
    return RatioValue(rid, value, "OK")


def cond(measure, of, op=">", threshold=0.0, unit="x", years=1):
    of = (of,) if isinstance(of, str) else tuple(of)
    return ConditionSpec("c", measure, of, op, threshold, unit, "label", years)


SPEC = CONFIG.signals["R1"]


def test_change_level_and_percent_points():
    s = sources({(2024, "dso"): ok("dso", 40.0), (2025, "dso"): ok("dso", 47.0),
                 (2024, "ebit_margin"): ok("ebit_margin", 0.20),
                 (2025, "ebit_margin"): ok("ebit_margin", 0.16)})
    r = condition(cond("change", "dso", ">", 5, "days"), SPEC, 2025, s)
    assert (r.state, r.value, r.values) == (FIRED, 7.0, ((2024, 40.0), (2025, 47.0)))
    m = condition(cond("change", "ebit_margin", "<", -3, "pp"), SPEC, 2025, s)
    assert m.state == FIRED and abs(m.value + 4.0) < 1e-9  # 20% -> 16%: -4 pp
    assert condition(cond("level", "dso", ">", 50, "days"), SPEC, 2025, s).state == NOT_FIRED
    gap = condition(cond("change", "dso"), SPEC, 2024, s)  # FY2023 not in the sources
    assert (gap.state, gap.reason, gap.value) == (GAP, "NO_YEAR", None)


def test_ratio_without_a_value_is_never_not_fired():
    s = sources({(2025, "dso"): RatioValue("dso", None, "GAP", "inputs without a value"),
                 (2025, "dio"): RatioValue("dio", None, "NOT_APPLIED", "inventory is not "
                                           "reported"),
                 (2025, "current_ratio"): RatioValue("current_ratio", None, "NM", "n.m.")})
    assert condition(cond("level", "dso"), SPEC, 2025, s).reason == "INPUT_GAP"
    assert condition(cond("level", "dio"), SPEC, 2025, s).state == NOT_APPLIED
    assert condition(cond("level", "current_ratio"), SPEC, 2025, s).reason == "NOT_MEANINGFUL"


def test_growth_gap_and_growth():
    s = sources(inputs={(2024, "receivables"): 60.0, (2025, "receivables"): 80.0,
                        (2024, "revenue"): 900.0, (2025, "revenue"): 1000.0,
                        (2024, "inventory"): 0.0, (2025, "inventory"): 5.0},
                absent=[(2025, "payables")])
    r = condition(cond("growth_gap", ("receivables", "revenue"), ">", 10, "pp"), SPEC, 2025, s)
    assert r.state == FIRED and abs(r.value - (100 / 3 - 100 / 9)) < 1e-9
    assert "receivables +33.3%" in r.detail
    g = condition(cond("growth", "revenue", ">", 0, "pp"), SPEC, 2025, s)
    assert g.state == FIRED and abs(g.value - 100 / 9) < 1e-9
    assert condition(cond("growth", "inventory"), SPEC, 2025, s).reason == "NOT_MEANINGFUL"
    assert condition(cond("growth", "payables"), SPEC, 2025, s).state == NOT_APPLIED
    assert condition(cond("growth", "cogs"), SPEC, 2025, s).reason == "INPUT_GAP"


def test_vs_median_and_level_years():
    r = {(y, "capex_to_revenue"): ok("capex_to_revenue", v)
         for y, v in ((2022, 0.04), (2023, 0.05), (2024, 0.06), (2025, 0.09))}
    s = sources(r)
    m = condition(cond("vs_median", "capex_to_revenue", ">", 1.5, "x", 3), SPEC, 2025, s)
    assert m.state == FIRED and abs(m.value - 0.09 / 0.05) < 1e-12
    assert condition(cond("vs_median", "capex_to_revenue", ">", 1.5, "x", 4), SPEC, 2025,
                     s).reason == "NO_YEAR"
    c = {(2024, "cfo_to_net_income"): ok("cfo_to_net_income", 0.7),
         (2025, "cfo_to_net_income"): ok("cfo_to_net_income", 0.6)}
    two = cond("level_years", "cfo_to_net_income", "<", 0.8, "x", 2)
    assert condition(two, SPEC, 2025, sources(c)).state == FIRED
    c[(2024, "cfo_to_net_income")] = ok("cfo_to_net_income", 0.9)
    assert condition(two, SPEC, 2025, sources(c)).state == NOT_FIRED
    del c[(2024, "cfo_to_net_income")]  # one year unknown, the other passes the test: GAP
    assert condition(two, SPEC, 2025, sources(c)).state == GAP
    c[(2025, "cfo_to_net_income")] = ok("cfo_to_net_income", 1.2)  # one year fails: settled
    assert condition(two, SPEC, 2025, sources(c)).state == NOT_FIRED


def test_scenario_max_runs_on_the_origin_year_only():
    year = lambda y, v: SimpleNamespace(fiscal_year=y, lines={"liquidity_shortfall": v})  # noqa
    f = SimpleNamespace(status="VERIFIED", reasons=(), years=[year(2026, 0.0), year(2027, 5e9),
                                                             year(2028, 2e9)])
    spec = CONFIG.signals["R10"]
    c = spec.conditions[0]
    r = condition(c, spec, 2025, sources(forecast=lambda name: f, origin=2025))
    assert (r.state, r.value) == (FIRED, 5e9) and "largest in FY2027" in r.detail
    assert condition(c, spec, 2024, sources(forecast=lambda name: f, origin=2025)).state == \
        NOT_APPLIED
    bad = SimpleNamespace(status="GAP", reasons=("no schedule",), years=[])
    assert condition(c, spec, 2025, sources(forecast=lambda n: bad, origin=2025)).reason == \
        "FORECAST_GAP"


def test_any_and_all_never_read_a_gap_as_not_fired():
    assert any_state([FIRED, GAP]) == FIRED and any_state([NOT_FIRED, GAP]) == GAP
    assert any_state([NOT_FIRED, NOT_APPLIED]) == NOT_FIRED
    assert any_state([NOT_APPLIED, NOT_APPLIED]) == NOT_APPLIED
    assert all_state([FIRED, FIRED]) == FIRED and all_state([FIRED, GAP]) == GAP
    assert all_state([NOT_FIRED, GAP]) == NOT_FIRED and all_state([FIRED, NOT_APPLIED]) == \
        NOT_APPLIED


def test_requires_gives_no_cogs_and_composite_combines_components():  # D4, R11
    ctx = SignalContext("SYNT", NAMES, lambda y, key: key == "cogs")
    r2 = signal(CONFIG.signals["R2"], 2025, sources(), ctx)
    assert (r2.state, r2.reason) == (GAP, "NO_COGS") and r2.conditions == ()
    other = signal(CONFIG.signals["R2"], 2025, sources(), SignalContext("SYNT", NAMES))
    assert (other.state, other.reason) == (GAP, "INPUT_GAP")  # cogs reported, not accepted
    base = dict(name="n", issuer="SYNT", fiscal_year=2025, severity="medium", data="history")
    from finagent.records import SignalResult

    done = {k: SignalResult(k, state=s, **base) for k, s in
            (("R1", FIRED), ("R2", GAP), ("R3", FIRED), ("R4", FIRED))}
    growing = sources(inputs={(2024, "revenue"): 900.0, (2025, "revenue"): 1000.0})
    ctx = SignalContext("SYNT", NAMES, done=done)
    r11 = signal(CONFIG.signals["R11"], 2025, growing, ctx)
    assert r11.state == FIRED and dict(r11.components)["R2"] == GAP
    done["R4"] = replace(done["R4"], state=NOT_FIRED)
    assert signal(CONFIG.signals["R11"], 2025, growing, ctx).state == NOT_FIRED
    done["R4"] = replace(done["R4"], state=GAP, reason="INPUT_GAP", detail="d")
    stuck = signal(CONFIG.signals["R11"], 2025, growing, ctx)
    assert (stuck.state, stuck.reason) == (GAP, "INPUT_GAP")


def test_signals_on_a_statement_set():
    s = make_set({(2024, "accounts_receivable"): 60})
    data = history(s, CONFIG)
    assert set(data) == {2021, 2022, 2023, 2024, 2025}
    assert data[2025].values["receivables"] == 8.0e7 and "da" not in data[2025].values
    results = {r.signal_id: r for r in evaluate_set(s, 2025, CONFIG, NAMES)}
    r1 = results["R1"]
    assert r1.state == FIRED and r1.name == NAMES["R1"] and r1.severity == "medium"
    fired = [c for c in r1.conditions if c.state == FIRED]
    assert [c.condition_id for c in fired] == ["receivables_growth_gap"]
    assert results["R10"].state == NOT_APPLIED  # no forecast given
    assert results["R7"].severity == "high" and results["R11"].severity == "high"
    early = evaluate_set(s, 2021, CONFIG, NAMES)  # no previous year: never "not fired"
    assert all(r.state != NOT_FIRED for r in early if r.signal_id in ("R1", "R3", "R8"))
    no_cogs = make_set(gaps={(2025, "cost_of_revenue"): "NOT_REPORTED"})
    by_id = {r.signal_id: r for r in evaluate_set(no_cogs, 2025, CONFIG, NAMES)}
    assert (by_id["R2"].reason, by_id["R9"].reason) == ("NO_COGS", "NO_COGS")
    # the float sources read the same year data
    src = float_sources(data, CONFIG)
    assert src.ratio(2025, "current_ratio") == data[2025].ratios["current_ratio"]
    assert year_data(s, 2025, CONFIG).ratios["funded_debt_to_ebitda"].status == "GAP"  # D2
    assert evaluate(2025, CONFIG, src, SignalContext("SYNT", NAMES))[0].signal_id == "R1"


def test_da_fallback_is_depreciation_plus_amortisation_when_both_are_read():
    """#179 (Vineet): no D&A line -> depreciation + amortisation of intangibles when both have
    an accepted value (noted on the condition); one of them alone -> no value (GAP)."""
    spec = CONFIG.inputs["da"]
    assert (spec.metric, spec.fallback) == ("depreciation_and_amortisation",
                                           ("depreciation", "amortisation_of_intangibles"))
    only_depreciation = make_set()  # depreciation 40m, no D&A, no amortisation
    assert read_input(only_depreciation, 2025, spec).value is None
    assert year_data(only_depreciation, 2025, CONFIG).ratios[
        "funded_debt_to_ebitda"].status == "GAP"
    both = make_set({(2025, "amortisation_of_intangibles"): 10, (2024, "depreciation"): 38,
                     (2024, "amortisation_of_intangibles"): 9, (2025, "operating_income"): 150,
                     (2024, "operating_income"): 140})
    found = read_input(both, 2025, spec)
    assert found.value == 50 * 10 ** 6
    assert found.note == "depreciation_and_amortisation = depreciation + " \
                         "amortisation_of_intangibles"
    data = history(both, CONFIG)
    assert data[2025].values["da"] == 5.0e7 and data[2025].notes == {"da": found.note}
    r5 = CONFIG.signals["R5"]
    level = condition(r5.conditions[0], r5, 2025, float_sources(data, CONFIG))
    assert level.state in (FIRED, NOT_FIRED) and level.value == pytest.approx(200 / (150 + 50))
    assert level.detail == f"{found.note} in FY2025 (#179)"
    change = condition(r5.conditions[1], r5, 2025, float_sources(data, CONFIG))
    assert change.detail == f"{found.note} in FY2024, FY2025 (#179)"
    reported = make_set({(2025, "depreciation_and_amortisation"): 55,
                         (2025, "amortisation_of_intangibles"): 10})
    assert read_input(reported, 2025, spec).value == 55 * 10 ** 6  # the D&A line itself
    assert read_input(reported, 2025, spec).note == ""


def test_signal_ratios_take_the_ratio_form():
    specs = ratio_specs(CONFIG)
    assert specs["current_ratio"] is BY_ID["current_ratio"]
    lev = specs["funded_debt_to_ebitda"]
    assert [k for _, k, _ in lev.numerator] == ["funded_debt"]
    assert [k for _, k, _ in lev.denominator] == ["ebit", "da"]  # D2: EBIT + D&A


def test_planner_risk_metric_list_matches_the_signals():  # #155
    answers = load_planner_config().answers["risk"]
    assert answers.metrics == ("signals_fired",) + CONFIG.metric_ids()
    assert answers.required == ("signals_fired",)
    for s in CONFIG.signals.values():
        for c in s.conditions + ((s.while_,) if s.while_ else ()):
            unit, classes = answers.expected(f"{s.signal_id}.{c.condition_id}")
            assert unit == c.unit
            assert classes == (("CALCULATION_FROM_MODEL_ASSUMPTION",) if s.data == "scenario"
                               else ("CALCULATION",))


def test_signal_config_validation(tmp_path):
    source = (Path(__file__).parents[1] / "configs" / "signals.yaml").read_text("utf-8")
    path = tmp_path / "s.yaml"
    for old, new in (("severity: high\n    fires: all", "severity: urgent\n    fires: all"),
                     ("any_of: [R1, R2]", "any_of: [R1, R99]"),
                     ("min_score: 0.30", "min_score: 1.5"),
                     ("op: \"<\", threshold: 0.8", "op: \"<=\", threshold: 0.8")):
        assert old in source
        path.write_text(source.replace(old, new), encoding="utf-8")
        with pytest.raises(ConfigError):
            load_signal_config(path)
    assert CONFIG.signals["R10"].scenario == "Moderate" and CONFIG.evidence.max_words == 60


def test_evidence_versions_share_the_rules_and_differ_in_retrieval(tmp_path):  # D-EV
    source = (Path(__file__).parents[1] / "configs" / "signals.yaml").read_text("utf-8")
    v1, v2 = CONFIG.evidence_versions["v1"], CONFIG.evidence_versions["v2"]
    assert CONFIG.evidence == v2 and CONFIG.with_evidence("v1").evidence == v1
    assert (v1.variant, v1.order, v1.min_rerank_score) == ("bm25", "score", None)
    assert (v2.variant, v2.order) == ("hybrid_rrf_rerank", "rank") and v2.min_rerank_score
    # D-EV2: only v2 (the reranker gate) shows a low-confidence sentence on a gap card.
    assert v2.low_confidence_label == "Low confidence: may not explain the movement"
    assert v1.low_confidence_label is None
    shared = ("sections", "explain_sections", "hits", "max_quotes", "min_words", "max_words",
              "min_score", "model_derived_label")
    assert all(getattr(v1, k) == getattr(v2, k) for k in shared)
    with pytest.raises(ConfigError):
        CONFIG.with_evidence("v9")
    path = tmp_path / "s.yaml"
    for old, new in (("evidence_version: v2", "evidence_version: v9"),
                     ("v1: {variant: bm25, order: score}",
                      "v1: {variant: bm25, order: score, min_rerank_score: 1}"),
                     ("v1: {variant: bm25, order: score}",  # D-EV2: a label needs the gate
                      'v1: {variant: bm25, order: score, low_confidence_label: "x"}'),
                     ("v1: {variant: bm25, order: score}", "v1: {variant: magic}"),
                     ("v1: {variant: bm25, order: score}", "v1: {order: sideways}")):
        assert old in source
        path.write_text(source.replace(old, new), encoding="utf-8")
        with pytest.raises(ConfigError):
            load_signal_config(path)
    start = source.index("  evidence_version: v2")
    end = source.index("\n", source.index("    v2: {"))
    path.write_text(source[:start] + source[end + 1:], encoding="utf-8")  # no versions: v1
    legacy = load_signal_config(path).evidence
    assert (legacy.version, legacy.variant, legacy.min_rerank_score) == ("v1", None, None)
    assert legacy.low_confidence_label is None
