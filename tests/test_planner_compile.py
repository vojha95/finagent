"""The goal compiler: front end, parsers (M0 grammar, M1 classifier with fallback), assembly."""

import json
import re
from pathlib import Path

import pytest
from planner_support import StubClassifier, resources

from finagent.planner.compile import (MODELS_DIR, ClauseText, GrammarParser, TrainedParser,
                                      assemble, clause_texts, compile_question,
                                      load_trained_parser, matches_any, question_clauses)
from finagent.planner.intents import IntentClassifier
from finagent.records import ClauseParse, Goal

R = resources()
M0 = GrammarParser()
ROOT = Path(__file__).resolve().parents[1]


def _goals(question, parser=M0, selected=None):
    c = compile_question(question, parser, R, selected)
    assert c.clarification is None, c.clarification
    return c.goals


def _reason(question, parser=M0, selected=None):
    c = compile_question(question, parser, R, selected)
    return c.clarification.reason if c.clarification else None


def test_clarify_uses_the_vocabulary_category():
    c = R.clarify("ADVICE")
    assert c.category == "advice" and c.status == "CLARIFICATION" and c.message


def test_clause_texts_and_question_clauses():
    (clause,) = clause_texts("what was <issuer>'s revenue in fy2024?", R.vocabulary)
    assert clause.years == (2024,) and "<year>" in clause.body and not clause.conditional
    clauses, mentions = question_clauses("Show Amazon revenue; reconcile the statements", R)
    assert len(clauses) == 2 and [m.ticker for m in mentions] == ["AMZN"]


def test_matches_any():
    assert matches_any("should i buy it", [r"\bbuy\b"]) and not matches_any("x", [r"y"])


def test_grammar_compiles_appendix_d_forms():
    assert _goals("What was Amazon's revenue in 2025?") == (Goal("AMZN", 2025, "fact",
                                                                 "revenue"),)
    assert _goals("Reconcile the three statements for Microsoft")[0].kind == "statements"
    (fc,) = _goals("Forecast free cash flow for Amazon in 2027")
    assert fc == Goal("AMZN", "latest", "forecast", "free_cash_flow", "Base", 2027)
    goals = _goals("Assess Severe liquidity for Microsoft; if there is a shortfall, check debt "
                   "service")
    assert [g.kind for g in goals] == ["liquidity", "debt"]
    assert goals[1].conditional and goals[1].scenario == "Severe"
    assert _reason("How much did Amazon sell last year?") == "OUT_OF_GRAMMAR"


def test_front_end_clarifications_before_parsing():
    assert _reason("Should I buy Amazon stock?") == "ADVICE"
    assert _reason("What was revenue in 2024?") == "NO_ISSUER"
    assert _reason("Amazon revenue in Q3 2024") == "UNSUPPORTED_PERIOD"
    assert _reason("Amazon revenue in 2023 and 2024") == "MULTIPLE_PERIODS"
    assert _reason("If revenue falls, check debt service for Amazon") == "UNSUPPORTED_CONDITION"


def test_selected_issuer_and_comparison():
    assert _goals("What was the revenue in 2024?", selected="MSFT")[0].issuer == "MSFT"
    goals = _goals("Compare Amazon and Microsoft revenue in 2024")
    assert [g.issuer for g in goals] == ["AMZN", "MSFT"]


def test_trained_parser_routes():
    clause = ClauseText("x", "what was <issuer>'s revenue in <year>", (2024,), False, True)
    confident = TrainedParser(StubClassifier("fact", 0.99), 0.8)
    parsed = confident.parse(clause, R)
    assert parsed.route == "classifier" and parsed.metrics == ("revenue",) and parsed.period == 2024
    assert TrainedParser(StubClassifier("advice", 0.99), 0.8).parse(clause, R) == "ADVICE"
    assert TrainedParser(StubClassifier("unsupported", 0.99), 0.8).parse(clause, R) == \
        "OUT_OF_SCOPE"
    assert TrainedParser(StubClassifier("statements", 0.99), 0.8).parse(
        ClauseText("x", "revenue next year", (), False, True), R) == "TARGET_NOT_APPLICABLE"


def test_below_threshold_falls_back_to_the_grammar_then_clarifies():
    unsure = TrainedParser(StubClassifier("risk", 0.4), 0.8, "v0")
    grammar_form = ClauseText("x", "what was the revenue", (), False, True)
    assert unsure.parse(grammar_form, R).route == "grammar"
    paraphrase = ClauseText("x", "how much did <issuer> sell", (), False, True)
    assert unsure.parse(paraphrase, R) == "LOW_CONFIDENCE"
    assert unsure.name == "m1-stub-v0"


def test_assemble_rules():
    liq = ClauseParse("l", "liquidity", (), "latest", "Moderate", 2027)
    debt = ClauseParse("d", "debt", (), "latest", "", None, True)
    goals = assemble([liq, debt], ["AMZN"], R.planner)
    assert goals[1] == Goal("AMZN", "latest", "debt", "", "Moderate", 2027, True)
    assert assemble([debt], ["AMZN"], R.planner) == "CONDITIONAL_WITHOUT_LIQUIDITY"
    fact_cond = ClauseParse("f", "fact", ("revenue",), 2024, conditional=True)
    assert assemble([liq, fact_cond], ["AMZN"], R.planner) == "UNSUPPORTED_CONDITION"
    fc = ClauseParse("f", "forecast", ("revenue", "cash"), "latest")
    assert [(g.metric, g.scenario) for g in assemble([fc], ["AMZN", "MSFT"], R.planner)] == \
        [("revenue", "Base"), ("cash", "Base"), ("revenue", "Base"), ("cash", "Base")]


def test_load_trained_parser(tmp_path):
    model = IntentClassifier(c=1.0).fit(["what was revenue", "reconcile statements"] * 2,
                                        ["fact", "statements"] * 2)
    import joblib

    joblib.dump(model, tmp_path / "intent_t0.joblib")
    parser = load_trained_parser(R, tmp_path, "t0")
    assert parser.threshold == R.planner.confidence_threshold
    assert parser.name == "m1-intent-tfidf-lr-t0"


def test_configured_threshold_matches_the_trained_model():
    meta = MODELS_DIR / f"intent_{R.planner.model_version}.meta.json"
    if not meta.exists():
        pytest.skip("intent model not trained")
    assert json.loads(meta.read_text())["threshold"] == R.planner.confidence_threshold


def test_no_company_specific_branch_in_the_planner():
    for path in (ROOT / "finagent" / "planner").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"(ticker|issuer)\s*==\s*['\"]", text), path.name
