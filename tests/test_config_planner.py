"""Planner configs: universe names, the intent vocabulary and planner.yaml (Phase 6)."""

from pathlib import Path

import pytest
import yaml
from model_support import AS_OF, make_set

from finagent.config import (GOAL_KINDS, INTENTS_PATH, PLANNER_PATH, UNIVERSE_PATH, ConfigError,
                             load_derivations, load_intent_vocabulary, load_issuer_names,
                             load_planner_config, load_universe)
from finagent.model.forecast import build_forecast
from finagent.model.ratios import BY_ID
from finagent.tools import RATIO_UNITS

# Forecast metrics with no projected line of their own: derived from projected lines, as the
# workbook does (finagent/workbook/model.py "Free cash flow (CFO - capex)").
DERIVED_FORECAST = {"free_cash_flow": ("cfo", "capex")}


def _write(tmp_path: Path, source: Path, change) -> Path:
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    change(data)
    path = tmp_path / source.name
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_universe_accepts_name_keys_and_every_ticker_has_a_name():
    universe = load_universe()
    names = load_issuer_names()
    assert set(names.names) == set(universe)
    assert names.capitalised <= {n for listed in names.names.values() for n in listed}


def test_a_name_may_not_belong_to_two_tickers(tmp_path):
    def clash(data):
        data["names"]["MSFT"] = data["names"]["MSFT"] + [data["names"]["AMZN"][0]]

    with pytest.raises(ConfigError):
        load_issuer_names(_write(tmp_path, UNIVERSE_PATH, clash))


def test_a_ticker_without_a_name_is_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load_issuer_names(_write(tmp_path, UNIVERSE_PATH, lambda d: d["names"].pop("AMZN")))


def test_vocabulary_loads_and_checks():
    v = load_intent_vocabulary()
    assert set(v.targets) == {"+1", "+2", "+3"} and set(v.scenarios) == {"Base", "Moderate",
                                                                         "Severe"}
    assert v.metrics["explain"] is v.metrics["fact"]  # alias
    assert v.clarification("ADVICE")[0] == "advice"
    with pytest.raises(ConfigError):
        v.clarification("NOPE")


@pytest.mark.parametrize("change", [
    lambda d: d["targets"].pop("+3"),
    lambda d: d["scenarios"].pop("Severe"),
    lambda d: d["metrics"]["fact"].update(weather=["weather"]),
    lambda d: d["metrics"]["fact"].update(revenue=["revenue", "net income"]),
    lambda d: d["clarifications"]["ADVICE"].update(category="other"),
    lambda d: d["metrics"].update(horoscope={"x": ["x"]}),
    lambda d: d.update(advice_patterns=["(unclosed"]),
])
def test_vocabulary_rejects(tmp_path, change):
    with pytest.raises(ConfigError):
        load_intent_vocabulary(_write(tmp_path, INTENTS_PATH, change))


def test_planner_config():
    p = load_planner_config()
    assert tuple(p.kinds) == GOAL_KINDS and p.default_scenario == "Base"
    assert p.kinds["debt"].conditional and p.kinds["forecast"].target
    assert 0 < p.confidence_threshold <= 1


@pytest.mark.parametrize("change", [
    lambda d: d["goal_kinds"].pop("risk"),
    lambda d: d.update(default_scenario="Apocalypse"),
    lambda d: d["compiler"].update(confidence_threshold=1.5),
    lambda d: d["goal_kinds"]["statements"].update(defaults=["revenue"]),
])
def test_planner_config_rejects(tmp_path, change):
    with pytest.raises(ConfigError):
        load_planner_config(_write(tmp_path, PLANNER_PATH, change))


def test_planner_defaults_are_metrics_of_their_kind():
    v, p = load_intent_vocabulary(), load_planner_config()
    for kind, spec in p.kinds.items():
        assert set(spec.defaults) <= set(v.metrics.get(kind, {})), kind


def test_credit_metrics_are_ratios_of_the_model():
    assert set(load_intent_vocabulary().metrics["credit"]) <= set(BY_ID)


def test_planner_actions_budget_and_answers():  # 8.15, BC25, Phase 6.3
    p = load_planner_config()
    assert [a.priority for a in p.actions] == sorted(a.priority for a in p.actions)
    assert {a.kind for a in p.actions if a.trigger == "start"} == set(GOAL_KINDS)
    doc_only = next(a for a in p.actions if a.name == "reported_document_only")
    assert (doc_only.kind, doc_only.trigger) == ("fact", "TAG_MISSING")
    assert (p.step_budget.default, p.step_budget.min, p.step_budget.max) == (12, 0, 30)
    assert set(p.answers) == set(GOAL_KINDS)
    assert set(p.answers["statements"].required) <= set(p.answers["statements"].metrics)


def test_fact_answers_allow_calculation_exactly_for_derived_metrics():  # #118
    targets = {r.derivation_id for r in load_derivations().rules}
    fact = load_planner_config().answers["fact"]
    for metric in load_intent_vocabulary().metrics["fact"]:
        assert ("CALCULATION" in fact.expected(metric)[1]) == (metric in targets), metric


def test_credit_answer_units_follow_the_ratio_format():  # #118
    credit = load_planner_config().answers["credit"]
    for metric in load_intent_vocabulary().metrics["credit"]:
        assert credit.expected(metric)[0] == RATIO_UNITS[BY_ID[metric].fmt], metric


@pytest.mark.parametrize("change", [
    lambda d: d["step_budget"].update(max=31),
    lambda d: d["step_budget"].update(default=-1),
    lambda d: d["answers"].pop("risk"),
    lambda d: d["actions"].append(dict(d["actions"][0])),  # duplicate name
    lambda d: d["actions"].append({"name": "x", "kind": "fact", "trigger": "start",
                                   "prerequisite": "weather"}),
    lambda d: d.update(actions=[a for a in d["actions"] if a["kind"] != "credit"]),
])
def test_planner_actions_and_answers_rejects(tmp_path, change):
    with pytest.raises(ConfigError):
        load_planner_config(_write(tmp_path, PLANNER_PATH, change))


def test_forecast_and_liquidity_metrics_are_projected_lines():
    lines = set(build_forecast(make_set(), AS_OF).years[0].lines)
    v = load_intent_vocabulary()
    for metric in v.metrics["forecast"]:
        assert metric in lines or set(DERIVED_FORECAST.get(metric, ("?",))) <= lines, metric
    assert set(v.metrics["liquidity"]) <= lines
