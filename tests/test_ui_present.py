"""Tests for finagent/ui/present.py: the plain-language answer view and its no-new-numbers
audit (Phase 9 UI rework). Synthetic investigations cover every answer kind; the real-run checks
need the git-ignored models and skip without them."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from finagent.planner.gate import GateDecision
from finagent.planner.render import Comparison
from finagent.records import (AnswerObservation, Clarification, Compilation, DroppedClause, Goal,
                              LowConfidenceSentence, Quote)
from finagent.ui import present as p

ROOT = Path(__file__).resolve().parents[1]
FILED = date(2026, 7, 29)
URL = "https://example.invalid/10k.htm"
ACC = "0000000001-26-000001"


@pytest.fixture(scope="module")
def labels():
    return p.Labels.load()


def _obs(metric, value, unit="USD", classification="REPORTED_FACT", **kw):
    base = dict(issuer="MSFT", cik="1", fiscal_year=2026, metric=metric, unit=unit, value=value,
                classification=classification, accession=ACC, filing_url=URL, filed=FILED,
                fiscal_period_end=date(2026, 6, 30), basis="AGREE", validated=True,
                channels=("document", "tag"))
    return AnswerObservation(**{**base, **kw})


def _goal(goal, observations=(), status="VERIFIED", reason="", requested=True, gaps=(),
          detail=""):
    return SimpleNamespace(goal=goal, requested=requested, status=status, reason=reason,
                           detail=detail, observations=tuple(observations), gaps=tuple(gaps),
                           decisions=tuple(GateDecision(o.metric, True) for o in observations))


def _inv(goals, question="q", clarification=None, comparisons=(), defaulted=False, dropped=()):
    compilation = Compilation(question, "test", clarification=clarification, dropped=dropped)
    return SimpleNamespace(question=question, as_of=date(2026, 11, 24), status="COMPLETE",
                           stop_reason="goals_resolved", compilation=compilation,
                           goals=tuple(goals), comparisons=tuple(comparisons),
                           scenario_defaulted=defaulted, answer="raw answer text")


def _clean(inv, plain, labels):
    shown, problems = p.audit(inv, plain, labels)
    assert problems == [], problems
    return shown


# ---------------------------------------------------------------- formats

@pytest.mark.parametrize("value, shown", [
    (Decimal("818042000000"), "USD 818.0bn"), (-1499278201.29, "USD -1.5bn"),
    (Decimal("71000000"), "USD 71.0m"), (Decimal("0"), "USD 0"), (12345, "USD 12,345"),
    (Decimal("1234567890123"), "USD 1,234.6bn")])
def test_usd(value, shown):
    assert p.usd(value) == shown


@pytest.mark.parametrize("value, unit, signed, shown", [
    (1.9256, "x", False, "1.93x"), (0.1234, "percent", False, "12.3%"),
    (27.82, "pp", True, "+27.8 pp"), (-5.23, "pp", True, "-5.2 pp"), (17.0, "days", False,
                                                                       "17.0 days"),
    (Decimal("5"), "count", False, "5"), (17.8, "%", False, "17.8%")])
def test_number(value, unit, signed, shown):
    assert p.number(value, unit, signed) == shown


def test_small_formats():
    assert p.limit(10.0, "pp") == "10 pp" and p.limit(1.5, "x") == "1.5x"
    assert p.limit(-3.0, "pp") == "-3 pp" and p.limit(0.0, "USD") == "USD 0"
    assert p.day(date(2026, 2, 6)) == "6 Feb 2026" and p.day("2026-07-29") == "29 Jul 2026"
    assert p.lower_first("Total assets") == "total assets"
    assert p.lower_first("EBIT margin") == "EBIT margin"
    assert p.verb("Total assets") == "were" and p.verb("Revenue") == "was"
    assert p.verb("Gross profit", present_tense=True) == "is"
    assert p.joined(["a"]) == "a" and p.joined(["a", "b", "c"]) == "a, b and c"
    assert p.first_two(["a", "b", "c"]) == "a and b, with more below"
    assert p.first_two(["a"]) == "a" and p.first_two([]) == ""
    assert p.growth_parts("x: FIRED; inventory +48.9%, cogs +21.1%; recomputed",
                          ("inventory", "cogs")) == [48.9, 21.1]
    assert p.growth_parts("no parts here", ("inventory", "cogs")) is None


def test_labels(labels):
    assert labels.badge("VERIFIED").tip.startswith("Read from the filing text")
    assert labels.badge("UNKNOWN_KEY").label == "Unknown key"
    assert labels.section("ITEM_7") == "MD&A"
    assert labels.section("ITEM_8/NOTE_3") == "notes to the financial statements"
    assert labels.metric("total_assets") == "Total assets"
    assert labels.metric("some_new_metric") == "Some new metric"
    spec, c = labels.condition("R2.inventory_growth_gap")
    assert spec.signal_id == "R2" and c.measure == "growth_gap"
    assert labels.condition("R11.revenue_growth")[1].measure == "growth"
    assert labels.condition("revenue") is None


# ---------------------------------------------------------------- answer kinds

def test_fact(labels):
    o = _obs("total_assets", Decimal("818042000000"))
    inv = _inv([_goal(Goal("MSFT", "latest", "fact", "total_assets"), [o])])
    plain = p.present(inv, labels)
    assert plain.kind == "fact"
    assert plain.headline == ("Microsoft, FY2026 (10-K filed 29 Jul 2026): total assets were "
                              "USD 818.0bn.")
    (f,) = plain.findings
    assert [b.key for b in f.badges] == ["VERIFIED"]
    assert f.evidence[-1].kind == "source" and f.evidence[-1].url == URL
    assert any("basis: AGREE" in t for t in plain.technical)
    assert _clean(inv, plain, labels) > 0


def test_fact_restated_and_single_channel(labels):
    o = _obs("revenue", Decimal("100000000"), flags=("SINGLE_CHANNEL", "RESTATED"),
             original_value=Decimal("90000000"), original_accession="acc-0")
    inv = _inv([_goal(Goal("MSFT", 2026, "fact", "revenue"), [o])])
    plain = p.present(inv, labels)
    (f,) = plain.findings
    assert [b.key for b in f.badges] == ["SINGLE_CHANNEL", "RESTATED"]
    assert "first reported as USD 90.0m" in f.text
    _clean(inv, plain, labels)


def test_unavailable_goal(labels):
    inv = _inv([_goal(Goal("MSFT", 2024, "fact", "inventory"), status="GAP",
                      reason="NO_OBSERVATION")])
    plain = p.present(inv, labels)
    assert plain.headline == labels.text["notes"]["no_value"]
    (f,) = plain.unavailable
    assert f.title == "Inventory" and "FY2024" in f.text and "not reported" in f.text
    assert f.badges[0].key == "GAP"
    _clean(inv, plain, labels)


def test_credit_not_meaningful(labels):
    o = _obs("current_ratio", None, unit="x", classification="CALCULATION",
             flags=("NOT_MEANINGFUL",), detail="denominator 0")
    inv = _inv([_goal(Goal("MSFT", "latest", "credit", "current_ratio"), [o])])
    plain = p.present(inv, labels)
    assert "current ratio is not meaningful" in plain.headline
    assert "NOT_MEANINGFUL" in [b.key for b in plain.findings[0].badges]
    _clean(inv, plain, labels)


def test_statements_with_review(labels):
    obs = [_obs("I9", Decimal("0"), classification="CALCULATION"),
           _obs("I10", Decimal("0"), classification="CALCULATION",
                flags=("CORRECTED_VALUES",)),
           _obs("I11", Decimal("71000000"), classification="CALCULATION", flags=("REVIEW",))]
    inv = _inv([_goal(Goal("MSFT", 2026, "statements"), obs)])
    plain = p.present(inv, labels)
    assert plain.headline.endswith(": the statements tie out.")
    assert plain.findings[0].text == labels.text["statements"]["holds"]
    assert "USD 71.0m" in plain.findings[2].text
    assert "REVIEW" in [b.key for b in plain.findings[2].badges]
    assert any("retained earnings roll forward" in n for n in plain.notes)
    assert labels.text["flags"]["CORRECTED_VALUES"] in plain.notes
    _clean(inv, plain, labels)


def _model(metric, value, unit="USD", **kw):
    return _obs(metric, value, unit=unit, classification="CALCULATION_FROM_MODEL_ASSUMPTION",
                fiscal_year=2027, fiscal_period_end=None, scenario="Severe", channels=(),
                basis="Severe scenario from FY2026 (checks F1-F5 pass)", **kw)


def _origin():
    return _goal(Goal("MSFT", 2026, "statements"),
                 [_obs("I9", Decimal("0"), classification="CALCULATION")], requested=False)


def test_forecast_cites_its_origin(labels):
    o = _model("revenue", 765182187649.41, flags=("CASH_SHORTFALL", "LEASE_COST_EQUALS_PAYMENT"))
    inv = _inv([_goal(Goal("MSFT", "latest", "forecast", "revenue", "Severe"), [o],
                      gaps=(("dividends", "NOT_MODELLED"),)), _origin()], defaulted=True)
    plain = p.present(inv, labels)
    assert plain.headline == ("Microsoft, Severe scenario, from the FY2026 10-K (filed 29 Jul "
                              "2026): revenue is projected at USD 765.2bn in FY2027.")
    (f,) = plain.findings
    assert f.badges[0].key == "MODEL"
    assert f.evidence[0].source == "model from the FY2026 10-K filed 29 Jul 2026"
    assert labels.text["flags"]["CASH_SHORTFALL"] in plain.notes
    assert labels.text["notes"]["base_default"] in plain.notes
    assert not any("LEASE_COST" in n for n in plain.notes)  # technical only
    assert any("Gap: dividends: NOT_MODELLED" in t for t in plain.technical)
    _clean(inv, plain, labels)


def test_liquidity_with_conditional_debt(labels):
    liq = [_model("liquidity_headroom", -1499278201.29), _model("liquidity_shortfall",
                                                                1499278201.29)]
    debt = [_model("scoped_dscr", 0.6222, unit="x"),
            _model("cash_available_for_debt_service", 1059163533.87)]
    gaps = (("comprehensive_dscr", "COMPREHENSIVE_DSCR_WITHHELD: Not reconciled."),
            ("finance_lease_payments", "NOT_APPLIED: the filer has no such obligation"))
    inv = _inv([_goal(Goal("MSFT", "latest", "liquidity", "liquidity_headroom", "Severe"),
                      liq[:1]),
                _goal(Goal("MSFT", "latest", "liquidity", "liquidity_shortfall", "Severe"),
                      liq[1:]),
                _goal(Goal("MSFT", "latest", "debt", "", "Severe", conditional=True), debt,
                      gaps=gaps), _origin()])
    plain = p.present(inv, labels)
    assert plain.kind == "liquidity+debt"
    assert "liquidity falls short by USD 1.5bn in FY2027" in plain.headline
    assert "scheduled debt-service coverage is projected at 0.62x" in plain.headline
    group = labels.text["debt_group"]["conditional"]
    debt_findings = [f for f in plain.findings if f.group == group]
    assert [f.title for f in debt_findings] == ["Scheduled debt-service coverage",
                                                "Cash available for debt service",
                                                "Comprehensive debt-service coverage"]
    assert debt_findings[-1].badges[0].key == "WITHHELD"
    assert "Not reconciled." in debt_findings[-1].text
    assert any(n.startswith("Finance lease payments: does not apply") for n in plain.notes)
    _clean(inv, plain, labels)


def test_conditional_debt_skipped(labels):
    inv = _inv([_goal(Goal("MSFT", "latest", "liquidity", "liquidity_shortfall", "Base"),
                      [replace(_model("liquidity_shortfall", 0.0), scenario="Base")]),
                _goal(Goal("MSFT", "latest", "debt", "", "Base", conditional=True),
                      status="SKIPPED", reason="CONDITION_NOT_MET")])
    plain = p.present(inv, labels)
    assert "does not fall short" in plain.headline
    (f,) = plain.unavailable
    assert labels.text["conditional_skipped"] in f.text and f.badges[0].key == "SKIPPED"
    _clean(inv, plain, labels)


def _quote(text, section="ITEM_7"):
    return Quote(f"{ACC}:{section}:0001", ACC, text, section, 0.62, "ab" * 32)


def test_explain_with_quotes(labels):
    fact = _obs("revenue", Decimal("716924000000"))
    change = _obs("revenue_change", Decimal("78965000000"), classification="CALCULATION",
                  quotes=(_quote("Sales increased 12% in 2025, compared to the prior year."),))
    inv = _inv([_goal(Goal("MSFT", "latest", "explain", "revenue"), [fact, change])])
    plain = p.present(inv, labels)
    assert plain.headline == ("Microsoft, FY2026 (10-K filed 29 Jul 2026): revenue was "
                              "USD 716.9bn, up USD 79.0bn from FY2025.")
    why = plain.findings[1]
    assert why.title == "Why revenue changed" and "QUOTED" in [b.key for b in why.badges]
    (e,) = why.evidence
    assert e.kind == "quote" and e.source == "10-K FY2026, MD&A" and e.url == URL
    _clean(inv, plain, labels)


def test_explain_gap_shows_the_verified_figure(labels):
    fact = _obs("revenue", Decimal("716924000000"))
    inv = _inv([_goal(Goal("MSFT", "latest", "explain", "revenue"), status="EVIDENCE_GAP",
                      reason="EVIDENCE_GAP"),
                _goal(Goal("MSFT", "latest", "fact", "revenue"), [fact], requested=False)])
    plain = p.present(inv, labels)
    assert "revenue was USD 716.9bn, but the filing does not explain" in plain.headline
    figure, why = plain.findings
    assert figure.title == "Revenue" and not plain.unavailable
    assert (why.title, why.text) == ("Why revenue changed", labels.text["evidence"]["gap"])
    assert [b.key for b in why.badges] == ["EVIDENCE_GAP"]
    _clean(inv, plain, labels)


def _risk_obs(metric, value, unit, basis, flags=(), quotes=(), low=None):
    return _obs(metric, value, unit=unit, classification="CALCULATION", basis=basis,
                channels=(), flags=flags, quotes=quotes, low_confidence=low)


def _risk_inv():
    low = LowConfidenceSentence(_quote("Inventory levels remained elevated."), -8.2, 5.263,
                                "Low confidence: may not explain the movement")
    obs = [
        _obs("signals_fired", Decimal("3"), unit="count", classification="CALCULATION",
             channels=()),
        _risk_obs("R2.dio_change", -0.53, "days", "R2 Inventory build: DIO up, test > 10 days: "
                                                  "NOT_FIRED; recomputed in Decimal"),
        _risk_obs("R2.inventory_growth_gap", 27.82, "pp",
                  "R2 Inventory build: inventory growth minus COGS growth, test > 10 pp: FIRED; "
                  "inventory +48.9%, cogs +21.1%; recomputed in Decimal",
                  flags=("EVIDENCE_GAP",), low=low),
        _risk_obs("R3.capex_to_revenue_change", 12.03, "pp", "R3: FIRED",
                  quotes=(_quote("We will continue to invest in capital expenditures."),)),
        _risk_obs("R3.capex_to_revenue_vs_median", 1.9257, "x", "R3: FIRED",
                  quotes=(_quote("We will continue to invest in capital expenditures."),)),
        _risk_obs("R11.revenue_growth", 17.8, "pp", "R11: FIRED; revenue +17.8%",
                  flags=("EVIDENCE_GAP",)),
    ]
    gaps = tuple((s, "NOT_FIRED") for s in ("R1", "R4", "R5", "R6", "R7", "R9", "R10")) + (
        ("R8", "GAP: NO_COGS the filer reports no cogs line in FY2026"),)
    return _inv([_goal(Goal("MSFT", "latest", "risk"), obs, reason="SIGNALS_EVALUATED",
                       gaps=gaps)])


def test_risk(labels):
    inv = _risk_inv()
    plain = p.present(inv, labels)
    assert plain.kind == "risk"
    assert plain.headline == ("Microsoft, FY2026 (10-K filed 29 Jul 2026): 3 of 11 risk "
                              "signals fired.")
    names = [f.title for f in plain.findings]
    assert names == [labels.signals["R2"], labels.signals["R3"], labels.signals["R11"]]
    r2, r3, r11 = plain.findings
    assert r2.text == ("Inventory grew 48.9% while cost of sales grew 21.1% (gap +27.8 pp; flag "
                       "above 10 pp).")
    assert [e.kind for e in r2.evidence] == ["gap", "low", "source"]
    assert r2.evidence[1].text == "Inventory levels remained elevated."
    assert len(r2.more) == 1 and "not flagged" in r2.more[0]  # the measure that did not fire
    assert r3.text.count("flag above") == 2 and "1.93x" in r3.text
    assert [e.kind for e in r3.evidence] == ["quote", "source"]  # one quote shown once
    assert r11.text.startswith("Revenue grew 17.8% while")
    assert labels.signals["R2"] in r11.text and labels.signals["R3"] in r11.text
    assert r11.more and "above 0%" in r11.more[0]
    assert plain.checked == tuple(labels.signals[s] for s in ("R1", "R4", "R5", "R6", "R7",
                                                               "R9", "R10"))
    (gap,) = plain.unavailable
    assert gap.title == labels.signals["R8"] and labels.reason("NO_COGS") in gap.text
    _clean(inv, plain, labels)


def test_risk_growth_gap_without_recorded_parts(labels):
    inv = _risk_inv()
    goal = inv.goals[0]
    obs = tuple(replace(o, basis="R2: FIRED") if o.metric == "R2.inventory_growth_gap" else o
                for o in goal.observations)
    inv.goals = (replace_goal(goal, obs),)
    plain = p.present(inv, labels)
    assert plain.findings[0].text.startswith("Inventory grew faster than cost of sales by")
    _clean(inv, plain, labels)


def replace_goal(goal, observations):
    return SimpleNamespace(**{**vars(goal), "observations": observations})


def test_comparison(labels):
    a = _obs("revenue", Decimal("331839000000"))
    b = _obs("revenue", Decimal("716924000000"), issuer="AMZN", fiscal_year=2025,
             filed=date(2026, 2, 6))
    c = Comparison("revenue", "fact", a, b, a.value - b.value, "USD", "CALCULATION")
    inv = _inv([_goal(Goal("MSFT", "latest", "fact", "revenue"), [a]),
                _goal(Goal("AMZN", "latest", "fact", "revenue"), [b])], comparisons=[c])
    plain = p.present(inv, labels)
    assert plain.kind == "comparison"
    assert plain.headline == ("Microsoft's FY2026 revenue (USD 331.8bn) was USD 385.1bn lower "
                              "than Amazon's FY2025 revenue (USD 716.9bn).")
    assert labels.text["notes"]["fiscal_years"] in plain.notes
    _clean(inv, plain, labels)


def test_clarification_with_suggestions(labels):
    c = Clarification("MULTIPLE_PERIODS", "two_periods", "A goal covers one fiscal period.",
                      "what was operating income in fy2023 and fy2024")
    inv = _inv([], clarification=c)
    plain = p.present(inv, labels)
    assert plain.kind == "clarification" and plain.headline == labels.text["clarify"]["intro"]
    entry = labels.text["clarify"]["reasons"]["MULTIPLE_PERIODS"]
    assert plain.clarify.question == entry["ask"]
    assert plain.clarify.suggestions == tuple(entry["try"])
    assert any("MULTIPLE_PERIODS" in t for t in plain.technical)
    other = p.present(_inv([], clarification=replace(c, reason="SOMETHING_NEW")), labels)
    assert other.clarify.question == labels.text["clarify"]["other"]["ask"]
    _clean(inv, plain, labels)


def test_dropped_clause_note(labels):
    o = _obs("revenue", Decimal("100000000"))
    question = "What was revenue? and 42 bananas"
    inv = _inv([_goal(Goal("MSFT", "latest", "fact", "revenue"), [o])], question=question,
               dropped=(DroppedClause("and 42 bananas", ("42",)),))
    plain = p.present(inv, labels)
    assert any("“and 42 bananas”" in n for n in plain.notes)
    _clean(inv, plain, labels)  # the user's own words are not a new number


def test_plain_text(labels):
    text = p.plain_text(p.present(_risk_inv(), labels))
    assert text.splitlines()[0].startswith("Microsoft, FY2026")
    assert "Possibly related (low confidence, not evidence)" in text
    assert "Checked, not flagged:" in text


def test_plain_text_names_a_group_once():
    plain = p.PlainAnswer("debt", "Headline.", findings=(
        p.Finding("A", "One.", group="Debt service"), p.Finding("B", "Two.", group="Debt service")))
    assert p.plain_text(plain).splitlines() == ["Headline.", "Debt service:", "- A: One.",
                                                "- B: Two."]


# ---------------------------------------------------------------- the audit catches new numbers

def test_audit_flags_a_new_number_and_a_changed_quote(labels):
    inv = _risk_inv()
    plain = p.present(inv, labels)
    f = plain.findings[0]
    tampered = replace(plain, findings=(replace(f, text=f.text + " Up 99.9 pp."),
                                        *plain.findings[1:]))
    _, problems = p.audit(inv, tampered, labels)
    assert len(problems) == 1 and "'99.9'" in problems[0]
    r3 = plain.findings[1]
    changed = replace(r3, evidence=(replace(r3.evidence[0], text="We will invest more."),
                                    *r3.evidence[1:]))
    _, problems = p.audit(inv, replace(plain, findings=(f, changed, *plain.findings[2:])),
                          labels)
    assert len(problems) == 1 and "not verbatim" in problems[0]


def test_allowed_numbers_are_built_from_the_records(labels):
    inv = _risk_inv()
    allowed = p.allowed_numbers(inv, labels)
    assert {"27.8", "48.9", "21.1", "10", "1.93", "2026", "29"} <= allowed
    assert "99.9" not in allowed


# ---------------------------------------------------------------- the audit sample

def test_e5_sample_round_robin_by_group():
    rows = [{"id": f"Q{i:03d}", "author": "canonical", "cell": c}
            for i, c in enumerate(["a1", "a2", "b1", "c1", "c2", "c3"], 1)]
    rows.append({"id": "Q099", "author": "vineet", "cell": "a1"})
    grid = {"a1": "A", "a2": "A", "b1": "B", "c1": "C", "c2": "C", "c3": "C"}
    picked = [r["id"] for r in p.e5_sample(rows, grid, 5)]
    assert picked == ["Q001", "Q003", "Q004", "Q002", "Q005"]
    assert len(p.e5_sample(rows, grid, 50)) == 6


# ---------------------------------------------------------------- real runs (local only)

needs_models = pytest.mark.skipif(not (ROOT / "models" / "intent_v3.joblib").exists(),
                                  reason="models are git-ignored; run where they are pinned")


@needs_models
def test_clarification_suggestions_compile_for_a_validation_company(labels):
    """Each suggested rephrasing is a question the compiler accepts (no clarification)."""
    from finagent.config import load_universe
    from finagent.planner.compile import (CompilerResources, compile_question,
                                          load_trained_parser)

    resources = CompilerResources.load()
    parser = load_trained_parser(resources)
    ticker = sorted(t for t, s in load_universe().items() if s == "validation")[0]
    clarify = labels.text["clarify"]
    suggestions = {s for e in [*clarify["reasons"].values(), clarify["other"]]
                   for s in e.get("try", ())}
    for s in suggestions:
        c = compile_question(s, parser, resources, ticker)
        assert c.clarification is None and c.goals, (s, c.clarification)


@needs_models
def test_real_risk_answer_passes_the_audit(labels):
    from finagent.config import load_universe
    from finagent.planner.investigate import investigate
    from finagent.ui.data import load_ui_config

    cfg = load_ui_config()
    ticker = sorted(t for t, s in load_universe().items() if s == "validation")[0]
    inv = investigate("What are the main risk signals?", cfg.as_of_default,
                      selected_issuer=ticker)
    plain = p.present(inv, labels)
    shown, problems = p.audit(inv, plain, labels)
    assert shown > 0 and problems == []
