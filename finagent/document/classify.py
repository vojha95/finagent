"""Line-item classifiers for the document channel (blueprint 8.4).

Every model maps a RowInstance to a Prediction: top-3 (item, probability), OTHER included.
Inputs are the visible row only (label, parent header, statement, layout); never tags (rule 7).

M0: rules, label synonym dictionaries from configs/label_synonyms.yaml.
M1: TF-IDF (word 1-2-grams, character 3-5-grams) on statement + parent header + label, then a
class-weighted logistic regression. Probabilities are softmax(decision / temperature); the
temperature is fitted on validation outside this package (finagent.training.m1) and is 1.0 untrained.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yaml

from finagent.config import CONFIG_DIR, ConfigError
from finagent.records import Prediction, RowInstance

OTHER = "OTHER"
TOP_K = 3
LABEL_SYNONYMS_PATH = CONFIG_DIR / "label_synonyms.yaml"

_FOOTNOTE = re.compile(r"\s*\((note|notes)\s[^)]*\)|\s*\(\d\)|\*+$", re.IGNORECASE)


class RowClassifier(Protocol):
    version: str

    def predict(self, rows: Sequence[RowInstance]) -> list[Prediction]: ...


def normalise_label(text: str | None) -> str:
    """Lower case, straight apostrophes, no footnote references, single spaces, no colon."""
    text = (text or "").replace("’", "'").replace("‘", "'")
    text = _FOOTNOTE.sub("", text)
    return " ".join(text.lower().split()).rstrip(":").strip()


@dataclass(frozen=True)
class SynonymRule:
    item: str
    statements: tuple[str, ...]
    patterns: tuple[re.Pattern[str], ...]


def load_synonyms(path: Path = LABEL_SYNONYMS_PATH) -> list[SynonymRule]:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Cannot load label synonyms: {path}") from exc
    rules = []
    for raw in data.get("items", []):
        try:
            rules.append(SynonymRule(
                item=raw["id"],
                statements=tuple(raw["statements"]),
                patterns=tuple(re.compile(p) for p in raw["patterns"]),
            ))
        except (KeyError, TypeError, re.error) as exc:
            raise ConfigError(f"Bad synonym rule: {raw!r}") from exc
    return rules


class RulesClassifier:
    """M0: the first synonym rule whose pattern matches the whole label wins; else OTHER."""

    version = "m0-rules"

    def __init__(self, rules: list[SynonymRule] | None = None) -> None:
        self.rules = rules if rules is not None else load_synonyms()

    def classify(self, row: RowInstance) -> str:
        label = normalise_label(row.label)
        for rule in self.rules:
            if row.statement in rule.statements and any(p.fullmatch(label) for p in rule.patterns):
                return rule.item
        return OTHER

    def predict(self, rows: Sequence[RowInstance]) -> list[Prediction]:
        return [Prediction(r.row_id, ((self.classify(r), 1.0),), self.version) for r in rows]


def row_text(row: RowInstance) -> str:
    """The visible text M1 reads: statement, parent header and label."""
    return f"[{row.statement}] {normalise_label(row.parent_header)} | {normalise_label(row.label)}"


class TfidfLogisticClassifier:
    """M1: TF-IDF word and character n-grams -> class-weighted logistic regression."""

    version = "m1-tfidf-lr"

    def __init__(self, c: float = 4.0, max_iter: int = 2000, temperature: float = 1.0) -> None:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import FeatureUnion, Pipeline

        self.pipeline = Pipeline([
            ("features", FeatureUnion([
                ("words", TfidfVectorizer(analyzer="word", ngram_range=(1, 2), sublinear_tf=True)),
                ("chars", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5),
                                          sublinear_tf=True)),
            ])),
            ("model", LogisticRegression(C=c, class_weight="balanced", max_iter=max_iter)),
        ])
        self.temperature = temperature

    def decision(self, rows: Sequence[RowInstance]):
        """Raw class scores (logits), one column per class in `classes`."""
        import numpy as np

        scores = np.asarray(self.pipeline.decision_function([row_text(r) for r in rows]))
        if scores.ndim == 1:  # binary: sklearn returns the second class's score only
            scores = np.column_stack([np.zeros_like(scores), scores])
        return scores

    @property
    def classes(self) -> tuple[str, ...]:
        return tuple(str(c) for c in self.pipeline.classes_)

    def probabilities(self, rows: Sequence[RowInstance]):
        """softmax(decision / temperature), one row per input row."""
        import numpy as np

        scores = self.decision(rows) / self.temperature
        scores = np.exp(scores - scores.max(axis=1, keepdims=True))
        return scores / scores.sum(axis=1, keepdims=True)

    def fit(self, rows: Iterable[RowInstance], items: Iterable[str]) -> "TfidfLogisticClassifier":
        rows, items = list(rows), list(items)
        if len(set(items)) < 2:
            raise ValueError("M1 needs at least two classes to train")
        self.pipeline.fit([row_text(r) for r in rows], items)
        return self

    def predict(self, rows: Sequence[RowInstance]) -> list[Prediction]:
        if not rows:
            return []
        probabilities = self.probabilities(rows)
        classes = self.classes
        predictions = []
        for row, p in zip(rows, probabilities):
            top = sorted(zip(classes, p), key=lambda pair: -pair[1])[:TOP_K]
            predictions.append(Prediction(
                row.row_id, tuple((str(c), float(q)) for c, q in top), self.version
            ))
        return predictions
