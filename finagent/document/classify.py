"""Line-item classifiers for the document channel (blueprint 8.4).

Every model maps a RowInstance to a Prediction: top-3 (item, probability), OTHER included.
Inputs are the visible row only (label, parent header, statement, layout); never tags (rule 7).

M0: rules, label synonym dictionaries from configs/label_synonyms.yaml.
M1: TF-IDF (word 1-2-grams, character 3-5-grams) on statement + parent header + label, then a
class-weighted logistic regression. Probabilities are softmax(decision / temperature); the
temperature is fitted on validation outside this package (finagent.training.m1) and is 1.0 untrained.
M2: the same TF-IDF reduced by truncated SVD, plus the structure and magnitude features of
finagent.document.structure (layout, log share of the statement maximum, subtotal detector), then
a class-weighted LightGBM. Hyperparameters are chosen outside this package (finagent.training.m2).
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
        return top_predictions(rows, self.probabilities(rows), self.classes, self.version)


def top_predictions(rows: Sequence[RowInstance], probabilities, classes: Sequence[str],
                    version: str) -> list[Prediction]:
    """Top-K (item, probability) per row, most probable first."""
    predictions = []
    for row, p in zip(rows, probabilities):
        top = sorted(zip(classes, p), key=lambda pair: -pair[1])[:TOP_K]
        predictions.append(Prediction(
            row.row_id, tuple((str(c), float(q)) for c, q in top), version
        ))
    return predictions


class SvdBoostedTreeClassifier:
    """M2: TF-IDF SVD components + structure and magnitude features -> LightGBM.

    The features of a row depend on the other rows of its statement (statement maximum,
    subtotals), so fit and predict take whole statements: every row of each (filing, statement)
    the rows belong to.
    """

    version = "m2-svd-lgbm"

    def __init__(self, n_components: int = 128, num_leaves: int = 31, n_estimators: int = 400,
                 learning_rate: float = 0.05, min_child_samples: int = 20,
                 class_weight: str | None = "balanced", random_state: int = 0,
                 n_jobs: int = 4) -> None:
        self.params = {
            "n_components": n_components, "num_leaves": num_leaves,
            "n_estimators": n_estimators, "learning_rate": learning_rate,
            "min_child_samples": min_child_samples, "class_weight": class_weight,
            "random_state": random_state, "n_jobs": n_jobs,
        }
        self.text = None
        self.model = None

    def _text_pipeline(self):
        from sklearn.decomposition import TruncatedSVD
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.pipeline import FeatureUnion, Pipeline

        return Pipeline([
            ("features", FeatureUnion([
                ("words", TfidfVectorizer(analyzer="word", ngram_range=(1, 2), sublinear_tf=True)),
                ("chars", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5),
                                          sublinear_tf=True)),
            ])),
            ("svd", TruncatedSVD(n_components=self.params["n_components"],
                                 random_state=self.params["random_state"])),
        ])

    def _features(self, rows: Sequence[RowInstance], fit: bool = False):
        import numpy as np

        from finagent.document.structure import row_features

        texts = [row_text(r) for r in rows]
        dense = self.text.fit_transform(texts) if fit else self.text.transform(texts)
        return np.hstack([dense, np.asarray(row_features(rows), dtype=float)])

    def fit(self, rows: Iterable[RowInstance], items: Iterable[str]) -> "SvdBoostedTreeClassifier":
        from lightgbm import LGBMClassifier

        rows, items = list(rows), list(items)
        if len(set(items)) < 2:
            raise ValueError("M2 needs at least two classes to train")
        self.text = self._text_pipeline()
        features = self._features(rows, fit=True)
        p = self.params
        self.model = LGBMClassifier(
            objective="multiclass" if len(set(items)) > 2 else "binary",
            n_estimators=p["n_estimators"],
            learning_rate=p["learning_rate"], num_leaves=p["num_leaves"],
            min_child_samples=p["min_child_samples"], class_weight=p["class_weight"],
            random_state=p["random_state"], n_jobs=p["n_jobs"], deterministic=True,
            force_col_wise=True, verbose=-1,
        )
        self.model.fit(features, items)
        return self

    @property
    def classes(self) -> tuple[str, ...]:
        return tuple(str(c) for c in self.model.classes_)

    def probabilities(self, rows: Sequence[RowInstance], iterations: int | None = None):
        """Class probabilities, one row per input row; `iterations` uses the first n trees."""
        return self.model.predict_proba(self._features(rows), num_iteration=iterations)

    def staged_probabilities(self, rows: Sequence[RowInstance], iterations: Sequence[int]):
        """{n: probabilities with the first n boosting rounds} from one feature pass."""
        features = self._features(rows)
        return {n: self.model.predict_proba(features, num_iteration=n) for n in iterations}

    def predict(self, rows: Sequence[RowInstance]) -> list[Prediction]:
        if not rows:
            return []
        return top_predictions(rows, self.probabilities(rows), self.classes, self.version)
