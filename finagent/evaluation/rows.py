"""Row-level metrics for the line-item classifiers (blueprint 8.4 training protocol, 10.1).

Accuracy counts every row, OTHER included. Macro-F1 averages over canonical items that have at
least one true row in the split, so rare items count as much as common ones; OTHER is reported
separately. Items with no true row in the split cannot be scored; any rows predicted as them are
listed as false positives.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from finagent.document.classify import OTHER


@dataclass(frozen=True)
class ItemScore:
    item: str
    precision: float | None  # None when nothing was predicted as the item
    recall: float | None  # None when the item has no true row
    f1: float | None
    support: int  # true rows
    predicted: int


@dataclass(frozen=True)
class RowMetrics:
    rows: int
    accuracy: float | None
    macro_f1: float | None  # over items with support, OTHER excluded
    items_scored: int
    other: ItemScore | None
    unsupported: dict[str, int] = field(default_factory=dict)  # item -> false positives


def _score(item: str, y_true: Sequence[str], y_pred: Sequence[str]) -> ItemScore:
    tp = sum(t == item and p == item for t, p in zip(y_true, y_pred))
    support = sum(t == item for t in y_true)
    predicted = sum(p == item for p in y_pred)
    precision = tp / predicted if predicted else None
    recall = tp / support if support else None
    if support == 0:
        f1 = None
    elif tp == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return ItemScore(item, precision, recall, f1, support, predicted)


def per_item_table(
    y_true: Sequence[str], y_pred: Sequence[str], items: Sequence[str] | None = None
) -> list[ItemScore]:
    """Precision, recall, F1 and support per item (OTHER included), sorted by item name.

    `items` adds items that appear in neither list (e.g. every concept-map item).
    """
    if len(y_true) != len(y_pred):
        raise ValueError("y_true and y_pred differ in length")
    names = set(y_true) | set(y_pred) | set(items or ())
    return [_score(item, y_true, y_pred) for item in sorted(names)]


def row_metrics(
    y_true: Sequence[str], y_pred: Sequence[str], items: Sequence[str] | None = None
) -> RowMetrics:
    table = per_item_table(y_true, y_pred, items)
    scored = [s for s in table if s.item != OTHER and s.support > 0]
    accuracy = sum(t == p for t, p in zip(y_true, y_pred)) / len(y_true) if y_true else None
    macro = sum(s.f1 for s in scored) / len(scored) if scored else None
    other = next((s for s in table if s.item == OTHER), None)
    unsupported = {s.item: s.predicted for s in table if s.item != OTHER and s.support == 0}
    return RowMetrics(len(y_true), accuracy, macro, len(scored), other, unsupported)


def evaluate_classifier(
    classifier, rows: Sequence, y_true: Sequence[str], items: Sequence[str] | None = None,
    top: int = 15,
) -> tuple[RowMetrics, list[ItemScore], list[tuple[str, str, int]]]:
    """Run `classifier` (any RowClassifier) on `rows`: metrics, per-item table, confusions."""
    y_pred = [p.item for p in classifier.predict(rows)]
    return (row_metrics(y_true, y_pred, items), per_item_table(y_true, y_pred, items),
            confusion_pairs(y_true, y_pred, top))


def confusion_pairs(
    y_true: Sequence[str], y_pred: Sequence[str], top: int = 15
) -> list[tuple[str, str, int]]:
    """The most frequent (true, predicted, count) errors, OTHER included; ties by name."""
    counts = Counter((t, p) for t, p in zip(y_true, y_pred) if t != p)
    return [(t, p, n) for (t, p), n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))][:top]
