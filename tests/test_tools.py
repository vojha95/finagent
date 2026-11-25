"""Planner tools (Phase 6.3): as_of on every method, explicit-key caches, outcome mapping,
the Decimal second way for ratios, BC25, and (7.3) risk signals and explanations with quotes
checked by the gate."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest
from model_support import AS_OF, FILINGS, make_set, value_obs
from statements_support import millions, obs, pending

from finagent.config import HeldOutError, load_planner_config
from finagent.credit.debt_service import debt_service_table
from finagent.model.ratios import BY_ID
from finagent.planner.gate import gate
from finagent.planner.loop import gate_context
from finagent.records import Goal, IdentityResult, StatementsCheck
from finagent.statements.replay import FULL, NO_DOCUMENT, SINGLE_CHANNEL_INPUTS
from finagent.tools import (FinAgentTools, ToolError, check_as_of, decimal_ratio, fact_outcome,
                            resolve_target, same_ratio, single_channel_outcome)
from finagent.verify.reconcile import Reconciliation


class FakeStore:
    """Every filing, whatever the date: the tools must apply as_of themselves."""

    def load_filings(self, issuer, as_of):
        return FILINGS


def tools(statement_set=None, view=FULL, checks=None):
    s = statement_set or make_set()
    t = FinAgentTools(FakeStore(), None, {"SYNT": "validation", "HOLD": "held_out"}, view=view)
    t.reconciliations[("SYNT", AS_OF, view)] = Reconciliation(
        "SYNT", AS_OF, s, s, (), (), checks if checks is not None else {2025: CHECK})
    return t


CHECK = StatementsCheck("SYNT", 2025, "VERIFIED", "I9 and I10 within tolerance", (
    IdentityResult("SYNT", 2025, "I9", Decimal(0), Decimal(0), "PASS"),
    IdentityResult("SYNT", 2025, "I10", Decimal(0), Decimal(0), "PASS"),
    IdentityResult("SYNT", 2025, "I11", millions(3), Decimal(0), "REVIEW"),
    IdentityResult("SYNT", 2025, "I12", None, None, "NOT_COMPUTABLE")))


# ---------------------------------------------------------------- helpers

def test_check_as_of_needs_a_date():
    assert check_as_of(AS_OF) == AS_OF
    for bad in (datetime(2026, 11, 24), "2026-11-24", None):
        with pytest.raises(ToolError):
            check_as_of(bad)


def test_resolve_target():
    assert resolve_target(2025, None) == 2026
    assert resolve_target(2025, "+2") == 2027 and resolve_target(2025, 2028) == 2028


def test_fact_outcome_maps_statuses():
    assert (fact_outcome(None).status, fact_outcome(None).reason) == ("GAP", "NO_OBSERVATION")
    ok = fact_outcome(obs("revenue", millions(5)))
    assert ok.status == "VERIFIED" and ok.observations[0].value == millions(5)
    assert ok.observations[0].validated
    tag = obs("revenue", 5, status="SINGLE_CHANNEL", channels=("tag",), reason="TAG_ONLY")
    assert (fact_outcome(tag).status, fact_outcome(tag).reason) == ("MISSING_PRIMARY",
                                                                    "DOC_MISSING")
    assert fact_outcome(pending("revenue", 5)).reason == "TAG_MISSING"
    assert fact_outcome(obs("revenue", 5, status="DISAGREE")).status == "CONFLICT"
    assert fact_outcome(obs("revenue", 5, status="INVALID")).status == "INVALID"
    gap = fact_outcome(obs("revenue", None, reason="NOT_REPORTED"))
    assert (gap.status, gap.reason, gap.observations) == ("GAP", "NOT_REPORTED", ())


def test_single_channel_outcome_needs_8_7_acceptance():
    doc = obs("inventory", 7, status="SINGLE_CHANNEL", channels=("document",), reason="DOC_ONLY")
    ok = single_channel_outcome(doc, "document")
    assert ok.status == "VERIFIED" and "SINGLE_CHANNEL" in ok.observations[0].flags
    assert not ok.observations[0].validated
    assert single_channel_outcome(pending("inventory", 7), "document").status == "GAP"
    assert single_channel_outcome(doc, "tag").reason == "NOT_TAG_ONLY"
    assert single_channel_outcome(None, "tag").reason == "NO_OBSERVATION"


def test_decimal_ratio_and_same_ratio():
    d = {"current_assets": Decimal(3), "current_liabilities": Decimal(2)}
    assert decimal_ratio(BY_ID["current_ratio"], d, {}, None, {}) == 1.5
    assert decimal_ratio(BY_ID["current_ratio"], {**d, "current_liabilities": Decimal(0)},
                         {}, None, {}) is None
    assert decimal_ratio(BY_ID["revenue_growth"], {"revenue": Decimal(11),
                                                   "revenue_prior": Decimal(10)},
                         {}, None, {}) == pytest.approx(0.1)
    dso = decimal_ratio(BY_ID["dso"], {"receivables": Decimal(80), "revenue": Decimal(1000)},
                        {"receivables": Decimal(70)}, 365.0, {})
    assert dso == pytest.approx(27.375)
    assert decimal_ratio(BY_ID["cash_conversion_cycle"], {}, {}, None,
                         {"dso": 30.0, "dio": 20.0, "dpo": 10.0}) == 40.0
    assert same_ratio(1.0, 1.0 + 1e-12) and not same_ratio(1.0, 1.01)
    assert not same_ratio(1.0, None)


# ---------------------------------------------------------------- evidence and as_of

def test_filings_apply_as_of_and_refuse_held_out():
    t = tools()
    assert [f.fiscal_year for f in t.filings("SYNT", date(2024, 3, 1))] == [2021, 2022, 2023]
    assert t.resolve_year("SYNT", "latest", AS_OF) == 2025
    assert t.resolve_year("SYNT", "latest", date(2024, 3, 1)) == 2023
    assert t.resolve_year("SYNT", 2019, AS_OF) is None
    assert t.resolve_year("SYNT", "latest", date(2020, 1, 1)) is None
    with pytest.raises(HeldOutError):
        t.filings("HOLD", AS_OF)
    with pytest.raises(ToolError):
        t.reported_verified("SYNT", 2025, "revenue", as_of="2026-11-24")


def test_reconciliation_cache_is_keyed_by_issuer_as_of_and_view():
    t = tools()
    assert t.statement_set("SYNT", AS_OF) is t.reconciliations[("SYNT", AS_OF, FULL)] \
        .statement_set
    with pytest.raises(ToolError):
        t.scenario("No such scenario")


# ---------------------------------------------------------------- fact actions

def test_reported_verified_latest_and_missing_year():
    t = tools()
    out = t.reported_verified("SYNT", "latest", "revenue", as_of=AS_OF)
    assert out.status == "VERIFIED" and out.observations[0].value == millions(1000)
    assert out.observations[0].accession == FILINGS[-1].accession
    gap = t.reported_verified("SYNT", 2019, "revenue", as_of=AS_OF)
    assert (gap.status, gap.reason) == ("GAP", "PERIOD_NOT_AVAILABLE")
    assert t.reported_verified("SYNT", 2025, "goodwill", as_of=AS_OF).reason == "NO_OBSERVATION"


def test_tag_only_and_document_only_bc25():
    tag = value_obs(2025, "inventory", 50, status="SINGLE_CHANNEL", channels=("tag",),
                    reason="TAG_ONLY")
    doc = value_obs(2025, "accounts_payable", 70, status="SINGLE_CHANNEL",
                    channels=("document",), reason="DOC_ONLY")
    held = value_obs(2025, "accounts_receivable", 80, status="SINGLE_CHANNEL", accepted=False,
                     channels=("document",), reason="NO_IDENTITY_CHECK")
    t = tools(make_set({(2025, "inventory"): tag, (2025, "accounts_payable"): doc,
                        (2025, "accounts_receivable"): held}))
    assert t.reported_verified("SYNT", 2025, "inventory", as_of=AS_OF).reason == "DOC_MISSING"
    out = t.reported_tag_only("SYNT", 2025, "inventory", as_of=AS_OF)
    assert out.status == "VERIFIED" and out.observations[0].flags == ("SINGLE_CHANNEL",)
    out = t.reported_document_only("SYNT", 2025, "accounts_payable", as_of=AS_OF)
    assert out.status == "VERIFIED" and out.observations[0].value == millions(70)
    out = t.reported_document_only("SYNT", 2025, "accounts_receivable", as_of=AS_OF)
    assert (out.status, out.reason, out.observations) == ("GAP", "NO_IDENTITY_CHECK", ())


# ---------------------------------------------------------------- statements

def test_reconcile_returns_residuals_with_review_flag():
    out = tools().reconcile("SYNT", 2025, as_of=AS_OF)
    assert out.status == "VERIFIED"
    by_id = {o.metric: o for o in out.observations}
    assert set(by_id) == {"I9", "I10", "I11"}  # NOT_COMPUTABLE left out
    assert by_id["I9"].value == Decimal(0) and by_id["I9"].classification == "CALCULATION"
    assert by_id["I11"].flags == ("REVIEW",) and by_id["I11"].value == millions(3)
    assert tools(checks={}).reconcile("SYNT", 2025, as_of=AS_OF).reason == "NO_FINAL_CHECK"
    flagged = tools(view=NO_DOCUMENT).reconcile("SYNT", 2025, as_of=AS_OF)  # #144
    assert all(SINGLE_CHANNEL_INPUTS in o.flags for o in flagged.observations)
    assert all(SINGLE_CHANNEL_INPUTS not in o.flags for o in out.observations)
    failed = StatementsCheck("SYNT", 2025, "INVALID", "I9 fails", ())
    out = tools(checks={2025: failed}).reconcile("SYNT", 2025, as_of=AS_OF)
    assert (out.status, out.reason) == ("INVALID", "FINAL_CHECK_INVALID")


# ---------------------------------------------------------------- forecast, liquidity

def test_project_answers_from_the_cached_forecast():
    t = tools()
    out = t.project("SYNT", "latest", "Base", None, as_of=AS_OF)
    assert out.status == "VERIFIED"
    f = t.forecasts[("SYNT", AS_OF, FULL, "Base")]
    lines = {y.fiscal_year: y.lines for y in f.years}[2026]
    by_metric = {o.metric: o for o in out.observations}
    assert by_metric["revenue"].value == lines["revenue"] and by_metric["revenue"].fiscal_year == 2026
    assert by_metric["free_cash_flow"].value == lines["cfo"] - lines["capex"]
    o = by_metric["revenue"]
    assert o.classification == "CALCULATION_FROM_MODEL_ASSUMPTION" and o.scenario == "Base"
    assert o.fiscal_period_end is None and o.accession == FILINGS[-1].accession
    assert SINGLE_CHANNEL_INPUTS not in o.flags
    assert t.project("SYNT", "latest", "Base", "+30", as_of=AS_OF).reason == \
        "TARGET_NOT_PROJECTED"
    liq = t.liquidity("SYNT", "latest", "Base", None, as_of=AS_OF)
    assert {o.metric for o in liq.observations} | {m for m, _ in liq.gaps} == \
        set(t.vocabulary.metrics["liquidity"])


def test_a_view_hiding_a_channel_flags_model_outputs():
    t = tools(view=NO_DOCUMENT)
    out = t.project("SYNT", "latest", "Base", None, as_of=AS_OF)
    assert all(SINGLE_CHANNEL_INPUTS in o.flags for o in out.observations)
    assert ("SYNT", AS_OF, NO_DOCUMENT, "Base") in t.forecasts


# ---------------------------------------------------------------- credit

def test_credit_ratios_values_gaps_and_not_meaningful():
    s = make_set({(2024, "accounts_receivable"): 70}, bases={"ebit_basis": "pretax_income"})
    out = tools(s).credit_ratios("SYNT", 2025, as_of=AS_OF)
    by_id = {o.metric: o for o in out.observations}
    gaps = dict(out.gaps)
    assert out.status == "VERIFIED" and by_id["ebit_to_gross_interest"].value == 14.0
    assert by_id["ebit_to_gross_interest"].unit == "x"
    assert by_id["dso"].unit == "days" and by_id["dso"].value == pytest.approx(27.375)
    assert by_id["net_margin"].unit == "percent"
    assert gaps["current_ratio"].startswith("INPUT_GAP")
    assert "recomputed in Decimal" in by_id["funded_debt_to_equity"].basis
    nm = make_set({(2025, "interest_expense"): 0}, bases={"ebit_basis": "pretax_income"})
    o = {o.metric: o for o in tools(nm).credit_ratios("SYNT", 2025, as_of=AS_OF)
         .observations}["ebit_to_gross_interest"]
    assert o.value is None and "NOT_MEANINGFUL" in o.flags and o.detail


def test_decimal_ratio_balance_change_leaves_out_unreported_lines():  # #156
    spec = BY_ID["operating_working_capital_change"]
    values = {"receivables": Decimal(120), "payables": Decimal(50)}
    opening = {"receivables": Decimal(100), "payables": Decimal(40)}
    assert decimal_ratio(spec, values, opening, None, {}) == 10.0  # (120-100) - (50-40)
    assert decimal_ratio(spec, values, {"receivables": Decimal(100)}, None, {}) is None
    assert decimal_ratio(spec, {}, {}, None, {}) is None


# ---------------------------------------------------------------- debt service (8.12)

def test_debt_service_scoped_answers_the_target_year():
    t = tools()
    out = t.debt_service_scoped("SYNT", "latest", "Base", None, as_of=AS_OF)
    lines = t.forecast("SYNT", "Base", AS_OF).years[0].lines
    by_metric = {o.metric: o for o in out.observations}
    service = (lines["debt_principal"] + lines["debt_interest"]
               + lines["finance_lease_principal"] + lines["finance_lease_interest"])
    assert (out.status, out.reason) == ("VERIFIED", "SCOPED_DEBT_SERVICE")
    dscr = by_metric["scoped_dscr"]
    available = (lines["cfo"] - lines["capex"] + lines["debt_interest"]
                 + lines["finance_lease_interest"])  # BC27
    assert dscr.value == pytest.approx(available / service)
    assert by_metric["cash_available_for_debt_service"].value == pytest.approx(available)
    assert dscr.unit == "x" and by_metric["debt_service"].unit == "USD"
    assert "BC27" in dscr.basis and "negative coverage" not in dscr.basis
    assert by_metric["debt_service"].value == pytest.approx(service)
    assert by_metric["finance_lease_payments"].value == pytest.approx(
        lines["finance_lease_principal"] + lines["finance_lease_interest"])
    assert dscr.classification == "CALCULATION_FROM_MODEL_ASSUMPTION"
    assert (dscr.fiscal_year, dscr.fiscal_period_end, dscr.scenario) == (2026, None, "Base")
    assert dscr.accession == FILINGS[-1].accession
    assert "gross of refinancing" in dscr.basis and "excludes short-term borrowings" in dscr.basis
    assert dict(out.gaps)["comprehensive_dscr"].startswith("COMPREHENSIVE_DSCR_WITHHELD: ")
    assert t.debt_service_scoped("SYNT", "latest", "Base", "+30", as_of=AS_OF).reason == \
        "TARGET_NOT_PROJECTED"
    with pytest.raises(ToolError):
        t.debt_service_scoped("SYNT", "latest", "Base", None, as_of=datetime(2026, 11, 24))


def test_debt_service_answers_pass_the_gate():  # #155
    t = tools()
    goal = Goal("SYNT", "latest", "debt", "", "Severe", "+2", True)
    out = t.debt_service_scoped("SYNT", "latest", "Severe", "+2", as_of=AS_OF)
    gated, decisions = gate(out, gate_context(goal, AS_OF, t, load_planner_config(),
                                              t.vocabulary))
    assert gated.status == "VERIFIED" and decisions and all(d.accepted for d in decisions)
    assert {o.metric for o in gated.observations} >= {"scoped_dscr", "debt_service"}


def test_debt_service_scoped_maps_invalid_and_not_applied(monkeypatch):
    import finagent.tools as T

    t = tools()
    for status, reason in (("INVALID", "RECOMPUTATION_MISMATCH"), ("NOT_APPLIED", "NOT_APPLIED")):
        monkeypatch.setattr(T, "debt_service_table", lambda f, s=status: [
            replace(row, status=s, scoped_dscr=None) for row in debt_service_table(f)])
        out = t.debt_service_scoped("SYNT", "latest", "Base", None, as_of=AS_OF)
        assert (out.status, out.reason, out.observations) == (
            "INVALID" if status == "INVALID" else "GAP", reason, ())


# ---------------------------------------------------------------- risk signals, explain (7.3)

def evidence_tools(statement_set=None, view=FULL):
    """tools() with a retriever over the synthetic FY2025 10-K and a pinned-chunk gate check
    built from the same document (no evidence read)."""
    from retrieval_support import EVIDENCE

    from finagent.config import load_retrieval_config
    from finagent.retrieval.index import Retriever, build_index
    from finagent.retrieval.pinned import PinnedChunks, quote_checker

    t = tools(statement_set, view)
    config = load_retrieval_config()
    index = build_index(FILINGS[-1], EVIDENCE.encode(), config)
    t.retriever = Retriever(FakeStore(), t.universe, config)
    t.retriever.indexes[FILINGS[-1].accession] = index
    pinned = PinnedChunks(FakeStore(), config)
    pinned.cache[FILINGS[-1].accession] = {c.chunk_id: c for c in index.chunks}
    ev = t.signal_config.evidence

    def checks(kind):
        sections = {"risk": ev.sections, "explain": ev.explain_sections}.get(kind, ())
        return quote_checker(pinned, sections, ev.max_words)

    return t, checks


def test_risk_signals_answer_fired_measures_with_quotes():
    t, checks = evidence_tools(make_set({(2024, "accounts_receivable"): 60}))
    out = t.risk_signals("SYNT", "latest", as_of=AS_OF)
    assert (out.status, out.reason) == ("VERIFIED", "SIGNALS_EVALUATED")
    by_metric = {o.metric: o for o in out.observations}
    count = by_metric["signals_fired"]
    assert count.unit == "count" and count.value >= 1 and "fired: R1" in count.basis
    r1 = by_metric["R1.receivables_growth_gap"]
    assert r1.unit == "pp" and abs(r1.value - (100 / 3 - 100 / 9)) < 1e-9
    assert r1.classification == "CALCULATION" and r1.validated and r1.quotes
    assert "recomputed in Decimal" in r1.basis and r1.detail.startswith("R1 Receivables")
    assert r1.quotes[0].text.startswith("Accounts receivable increased 33% in 2025")
    results, made = t.insight_cards("SYNT", 2025, AS_OF)  # a context measure gets no quotes
    r = next(x for x in results if x.signal_id == "R1")
    card = next(k for k in made if k.signal_id == "R1")
    fired = next(c for c in card.metrics if c.state == "FIRED")
    quiet = replace(fired, condition_id="dso_change", state="NOT_FIRED", value=1.0, unit="days")
    answers = t._signal_answers(t.statement_set("SYNT", AS_OF), t.filing("SYNT", 2025, AS_OF),
                                r, t.signal_config.signals["R1"],
                                replace(card, metrics=(fired, quiet)))
    assert [bool(o.quotes) for o in answers] == [True, False]
    gaps = dict(out.gaps)
    assert "R1" not in gaps and all(g.split(":")[0] in ("NOT_FIRED", "GAP", "NOT_APPLIED")
                                    for g in gaps.values())
    assert not any(o.metric.startswith(tuple(f"{k}." for k in gaps)) for o in out.observations)
    goal = Goal("SYNT", "latest", "risk")
    ctx = gate_context(goal, AS_OF, t, load_planner_config(), t.vocabulary, checks("risk"))
    gated, decisions = gate(out, ctx)
    assert gated.status == "VERIFIED" and all(d.accepted for d in decisions)
    forged = replace(out, observations=tuple(
        replace(o, quotes=(replace(o.quotes[0], text="Receivables doubled on fraud."),))
        if o.quotes else o for o in out.observations))
    assert gate(forged, ctx)[0].reason == "QUOTE_NOT_VERBATIM"
    no_check = gate_context(goal, AS_OF, t, load_planner_config(), t.vocabulary, None)
    assert gate(out, no_check)[0].reason == "QUOTE_NOT_CHECKABLE"
    with pytest.raises(ToolError):
        t.risk_signals("SYNT", 2025, as_of=datetime(2026, 11, 24))
    with pytest.raises(HeldOutError):
        t.risk_signals("HOLD", 2025, as_of=AS_OF)


def test_risk_signal_without_a_quote_is_flagged_evidence_gap(monkeypatch):
    t, _ = evidence_tools(make_set({(2024, "accounts_receivable"): 60}))
    t.signal_config = replace(t.signal_config, evidence=replace(t.signal_config.evidence,
                                                                min_score=1.0))
    out = t.risk_signals("SYNT", 2025, as_of=AS_OF)
    r1 = next(o for o in out.observations if o.metric.startswith("R1."))
    assert r1.quotes == () and "EVIDENCE_GAP" in r1.flags and out.status == "VERIFIED"


def test_explain_movement_quotes_item_7_or_is_an_evidence_gap():
    t, checks = evidence_tools()
    out = t.explain_movement("SYNT", 2025, "revenue", as_of=AS_OF)
    assert (out.status, out.reason) == ("VERIFIED", "EXPLAINED")
    value, moved = out.observations
    assert (value.metric, value.value) == ("revenue", millions(1000))
    assert (moved.metric, moved.value, moved.classification) == (
        "revenue_change", millions(100), "CALCULATION")
    assert moved.sources == (FILINGS[-2].accession,) and moved.quotes
    assert moved.quotes[0].text.startswith("Revenue increased 11% in 2025")
    assert all(q.section == "ITEM_7" for q in moved.quotes)
    goal = Goal("SYNT", 2025, "explain", "revenue")
    ctx = gate_context(goal, AS_OF, t, load_planner_config(), t.vocabulary, checks("explain"))
    gated, _ = gate(out, ctx)
    assert gated.status == "VERIFIED" and len(gated.observations) == 2
    debt = t.explain_movement("SYNT", 2025, "funded_debt", as_of=AS_OF)  # no debt sentence
    assert (debt.status, debt.reason, debt.observations) == ("GAP", "EVIDENCE_GAP", ())
    assert gate(debt, ctx)[0].reason == "EVIDENCE_GAP"  # a gap passes through, D7
    flat = t.explain_movement("SYNT", 2025, "dividends_paid", as_of=AS_OF)
    assert flat.reason == "NO_MOVEMENT"
    prior = t.explain_movement("SYNT", 2025, "inventory", as_of=AS_OF)
    assert prior.reason == "PRIOR_YEAR_NOT_VERIFIED" and prior.detail.startswith(
        "FY2024 inventory: GAP ")  # the fact's own status and reason
    assert t.explain_movement("SYNT", 2019, "revenue", as_of=AS_OF).reason == \
        "PERIOD_NOT_AVAILABLE"
