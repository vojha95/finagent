"""Intent classifier: vocabulary type tokens, input text and the TF-IDF model."""

import pytest
from planner_support import resources

from finagent.planner.intents import (INTENT_LABELS, IntentClassifier, intent_text, tag_phrases,
                                      vocabulary_tags)

V = resources().vocabulary


def test_labels_are_goal_kinds_plus_out_of_scope():
    assert INTENT_LABELS[-2:] == ("advice", "unsupported") and "fact" in INTENT_LABELS


def test_vocabulary_tags_types_and_precedence():
    tags = vocabulary_tags(V)
    assert list(tags) == ["ratiox", "headroomx", "itemx", "scenariox", "targetx", "latestx"]
    assert "current ratio" in tags["ratiox"] and "revenue" in tags["itemx"]
    assert "severe" in tags["scenariox"] and "stress" in tags["scenariox"]
    assert "next year" in tags["targetx"] and "latest" in tags["latestx"]
    every = [p for phrases in tags.values() for p in phrases]
    assert len(every) == len(set(every))  # a phrase has one type


def test_tag_phrases_longest_first():
    tags = {"itemx": ("net income", "income"), "scenariox": ("severe",)}
    assert tag_phrases("net income under severe", tags) == "itemx under scenariox"


def test_intent_text_masks_issuers_years_and_numbers():
    assert intent_text("<issuer> revenue in <year> above 1,200") == \
        "issuerx revenue in yearx above numx"
    assert intent_text("<issuer> revenue", {"itemx": ("revenue",)}) == "issuerx itemx"


TRAIN = [("what was revenue in <year>", "fact"), ("show net income for <issuer>", "fact"),
         ("reconcile the statements", "statements"), ("check the statements tie", "statements"),
         ("should i buy the stock", "advice"), ("is it a good investment", "advice")] * 3


def test_classifier_fits_and_predicts_with_calibrated_probabilities():
    model = IntentClassifier(c=4.0, tags=vocabulary_tags(V)).fit(*zip(*TRAIN))
    assert set(model.classes) == {"fact", "statements", "advice"}
    p = model.probabilities(["reconcile the statements for <issuer>"])
    assert p.shape == (1, 3) and abs(p.sum() - 1) < 1e-9
    label, confidence = model.predict(["reconcile the statements for <issuer>"])[0]
    assert label == "statements" and 0 < confidence <= 1
    assert model.decision(["what was revenue"]).shape == (1, 3)
    model.temperature = 0.1
    assert model.predict(["reconcile the statements"])[0][1] > confidence
    assert model.predict([]) == []


def test_unknown_label_is_rejected():
    with pytest.raises(ValueError):
        IntentClassifier().fit(["a", "b"], ["fact", "weather"])
