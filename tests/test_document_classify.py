from datetime import date

import pytest

from finagent.config import ConfigError, load_concept_map
from finagent.document.classify import (
    OTHER,
    TOP_K,
    RulesClassifier,
    TfidfLogisticClassifier,
    load_synonyms,
    normalise_label,
    row_text,
)
from finagent.records import RowInstance


def _row(label, statement="IS", parent=None, row_id="r") -> RowInstance:
    return RowInstance(
        row_id=row_id, accession="a", statement=statement, label=label, parent_header=parent,
        indent=0, is_total=False, is_bold=False, position=0, cell_texts=("1",),
        column_periods=(date(2025, 12, 31),), unit_text="(in millions)", table_index=0,
        source_row=0, column_spans=((1, 1),),
    )


def test_normalise_label():
    assert normalise_label("Total Stockholders’ Equity:") == "total stockholders' equity"
    assert normalise_label("Commitments and contingencies (Note 7)") == "commitments and contingencies"
    assert normalise_label(None) == ""


def test_synonym_items_exist_in_concept_map():
    items = set(load_concept_map())
    assert {rule.item for rule in load_synonyms()} <= items


def test_bad_synonym_file_raises(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text("items:\n  - id: x\n    statements: [IS]\n    patterns: ['(']\n")
    with pytest.raises(ConfigError):
        load_synonyms(path)


@pytest.mark.parametrize(
    ("label", "statement", "expected"),
    [
        ("Total net sales", "IS", "revenue"),
        ("Total revenue", "IS", "revenue"),
        ("Cost of sales", "IS", "cost_of_revenue"),
        ("Income before income taxes", "IS", "pretax_income"),
        ("Provision for income taxes", "IS", "income_tax_expense"),
        ("Net income", "IS", "net_income"),
        ("Net income", "CF", "net_income"),
        ("Cash and cash equivalents", "BS", "cash_and_cash_equivalents"),
        ("Total stockholders’ equity", "BS", "equity_attributable_to_parent"),
        ("Total liabilities and stockholders’ equity", "BS", "total_liabilities_and_equity"),
        ("Purchases of property and equipment", "CF", "capital_expenditure"),
        ("Net cash provided by (used in) operating activities", "CF", "operating_cash_flow"),
        ("Net cash from operations", "CF", "operating_cash_flow"),
        ("Fulfillment", "IS", OTHER),
        ("Cash and cash equivalents", "IS", OTHER),  # wrong statement for the synonym
    ],
)
def test_rules_classifier(label, statement, expected):
    assert RulesClassifier().classify(_row(label, statement)) == expected


def test_rules_predictions_have_version_and_probability():
    (prediction,) = RulesClassifier().predict([_row("Total assets", "BS", row_id="x")])
    assert prediction.row_id == "x" and prediction.item == "total_assets"
    assert prediction.candidates == (("total_assets", 1.0),)
    assert prediction.model_version == "m0-rules"


def test_row_text_uses_only_visible_text():
    assert row_text(_row("Inventories", "BS", "Current assets:")) == (
        "[BS] current assets | inventories"
    )


def test_tfidf_classifier_learns_and_returns_top_k():
    train = [
        (_row("Total net sales"), "revenue"), (_row("Total revenue"), "revenue"),
        (_row("Net revenues"), "revenue"), (_row("Net income"), "net_income"),
        (_row("Net earnings"), "net_income"), (_row("Net income (loss)"), "net_income"),
        (_row("Fulfillment"), OTHER), (_row("Marketing"), OTHER), (_row("Technology"), OTHER),
    ]
    model = TfidfLogisticClassifier().fit([r for r, _ in train], [y for _, y in train])
    (prediction,) = model.predict([_row("Total revenues")])
    assert prediction.item == "revenue"
    assert len(prediction.candidates) == TOP_K
    probabilities = [p for _, p in prediction.candidates]
    assert probabilities == sorted(probabilities, reverse=True) and sum(probabilities) <= 1.0001
    assert model.predict([]) == []


def test_tfidf_classifier_needs_two_classes():
    with pytest.raises(ValueError):
        TfidfLogisticClassifier().fit([_row("a"), _row("b")], ["revenue", "revenue"])


def _tiny_model(**kwargs):
    train = [
        (_row("Total net sales"), "revenue"), (_row("Total revenue"), "revenue"),
        (_row("Net income"), "net_income"), (_row("Net earnings"), "net_income"),
        (_row("Fulfillment"), OTHER), (_row("Marketing"), OTHER),
    ]
    return TfidfLogisticClassifier(**kwargs).fit([r for r, _ in train], [y for _, y in train])


def test_tfidf_temperature_one_matches_predict_proba():
    model = _tiny_model()
    rows = [_row("Total revenues"), _row("Net loss")]
    expected = model.pipeline.predict_proba([row_text(r) for r in rows])
    assert model.temperature == 1.0
    assert abs(model.probabilities(rows) - expected).max() < 1e-9
    assert model.classes == tuple(model.pipeline.classes_)


def test_tfidf_higher_temperature_flattens_probabilities():
    model = _tiny_model()
    rows = [_row("Total revenues")]
    sharp = model.predict(rows)[0]
    model.temperature = 5.0
    flat = model.predict(rows)[0]
    assert flat.item == sharp.item
    assert flat.candidates[0][1] < sharp.candidates[0][1]


def test_tfidf_decision_has_one_column_per_class_for_two_classes():
    model = TfidfLogisticClassifier().fit([_row("Revenue"), _row("Marketing")], ["revenue", OTHER])
    assert model.decision([_row("Revenue")]).shape == (1, 2)
    assert model.predict([_row("Revenue")])[0].item == "revenue"
