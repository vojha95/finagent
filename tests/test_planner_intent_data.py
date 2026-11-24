"""Training-data generator: templates, family split, train-split issuers, goals, leakage."""

import inspect
import random
from pathlib import Path

import pytest
from planner_support import resources

import finagent.planner.intent_data as intent_data
from finagent.config import load_issuer_names, load_universe
from finagent.planner.intent_data import (TRAIN, VALIDATION, Composite, Family, SlotSampler,
                                          TemplateSet, family_splits, generate, goals_from_json,
                                          goals_json, load_templates, render, render_composite,
                                          tidy, to_frame, write_dataset)
from finagent.records import Goal

R = resources()
UNIVERSE = load_universe()
NAMES = load_issuer_names()

SMALL = """
seed: 7
samples_per_family: 3
validation_share: 0.5
verb_phrases: [sell]
ticker_share: 0.0
families:
  fact:
    - {id: f1, style: template, text: "What was {issuer}'s {metric} in {year}?"}
    - {id: f2, style: paraphrase, text: "How much did {issuer} sell in {year}?", metrics: [revenue]}
  liquidity:
    - {id: l1, style: template, text: "Assess {scenario} liquidity for {issuer}"}
    - {id: l2, style: paraphrase, text: "Is {issuer} liquid {target} under {scenario}?"}
  debt:
    - {id: d1, style: template, text: "Check debt service for {issuer}"}
    - {id: d2, style: template, text: "Can {issuer} service its debt {target}?"}
  advice:
    - {id: a1, style: template, text: "Should I buy {issuer} stock?"}
composites:
  - {id: c1, first: liquidity, second: debt, join: "; if there is a {condition}, ", conditional: true}
"""


def _templates(tmp_path: Path, text: str = SMALL) -> TemplateSet:
    path = tmp_path / "templates.yaml"
    path.write_text(text, encoding="utf-8")
    return load_templates(path)


def _sampler(templates: TemplateSet, seed: int = 1) -> SlotSampler:
    return SlotSampler(random.Random(seed), R.vocabulary, NAMES, UNIVERSE, templates)


def test_load_templates_checks_slots_and_ids(tmp_path):
    t = _templates(tmp_path)
    assert [f.family_id for f in t.families][:2] == ["f1", "f2"]
    assert t.families[1].metrics == ("revenue",) and t.composites[0].conditional
    with pytest.raises(ValueError):
        _templates(tmp_path, SMALL.replace("{metric}", "{colour}"))
    with pytest.raises(ValueError):
        _templates(tmp_path, SMALL.replace("id: f2", "id: f1"))


def test_family_split_is_per_intent_and_seeded():
    fams = [Family(f"f{i}", "fact", "template", "x") for i in range(10)] + \
        [Family("r1", "risk", "template", "x")]
    split = family_splits(fams, 0.2, 3)
    assert sum(split[f"f{i}"] == VALIDATION for i in range(10)) == 2
    assert split["r1"] == VALIDATION  # at least one per intent
    assert split == family_splits(fams, 0.2, 3)


def test_sampler_uses_train_issuers_only(tmp_path):
    sampler = _sampler(_templates(tmp_path))
    assert sampler.issuers and all(UNIVERSE[t] == TRAIN for t in sampler.issuers)
    for _ in range(50):
        assert UNIVERSE[sampler.issuer()[1]] == TRAIN


def test_render_goals(tmp_path):
    t = _templates(tmp_path)
    sampler = _sampler(t)
    fact = render(t.families[0], sampler, R.planner)
    (goal,) = fact.goals
    assert goal.kind == "fact" and isinstance(goal.period, int) and goal.target is None
    assert render(t.families[1], sampler, R.planner).goals[0].metric == "revenue"
    liq = render(t.families[2], sampler, R.planner).goals[0]
    assert liq.period == "latest" and liq.scenario in {"Base", "Moderate", "Severe"}
    assert render(t.families[4], sampler, R.planner).goals[0].scenario == "Base"  # default
    assert render(t.families[6], sampler, R.planner).goals == ()


def test_conditional_composite_inherits_the_liquidity_goal(tmp_path):
    t = _templates(tmp_path)
    r = render_composite(t.composites[0], t.families[2], t.families[4], _sampler(t), R.planner)
    liquidity, debt = r.goals
    assert debt.conditional and debt.scenario == liquidity.scenario
    assert debt.target == liquidity.target and "; if there is a" in r.question


def test_generate_counts_splits_and_composites(tmp_path):
    t = _templates(tmp_path)
    samples = generate(t, R.vocabulary, NAMES, UNIVERSE, R.planner)
    plain = [s for s in samples if not s.composite]
    for family in t.families:
        assert sum(s.family == family.family_id for s in plain) == t.samples_per_family
    assert {s.split for s in samples if s.composite} == {VALIDATION}
    assert all(s.clarification == "advice" for s in plain if s.intent == "advice")
    assert samples == generate(t, R.vocabulary, NAMES, UNIVERSE, R.planner)  # seeded


def test_goals_json_round_trip_and_frame():
    goals = (Goal("AAPL", 2024, "fact", "revenue"), Goal("AAPL", "latest", "forecast", "cash",
                                                         "Base", "+2"))
    assert goals_from_json(goals_json(goals)) == goals
    frame = to_frame([intent_data.Sample("q", "fact", "f1", "template", TRAIN, goals)])
    assert frame.loc[0, "clarification"] == "" and frame.loc[0, "goals"] == goals_json(goals)


def test_write_dataset_meta_records_the_issuer_split(tmp_path):
    t = _templates(tmp_path)
    samples = generate(t, R.vocabulary, NAMES, UNIVERSE, R.planner)
    path = write_dataset(samples, "t1", tmp_path, tmp_path / "templates.yaml")
    meta = path.with_name("intents_t1.meta.json").read_text()
    assert path.exists() and '"issuer_split": "train"' in meta


def test_real_templates_render_every_family():
    t = load_templates()
    sampler = _sampler(t)
    for family in t.families:
        r = render(family, sampler, R.planner)
        assert "{" not in r.question
    assert {c.first for c in t.composites} <= {f.intent for f in t.families}


def test_generator_never_reads_the_gold_set():
    code = inspect.getsource(intent_data).replace(intent_data.__doc__, "").lower()
    assert "gold" not in code and "e5" not in code


def test_tidy_removes_slot_artefacts():  # #132
    assert tidy("What is Lowe's's revenue in in three years?") == \
        "What is Lowe's revenue in three years?"
    assert tidy("Walmart's revenue in the year") == "Walmart's revenue in the year"
    assert tidy("in a central case case") == "in a central case"


def test_composite_dataclass():
    assert Composite("c", "fact", "credit", ". ").conditional is False
