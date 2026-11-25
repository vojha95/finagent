"""Insight cards (finagent.risk.insights; 8.13, 13)."""

from dataclasses import replace
from datetime import date

import pytest
from retrieval_support import EVIDENCE, filing

from finagent.config import load_retrieval_config, load_signal_config
from finagent.records import ConditionResult, SignalResult
from finagent.retrieval.index import Retriever, build_index
from finagent.retrieval.templates import load_query_templates
from finagent.risk.insights import (EVIDENCE_GAP, GAP_TEXT, VERIFIED, card_text, cards,
                                    condition_text, evidence, make_card, model_card_made,
                                    number, shown_conditions)

CONFIG = load_retrieval_config()
SIGNALS = load_signal_config()
TEMPLATES = load_query_templates()
AS_OF = date(2026, 11, 24)
FY2025 = filing(2025, date(2026, 2, 1), "0000000001-26-000001")


class FakeStore:
    evidence_dir, manifest = None, {}

    def load_filings(self, issuer, as_of):
        return [FY2025]


def retriever():
    r = Retriever(FakeStore(), {"SYNT": "validation"}, CONFIG)
    r.indexes[FY2025.accession] = build_index(FY2025, EVIDENCE.encode(), CONFIG)
    return r


def result(signal_id="R1", state="FIRED", conditions=None):
    conditions = conditions if conditions is not None else (
        ConditionResult("dso_change", "DSO up", "GAP", None, "days", ">", 5.0, "INPUT_GAP"),
        ConditionResult("receivables_growth_gap", "receivables growth minus revenue growth",
                        "FIRED", 22.2, "pp", ">", 10.0, "", "receivables +33.3%, revenue "
                        "+11.1%"))
    return SignalResult(signal_id, TEMPLATES.signals[signal_id].name, "SYNT", 2025, state,
                        SIGNALS.signals[signal_id].severity, "history", conditions)


def test_numbers_and_condition_text():
    assert number(6.04, "days", True) == "+6.0 days" and number(-4.0, "pp", True) == "-4.0 pp"
    assert number(0.95, "x") == "0.95x" and number(3.69e10, "USD") == "USD 36,900.0m"
    c = ConditionResult("current_ratio_change", "current ratio falling", "FIRED", -0.19, "x",
                        "<", 0.0, values=((2024, 1.14), (2025, 0.95)))
    assert condition_text(c) == ("current ratio falling -0.19x (test < +0.00x; "
                                 "FY2024 1.14, FY2025 0.95)")


def test_card_text_is_a_template():
    r = result()
    assert [c.condition_id for c in shown_conditions(r)] == ["receivables_growth_gap"]
    text = card_text(r, VERIFIED)
    assert text.startswith("R1 Receivables outpacing sales (FY2025, severity medium): "
                           "receivables growth minus revenue growth +22.2 pp")
    assert text.endswith("Evidence: quoted below.")
    assert card_text(r, EVIDENCE_GAP).endswith(GAP_TEXT)
    r11 = replace(result("R11", conditions=(ConditionResult(
        "revenue_growth", "revenue growth", "FIRED", 11.1, "pp", ">", 0.0),)),
        components=(("R1", "FIRED"), ("R2", "GAP"), ("R3", "FIRED"), ("R4", "FIRED")))
    assert "components fired: R1, R3, R4" in card_text(r11, VERIFIED)


def test_cards_quote_the_same_10k_or_say_evidence_gap():
    r = retriever()
    made = cards([result(), result("R2"), result("R5", "NOT_FIRED")], FY2025, r, TEMPLATES,
                 SIGNALS.evidence, AS_OF)
    assert [c.signal_id for c in made] == ["R1", "R2"]  # only fired signals get a card
    r1, r2 = made
    assert r1.status == VERIFIED and 1 <= len(r1.evidence) <= SIGNALS.evidence.max_quotes
    assert r1.evidence[0].text.startswith("Accounts receivable increased 33% in 2025")
    assert all(q.accession == FY2025.accession for q in r1.evidence)
    assert (r1.accession, r1.filed, r1.fiscal_period_end) == (FY2025.accession, FY2025.filed,
                                                              FY2025.end)
    assert r1.query == TEMPLATES.signals["R1"].query
    assert r2.status == EVIDENCE_GAP and r2.evidence == () and GAP_TEXT in r2.text
    assert r2.detail  # why: candidates or none, and the sentences skipped
    with pytest.raises(ValueError):
        cards([replace(result(), fiscal_year=2024)], FY2025, r, TEMPLATES, SIGNALS.evidence,
              AS_OF)


def test_make_card_from_found_quotes():
    found, query = evidence(retriever(), result(), TEMPLATES, SIGNALS.evidence, AS_OF)
    card = make_card(result(), FY2025, found, query)
    assert card.metrics == shown_conditions(result()) and card.severity == "medium"
    gap = make_card(result(), FY2025, replace(found, quotes=()), query)
    assert gap.status == EVIDENCE_GAP and "best score" in gap.detail


def test_model_derived_card_is_labelled_without_a_search_or_a_quote():
    """#180 (Vineet): R10 (model data) gets the label, no query, no quote; never EVIDENCE_GAP."""
    from finagent.planner.render import MODEL_DERIVED_TEXT

    r10 = replace(result("R10", conditions=(ConditionResult(
        "moderate_shortfall_max", "largest modelled cash shortfall in a Moderate year", "FIRED",
        3.0e9, "USD", ">", 0.0, "", "Moderate scenario from FY2025; largest in FY2028",
        ((2026, 1.0e9), (2027, 2.0e9), (2028, 3.0e9))),)), data="scenario",
        scenario="Moderate")

    class NoSearch:
        def search(self, *args, **kwargs):
            raise AssertionError("a model-derived card must not search the filing")

    (card,) = cards([r10], FY2025, NoSearch(), TEMPLATES, SIGNALS.evidence, AS_OF)
    label = SIGNALS.evidence.model_derived_label
    assert label == "model-derived; no filing evidence expected" == MODEL_DERIVED_TEXT
    assert (card.status, card.evidence, card.query, card.detail) == (VERIFIED, (), "", label)
    assert card.text.endswith(f"Evidence: {label}.") and GAP_TEXT not in card.text
    assert model_card_made(card)
