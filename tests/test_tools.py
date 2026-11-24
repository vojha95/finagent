"""Planner tools (Phase 6.3): as_of on every method, explicit-key caches, outcome mapping,
the Decimal second way for ratios, BC25 and the Phase 7 placeholders."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from model_support import AS_OF, FILINGS, make_set, value_obs
from statements_support import millions, obs, pending

from finagent.config import HeldOutError
from finagent.model.ratios import BY_ID
from finagent.records import IdentityResult, StatementsCheck
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


# ---------------------------------------------------------------- Phase 7 placeholders

def test_placeholders_are_gaps_without_values():
    t = tools()
    for out in (t.debt_service_scoped("SYNT", "latest", "Base", None, as_of=AS_OF),
                t.explain_movement("SYNT", 2025, "revenue", as_of=AS_OF),
                t.risk_signals("SYNT", 2025, as_of=AS_OF)):
        assert (out.status, out.reason, out.observations) == ("GAP", "NOT_YET_AVAILABLE", ())
    with pytest.raises(ToolError):
        t.risk_signals("SYNT", 2025, as_of=datetime(2026, 11, 24))
