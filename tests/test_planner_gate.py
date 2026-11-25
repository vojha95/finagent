"""Observation gate (blueprint 8.16; #117, #118): each rule, and the goal-level decision."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from planner_support import resources
from tools_support import AS_OF, answer, by_year, filings

from finagent.planner import gate as G
from finagent.records import Goal, Outcome, Quote

R = resources()
FS = filings()
F24 = by_year(FS, 2024)


def ctx(goal: Goal, metrics=None, required=None, allowed=None):
    kind = goal.kind
    answers = R.planner.answers[kind]
    if kind == "statements":
        metrics, required, allowed = answers.metrics, answers.required, answers.metrics
    metrics = metrics if metrics is not None else ((goal.metric,) if goal.metric else ())
    allowed = allowed if allowed is not None else tuple(R.vocabulary.metrics.get(
        "fact" if kind == "explain" else kind, {}))
    return G.build_context(goal, AS_OF, FS, answers, allowed, metrics, required)


FACT = Goal("MSFT", 2024, "fact", "revenue")
REV = answer(F24, "revenue", Decimal("245122000000"))


def test_build_context_resolves_years_from_the_filings():
    c = ctx(Goal("MSFT", "latest", "forecast", "revenue", "Base", "+2"))
    assert (c.origin_year, c.expected_year, c.projected) == (2025, 2027, True)
    c = ctx(Goal("MSFT", "latest", "credit", "current_ratio"))
    assert (c.origin_year, c.expected_year) == (2025, 2025)
    early = G.build_context(FACT, date(2024, 3, 1), FS, R.planner.answers["fact"], ["revenue"],
                            ["revenue"])
    assert [f.fiscal_year for f in early.filings] == [2021, 2022, 2023]


def test_a_genuine_fact_passes_every_rule():
    assert G.check(REV, ctx(FACT)) == G.GateDecision("revenue", True)


@pytest.mark.parametrize("change, code", [
    ({"metric": "weather"}, "METRIC_NOT_ALLOWED"),
    ({"metric": "net_income"}, "METRIC_NOT_REQUESTED"),
    ({"issuer": "AMZN"}, "ISSUER_MISMATCH"),
    ({"cik": "0000000001"}, "CIK_MISMATCH"),
    ({"fiscal_year": 2023}, "FISCAL_YEAR_MISMATCH"),
    ({"fiscal_period_end": date(2024, 6, 30)}, "PERIOD_END_MISMATCH"),
    ({"unit": "x"}, "UNIT_MISMATCH"),
    ({"classification": "CALCULATION"}, "CLASSIFICATION_MISMATCH"),
    ({"accession": "0000000000-00-000000"}, "UNKNOWN_FILING"),
    ({"filing_url": "https://example/other.htm"}, "PROVENANCE_MISMATCH"),
    ({"value": Decimal("NaN")}, "NOT_FINITE"),
    ({"value": None}, "NOT_FINITE"),
    ({"basis": " "}, "NO_BASIS"),
    ({"validated": False, "channels": ("tag",)}, "UNFLAGGED_SINGLE_CHANNEL"),
    ({"quotes": (Quote("c1", "other-accession", "text"),)}, "QUOTE_FROM_OTHER_FILING"),
])
def test_each_rule_rejects(change, code):
    allowed = tuple(R.vocabulary.metrics["fact"]) + (("weather",) if change.get("metric") ==
                                                     "weather" else ())
    c = ctx(FACT, metrics=("revenue", "net_income", "weather"), allowed=allowed) \
        if "metric" in change else ctx(FACT)
    if change.get("metric") == "weather":
        c = replace(c, allowed=frozenset(R.vocabulary.metrics["fact"]))
    if change.get("metric") == "net_income":
        c = replace(c, metrics=("revenue",))
    assert G.check(replace(REV, **change), c).code == code


def test_filed_after_as_of_rule():
    late = replace(REV, filed=date(2027, 1, 1))
    assert G.filed_by_as_of(late, ctx(FACT)) == "FILED_AFTER_AS_OF"
    assert G.filed_by_as_of(REV, ctx(FACT)) is None


def test_single_channel_fact_with_its_flag_passes():
    tag_only = replace(REV, validated=False, channels=("tag",), flags=("SINGLE_CHANNEL",))
    assert G.check(tag_only, ctx(FACT)).accepted


def test_restated_fact_may_cite_a_later_filing_only_when_flagged():  # #117
    later = by_year(FS, 2025)
    restated = replace(REV, accession=later.accession, filing_url=later.filing_url,
                       filed=later.filed, flags=("RESTATED",), original_value=Decimal(1))
    assert G.check(restated, ctx(FACT)).accepted
    assert G.check(replace(restated, flags=()), ctx(FACT)).code == "ORIGIN_MISMATCH"
    ratio = replace(answer(F24, "current_ratio", 1.2, unit="x", classification="CALCULATION"),
                    accession=later.accession, filing_url=later.filing_url, filed=later.filed,
                    flags=("RESTATED",))
    assert G.check(ratio, ctx(Goal("MSFT", 2024, "credit", "current_ratio"))).code == \
        "ORIGIN_MISMATCH"


def test_classification_and_unit_are_per_metric():  # #118
    fcf = answer(F24, "free_cash_flow", Decimal(5), classification="CALCULATION")
    assert G.check(fcf, ctx(Goal("MSFT", 2024, "fact", "free_cash_flow"))).accepted
    gp = answer(F24, "gross_profit", Decimal(5), classification="CALCULATION")
    assert G.check(gp, ctx(Goal("MSFT", 2024, "fact", "gross_profit"))).accepted
    dso = answer(F24, "dso", 45.0, unit="days", classification="CALCULATION")
    assert G.check(dso, ctx(Goal("MSFT", 2024, "credit", "dso"))).accepted
    assert G.check(replace(dso, unit="x"), ctx(Goal("MSFT", 2024, "credit", "dso"))).code == \
        "UNIT_MISMATCH"


def test_projection_cites_the_origin_with_no_period_end():
    goal = Goal("MSFT", "latest", "forecast", "revenue", "Base", None)
    origin = by_year(FS, 2025)
    p = answer(origin, "revenue", 1.0, classification="CALCULATION_FROM_MODEL_ASSUMPTION",
               fiscal_year=2026, projected=True, scenario="Base")
    assert G.check(p, ctx(goal)).accepted
    assert G.check(replace(p, fiscal_period_end=origin.end), ctx(goal)).code == \
        "PERIOD_END_MISMATCH"
    older = by_year(FS, 2024)
    moved = replace(p, accession=older.accession, filing_url=older.filing_url, filed=older.filed)
    assert G.check(moved, ctx(goal)).code == "ORIGIN_MISMATCH"
    assert G.check(replace(p, validated=False), ctx(goal)).code == "NOT_VALIDATED"


def test_not_meaningful_ratio_passes_with_its_reason():  # #120 b
    goal = Goal("MSFT", 2024, "credit", "ebit_to_gross_interest")
    nm = answer(F24, "ebit_to_gross_interest", None, unit="x", classification="CALCULATION",
                flags=("NOT_MEANINGFUL",), detail="non-positive denominator")
    assert G.check(nm, ctx(goal)).accepted
    assert G.check(replace(nm, detail=""), ctx(goal)).code == "NOT_FINITE"


def test_non_answer_is_rejected():
    assert G.check(object(), ctx(FACT)).code == "NOT_AN_ANSWER"  # type: ignore[arg-type]


# ---------------------------------------------------------------- goal level

def test_gate_passes_unverified_outcomes_through():
    out, decisions = G.gate(Outcome("MISSING_PRIMARY", "DOC_MISSING"), ctx(FACT))
    assert (out.status, out.reason, decisions) == ("MISSING_PRIMARY", "DOC_MISSING", ())
    out, _ = G.gate(Outcome("GAP", "NOT_REPORTED", (REV,)), ctx(FACT))
    assert (out.status, out.reason) == ("INVALID", "UNVERIFIED_WITH_VALUES")


def test_gate_selects_the_requested_metric_and_rejects_duplicates_and_missing():
    other = answer(F24, "net_income", Decimal(1))
    out, decisions = G.gate(Outcome("VERIFIED", "AGREE", (other, REV)), ctx(FACT))
    assert out.status == "VERIFIED" and out.observations == (REV,) and len(decisions) == 1
    out, _ = G.gate(Outcome("VERIFIED", "AGREE", (REV, REV)), ctx(FACT))
    assert out.reason == "DUPLICATE_METRIC"
    out, _ = G.gate(Outcome("VERIFIED", "AGREE", (other,)), ctx(FACT))
    assert out.reason == "NO_OBSERVATION_FOR_METRIC"
    out, _ = G.gate(Outcome("VERIFIED", "AGREE", (replace(REV, unit="x"),)), ctx(FACT))
    assert (out.status, out.reason) == ("INVALID", "UNIT_MISMATCH")


def test_metric_listed_as_a_tool_gap_is_a_gap():  # #120 c
    goal = Goal("MSFT", 2024, "credit", "quick_ratio")
    out, _ = G.gate(Outcome("VERIFIED", "RATIOS_COMPUTED",
                            gaps=(("quick_ratio", "INPUT_GAP: inputs without a value"),)),
                    ctx(goal))
    assert (out.status, out.reason) == ("GAP", "INPUT_GAP")


def test_statements_goal_needs_i9_and_i10():
    goal = Goal("MSFT", 2024, "statements")
    i9, i10 = (answer(F24, i, Decimal(0), classification="CALCULATION") for i in ("I9", "I10"))
    out, _ = G.gate(Outcome("VERIFIED", "ok", (i9, i10)), ctx(goal))
    assert out.status == "VERIFIED" and {o.metric for o in out.observations} == {"I9", "I10"}
    out, _ = G.gate(Outcome("VERIFIED", "ok", (i9,)), ctx(goal))
    assert (out.status, out.reason) == ("INVALID", "NO_OBSERVATION_FOR_METRIC")


def test_finite():
    assert G.finite(Decimal(1)) and G.finite(1.5) and not G.finite(float("inf"))
    assert not G.finite(True) and not G.finite("1") and not G.finite(Decimal("NaN"))


def test_quotes_are_checked_against_the_rebuilt_chunk():  # #157
    quoted = replace(REV, quotes=(Quote("c1", REV.accession, "Revenue rose.", "ITEM_7", 0.5,
                                        "sha"),))
    assert G.quotes_verified(REV, ctx(FACT)) is None  # nothing quoted, nothing to check
    assert G.check(quoted, ctx(FACT)).code == "QUOTE_NOT_CHECKABLE"
    seen = []

    def check(o, filing):
        seen.append((o.metric, filing.accession))
        return None if o.quotes[0].text == "Revenue rose." else "QUOTE_NOT_VERBATIM"

    c = replace(ctx(FACT), quote_check=check)
    assert G.check(quoted, c).accepted and seen == [("revenue", F24.accession)]
    forged = replace(quoted, quotes=(replace(quoted.quotes[0], text="Revenue doubled."),))
    assert G.check(forged, c).code == "QUOTE_NOT_VERBATIM"


def test_low_confidence_sentence_is_never_checked_or_counted_as_a_quote():  # D-EV2
    from finagent.records import LowConfidenceSentence

    forged = Quote("c1", "0000000000-00-000000", "Not in any filing.", "ITEM_7", 0.5, "sha")
    low = LowConfidenceSentence(forged, 1.0, 5.263, "Low confidence: may not explain the "
                                                    "movement")
    shown = replace(REV, low_confidence=low)
    seen = []

    def check(o, filing):
        seen.append(o.metric)
        return "QUOTE_NOT_VERBATIM"

    c = replace(ctx(FACT), quote_check=check)
    assert G.quotes_verified(shown, c) is None and G.quotes_from_accession(shown, c) is None
    assert G.check(shown, ctx(FACT)).accepted and G.check(shown, c).accepted and seen == []
