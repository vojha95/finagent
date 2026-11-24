"""Intent classifier of the trained goal compiler (blueprint 8.15 step 3; Phase 6).

TF-IDF word and character n-grams -> class-weighted logistic regression, with a softmax
temperature fitted on the validation families. One prediction per clause body (normalised,
issuers and years masked, condition removed: finagent.planner.compile.clause_texts), so no
company name or year can become a feature. Vocabulary phrases are replaced by their type before
the features are built (a reported item, a ratio, a liquidity measure, a scenario, a projected
year, a latest-period phrase): the model learns how a request is put, not which synonym it uses,
and the slot filler reads the phrase itself. Labels: the eight goal kinds, plus advice and
unsupported (out of scope: CLARIFICATION). Trained by finagent.training.intent.
"""

import re
from collections.abc import Mapping, Sequence

from finagent.config import GOAL_KINDS, IntentVocabulary
from finagent.planner.text import ISSUER_TOKEN, YEAR_TOKEN, find_phrases

OUT_OF_SCOPE = ("advice", "unsupported")
INTENT_LABELS = GOAL_KINDS + OUT_OF_SCOPE
Tags = Mapping[str, tuple[str, ...]]  # type token -> phrases


def vocabulary_tags(vocabulary: IntentVocabulary) -> dict[str, tuple[str, ...]]:
    """Type token -> phrases: ratios, liquidity measures, reported or projected items,
    scenarios (named or ambiguous), projected years and latest-period phrases. A phrase in
    several tables takes the first type in this order."""
    def metric_phrases(*kinds: str) -> set[str]:
        return {p for k in kinds for ps in vocabulary.metrics.get(k, {}).values() for p in ps}

    ordered = [
        ("ratiox", metric_phrases("credit")),
        ("headroomx", metric_phrases("liquidity")),
        ("itemx", metric_phrases("fact", "forecast")),
        ("scenariox", {p for ps in vocabulary.scenarios.values() for p in ps}
         | set(vocabulary.ambiguous_scenarios)),
        ("targetx", {p for ps in vocabulary.targets.values() for p in ps}),
        ("latestx", set(vocabulary.latest_phrases)),
    ]
    tags: dict[str, tuple[str, ...]] = {}
    taken: set[str] = set()
    for token, phrases in ordered:
        tags[token] = tuple(sorted(phrases - taken))
        taken |= phrases
    return tags


def tag_phrases(body: str, tags: Tags) -> str:
    """The body with every tagged phrase replaced by its type token (longest phrase first)."""
    by_phrase = {p: token for token, phrases in tags.items() for p in phrases}
    found = find_phrases(body, by_phrase)
    out, last = [], 0
    for phrase, start, end in found:
        out += [body[last:start], f" {by_phrase[phrase]} "]
        last = end
    out.append(body[last:])
    return " ".join("".join(out).split())


def intent_text(body: str, tags: Tags | None = None) -> str:
    """The classifier's input: phrases as type tokens, issuer and year tokens as words, other
    numbers as one token."""
    text = tag_phrases(body, tags) if tags else body
    text = text.replace(ISSUER_TOKEN, " issuerx ").replace(YEAR_TOKEN, " yearx ")
    text = re.sub(r"\d+(?:[.,]\d+)*", " numx ", text)
    return " ".join(text.split())


class IntentClassifier:
    """TF-IDF + logistic regression over clause bodies. tags (vocabulary_tags) are stored with
    the model, so a saved model reads clauses exactly as it was trained."""

    version = "intent-tfidf-lr"

    def __init__(self, c: float = 4.0, max_iter: int = 3000, temperature: float = 1.0,
                 tags: Tags | None = None) -> None:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import FeatureUnion, Pipeline

        self.pipeline = Pipeline([
            ("features", FeatureUnion([
                ("words", TfidfVectorizer(analyzer="word", ngram_range=(1, 2), sublinear_tf=True,
                                          token_pattern=r"(?u)\b[\w&'-]+\b")),
                ("chars", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5),
                                          sublinear_tf=True)),
            ])),
            ("model", LogisticRegression(C=c, class_weight="balanced", max_iter=max_iter)),
        ])
        self.temperature = temperature
        self.tags = dict(tags or {})

    def _texts(self, bodies: Sequence[str]) -> list[str]:
        return [intent_text(b, self.tags) for b in bodies]

    def fit(self, bodies: Sequence[str], labels: Sequence[str]) -> "IntentClassifier":
        unknown = set(labels) - set(INTENT_LABELS)
        if unknown:
            raise ValueError(f"unknown intent labels {sorted(unknown)}")
        self.pipeline.fit(self._texts(bodies), list(labels))
        return self

    @property
    def classes(self) -> tuple[str, ...]:
        return tuple(str(c) for c in self.pipeline.classes_)

    def decision(self, bodies: Sequence[str]):
        """Raw class scores (logits), one column per class in `classes`."""
        import numpy as np

        return np.asarray(self.pipeline.decision_function(self._texts(bodies)))

    def probabilities(self, bodies: Sequence[str]):
        """softmax(decision / temperature), one row per clause body."""
        import numpy as np

        scores = self.decision(bodies) / self.temperature
        scores = np.exp(scores - scores.max(axis=1, keepdims=True))
        return scores / scores.sum(axis=1, keepdims=True)

    def predict(self, bodies: Sequence[str]) -> list[tuple[str, float]]:
        """(top intent, its probability) per clause body."""
        if not bodies:
            return []
        p = self.probabilities(bodies)
        classes = self.classes
        return [(classes[int(row.argmax())], float(row.max())) for row in p]
