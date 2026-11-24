import pytest

from finagent.document.classify import OTHER
from finagent.evaluation.rows import (
    confusion_pairs,
    evaluate_classifier,
    per_item_table,
    row_metrics,
)
from finagent.records import Prediction

Y_TRUE = ["revenue", "revenue", "net_income", OTHER, OTHER, OTHER]
Y_PRED = ["revenue", OTHER, "net_income", OTHER, "revenue", "total_receivables"]


def test_per_item_table():
    table = {s.item: s for s in per_item_table(Y_TRUE, Y_PRED, ["operating_expenses"])}
    revenue = table["revenue"]
    assert (revenue.support, revenue.predicted) == (2, 2)
    assert revenue.precision == revenue.recall == revenue.f1 == 0.5
    assert table["net_income"].f1 == 1.0
    assert table["operating_expenses"].f1 is None and table["operating_expenses"].precision is None
    assert table["total_receivables"].precision == 0.0 and table["total_receivables"].f1 is None
    with pytest.raises(ValueError):
        per_item_table(["a"], [])


def test_row_metrics_excludes_other_from_macro():
    metrics = row_metrics(Y_TRUE, Y_PRED, ["operating_expenses"])
    assert metrics.rows == 6 and metrics.accuracy == pytest.approx(3 / 6)
    assert metrics.items_scored == 2 and metrics.macro_f1 == pytest.approx((0.5 + 1.0) / 2)
    assert metrics.other.support == 3 and metrics.other.recall == pytest.approx(1 / 3)
    assert metrics.unsupported == {"operating_expenses": 0, "total_receivables": 1}
    empty = row_metrics([], [])
    assert empty.accuracy is None and empty.macro_f1 is None and empty.other is None


def test_row_metrics_item_never_predicted_scores_zero():
    metrics = row_metrics(["revenue", "net_income"], ["revenue", "revenue"])
    assert metrics.macro_f1 == pytest.approx((2 / 3 + 0.0) / 2)


def test_confusion_pairs_most_frequent_first():
    y_true = ["operating_expenses"] * 3 + ["net_income"] * 2 + ["revenue"]
    y_pred = ["total_costs_and_expenses"] * 3 + ["net_income_including_nci"] * 2 + ["revenue"]
    assert confusion_pairs(y_true, y_pred) == [
        ("operating_expenses", "total_costs_and_expenses", 3),
        ("net_income", "net_income_including_nci", 2),
    ]
    assert confusion_pairs(y_true, y_pred, top=1) == [
        ("operating_expenses", "total_costs_and_expenses", 3)]


def test_evaluate_classifier_uses_top_prediction():
    class Fixed:
        version = "fixed"

        def predict(self, rows):
            return [Prediction(r, ((p, 1.0),), self.version) for r, p in zip(rows, Y_PRED)]

    metrics, table, confusions = evaluate_classifier(Fixed(), list("abcdef"), Y_TRUE)
    assert metrics.accuracy == pytest.approx(0.5)
    assert {s.item for s in table} >= {"revenue", OTHER}
    assert (OTHER, "revenue", 1) in confusions
