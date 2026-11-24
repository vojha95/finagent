"""Intent classifier training: C choice, temperature, threshold rule, compile scores, card."""

import json
import math
from pathlib import Path

import pandas as pd
import pytest
from planner_support import resources

from finagent.config import load_issuer_names, load_universe
from finagent.planner.compile import GrammarParser
from finagent.planner.intent_data import generate, load_templates, write_dataset
from finagent.training.intent import (HAND_WRITTEN, ClauseRow, calibrate, choose_threshold,
                                      clause_rows, compile_scores, expected_category, macro_f1,
                                      model_card, per_class, save, select_c, train,
                                      write_model_card)

R = resources()

TEMPLATES = """
seed: 11
samples_per_family: 4
validation_share: 0.5
verb_phrases: []
ticker_share: 0.0
families:
  fact:
    - {id: f1, style: template, text: "What was {issuer}'s {metric} in {year}?"}
    - {id: f2, style: template, text: "Show {issuer} {metric} for {year}"}
  statements:
    - {id: s1, style: template, text: "Reconcile the three statements for {issuer}"}
    - {id: s2, style: template, text: "Validate the latest statements for {issuer}"}
  advice:
    - {id: a1, style: template, text: "Should I buy {issuer} stock?"}
    - {id: a2, style: template, text: "Is {issuer} a good investment?"}
composites: []
"""


@pytest.fixture(scope="module")
def dataset(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("intents")
    (tmp / "templates.yaml").write_text(TEMPLATES, encoding="utf-8")
    samples = generate(load_templates(tmp / "templates.yaml"), R.vocabulary, load_issuer_names(),
                       load_universe(), R.planner)
    write_dataset(samples, "t1", tmp, tmp / "templates.yaml")
    return tmp


def _rows(labels, split="train"):
    bodies = {"fact": "what was <issuer>'s revenue in <year>", "statements":
              "reconcile the statements for <issuer>", "advice": "should i buy <issuer> stock"}
    return [ClauseRow(bodies[l] + f" {i}", l, "f", "template", split)
            for i, l in enumerate(labels)]


def test_clause_rows_use_the_compiler_front_end(dataset):
    frame = pd.read_parquet(dataset / "intents_t1.parquet")
    rows, multi = clause_rows(frame, R)
    assert len(rows) == len(frame) and multi == 0
    assert all("<issuer>" in r.body for r in rows)


def test_per_class_and_macro_f1():
    table = per_class(["fact", "fact", "advice"], ["fact", "advice", "advice"])
    assert table["fact"]["recall"] == 0.5 and table["risk"]["support"] == 0
    assert macro_f1(["fact", "advice"], ["fact", "advice"]) == 1.0


def test_select_c_keeps_the_best_and_breaks_ties_on_smaller_c():
    train_rows = _rows(["fact", "statements", "advice"] * 4)
    val_rows = _rows(["fact", "statements", "advice"], "validation")
    c, scores, model = select_c(train_rows, val_rows, (1.0, 4.0))
    assert set(scores) == {"1.0", "4.0"} and c == 1.0 and scores["1.0"] == 1.0
    cal = calibrate(model, val_rows)
    assert cal["temperature"] > 0 and cal["nll_after"] <= cal["nll_before"] + 1e-9


def test_choose_threshold_rule():
    conf = [0.95, 0.9, 0.6, 0.5]
    correct = [True, True, False, True]
    t, table = choose_threshold(conf, correct, 0.98, (0.4, 0.55, 0.7, 0.92))
    assert t == 0.7 and table[0]["coverage"] == 1.0
    t, _ = choose_threshold([0.9, 0.8], [False, True], 0.99, (0.5, 0.85))
    assert t == 0.5  # none reaches the target: the most precise (ties: lower threshold)


def test_expected_category_treats_nan_as_none():
    assert expected_category({"clarification": float("nan")}) == ""
    assert expected_category({"clarification": "advice"}) == "advice"


def test_compile_scores_counts_clarifications(dataset):
    frame = pd.read_parquet(dataset / "intents_t1.parquet")
    scores = compile_scores(frame, GrammarParser(), R)
    assert scores["accuracy"]["advice"] == 1.0
    assert scores["counts"]["all"] == len(frame)
    assert set(scores["routes"]) <= {"grammar", "classifier"}


def test_train_save_and_card(dataset, tmp_path):
    model, meta = train("t1", dataset, (1.0,), R)
    assert 0 <= meta["validation_accuracy"] <= 1 and meta["threshold"] in \
        [r["threshold"] for r in meta["threshold_table"]]
    assert math.isclose(model.temperature, meta["calibration"]["temperature"])
    path = save(model, meta, tmp_path, "t1")
    assert path.exists() and json.loads((tmp_path / "intent_t1.meta.json").read_text())["c"] == 1.0
    card = model_card(meta)
    assert "Threshold" in card and "Should I" not in card  # metrics only, no question text
    card_path = tmp_path / "card.md"
    card_path.write_text("old\n" + HAND_WRITTEN + "\nkept by hand\n", encoding="utf-8")
    write_model_card(meta, card_path)
    assert card_path.read_text().endswith("kept by hand\n")
