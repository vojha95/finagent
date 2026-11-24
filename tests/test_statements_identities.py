"""Identities I1-I12: PASS / FAIL / NOT_COMPUTABLE, omitted operands, acceptance."""

from decimal import Decimal

from statements_support import FILING, END, fact, millions, obs, pending, row, tag_only

from finagent.config import load_identities
from finagent.records import Prediction
from finagent.statements.identities import (
    FAIL,
    NOT_COMPUTABLE,
    PASS,
    REVIEW,
    acceptance_checks,
    Context,
    apply_acceptance,
    evaluate_filing,
    expand,
    prior_year_results,
    tolerance,
    Operands,
)

LIBRARY = load_identities()
SPECS = {s.identity_id: s for s in LIBRARY.identities}
NO_NCI = {("us-gaap:MinorityInterest",): "ABSENT",
          ("us-gaap:NetIncomeLossAttributableToNoncontrollingInterest",): "ABSENT"}


def _balanced():
    """A small filing whose identities hold (amounts in millions)."""
    return {
        "revenue": obs("revenue", millions(1000)), "cost_of_revenue": obs("cost_of_revenue",
                                                                          millions(600)),
        "gross_profit": obs("gross_profit", millions(400)),
        "operating_income": obs("operating_income", millions(150)),
        "operating_expenses": obs("operating_expenses", millions(250)),
        "pretax_income": obs("pretax_income", millions(140)),
        "income_tax_expense": obs("income_tax_expense", millions(40)),
        "net_income": obs("net_income", millions(100)),
        "total_assets": obs("total_assets", millions(900)),
        "total_current_assets": obs("total_current_assets", millions(300)),
        "cash_and_cash_equivalents": obs("cash_and_cash_equivalents", millions(100)),
        "accounts_receivable": obs("accounts_receivable", millions(80)),
        "total_liabilities": obs("total_liabilities", millions(500)),
        "equity_attributable_to_parent": obs("equity_attributable_to_parent", millions(400)),
        "total_liabilities_and_equity": obs("total_liabilities_and_equity", millions(900)),
        "statement_cash_opening": tag_only("statement_cash_opening", millions(90)),
        "operating_cash_flow": obs("operating_cash_flow", millions(120)),
        "investing_cash_flow": obs("investing_cash_flow", millions(-70)),
        "financing_cash_flow": obs("financing_cash_flow", millions(-40)),
        "statement_cash": tag_only("statement_cash", millions(100)),
        "retained_earnings_opening": tag_only("retained_earnings_opening", millions(300)),
        "retained_earnings": tag_only("retained_earnings", millions(370)),
        "dividends_charged_to_re": tag_only("dividends_charged_to_re", millions(30)),
        "capital_expenditure": obs("capital_expenditure", millions(50)),
        "depreciation": tag_only("depreciation", millions(20)),
    }


def _ctx(pool, nci=None, **fields):
    bases = {"pretax_basis": "pretax_income" if "pretax_income" in pool else None}
    return Context(FILING, pool, bases, NO_NCI if nci is None else nci, 6, **fields)


def _by_id(results):
    found = {}
    for r in results:
        found.setdefault(r.identity_id, []).append(r)
    return found


def test_balanced_filing_passes_and_lists_omitted_operands():
    results = _by_id(evaluate_filing(LIBRARY, _ctx(_balanced())))
    for identity in ("I1", "I2", "I3", "I4", "I5", "I7", "I9", "I10", "I11"):
        assert results[identity][0].status == PASS, identity
    assert results["I9"][0].residual == 0 and results["I9"][0].tolerance == 0
    assert "temporary_equity" in results["I9"][0].omitted  # left out, never 0
    assert "fx_effect_on_cash" in results["I10"][0].omitted
    assert "repurchases_charged_to_re" in results["I11"][0].omitted
    assert results["I4"][0].detail == "variant 2"
    assert all(r.status == PASS for r in results["I12"]) and len(results["I12"]) == 4
    assert results["I6"][0].status == NOT_COMPUTABLE  # no cash flow rows given


def test_failures_and_exact_tolerance():
    pool = _balanced()
    pool["total_assets"] = obs("total_assets", millions(900) + 1)
    results = _by_id(evaluate_filing(LIBRARY, _ctx(pool)))
    assert results["I9"][0].status == FAIL and results["I9"][0].residual == Decimal(1)
    assert results["I1"][0].status == PASS  # default tolerance: 0.1% of the largest operand
    pool["retained_earnings"] = tag_only("retained_earnings", millions(371))
    i11 = _by_id(evaluate_filing(LIBRARY, _ctx(pool)))["I11"][0]
    # #61: an I11 failure is REVIEW with its residual, never FAIL (it blocks nothing)
    assert i11.status == REVIEW and i11.residual == millions(-1)


def test_i11_needs_disclosed_distributions():
    pool = _balanced()
    del pool["dividends_charged_to_re"]
    i11 = _by_id(evaluate_filing(LIBRARY, _ctx(pool)))["I11"][0]
    assert i11.status == NOT_COMPUTABLE and i11.residual is None  # Appendix B "when" (#61)
    assert set(i11.omitted) == {"dividends_charged_to_re", "repurchases_charged_to_re"}


def test_review_blocks_no_acceptance():
    pool = _balanced()
    pool["net_income"] = pending("net_income", millions(100))
    pool["retained_earnings"] = tag_only("retained_earnings", millions(371))  # I11 off by 1
    results = evaluate_filing(LIBRARY, _ctx(pool))
    assert _by_id(results)["I11"][0].status == REVIEW
    assert all(status in (PASS, FAIL) for status, _ in acceptance_checks(results))
    assert apply_acceptance(pool, results)["net_income"].accepted  # I5 passes; REVIEW ignored


def test_not_computable_lists_missing_operands():
    pool = _balanced()
    pool["total_liabilities"] = obs("total_liabilities", status="DISAGREE", accepted=False,
                                    reason="VALUES_DIFFER")
    results = _by_id(evaluate_filing(LIBRARY, _ctx(pool)))
    assert results["I9"][0].status == NOT_COMPUTABLE
    assert results["I9"][0].missing == ("total_liabilities",)
    del pool["gross_profit"]
    assert _by_id(evaluate_filing(LIBRARY, _ctx(pool)))["I3"][0].status == NOT_COMPUTABLE


def test_equity_operand_needs_no_nci_for_parent_equity():
    pool = _balanced()
    reported = {("us-gaap:MinorityInterest",): "REPORTED",
                ("us-gaap:NetIncomeLossAttributableToNoncontrollingInterest",): "ABSENT"}
    i9 = _by_id(evaluate_filing(LIBRARY, _ctx(pool, nci=reported)))["I9"][0]
    assert i9.status == NOT_COMPUTABLE and "equity" in i9.missing
    pool["noncontrolling_interest_equity"] = tag_only("noncontrolling_interest_equity", 0)
    i9 = _by_id(evaluate_filing(LIBRARY, _ctx(pool, nci=reported)))["I9"][0]
    assert i9.status == PASS


def test_temporary_equity_counts_only_on_a_face_row():
    pool = _balanced()
    pool["total_liabilities"] = obs("total_liabilities", millions(495))
    pool["temporary_equity"] = tag_only("temporary_equity", millions(5), display_check="MATCH")
    assert _by_id(evaluate_filing(LIBRARY, _ctx(pool)))["I9"][0].status == PASS
    pool["temporary_equity"] = tag_only("temporary_equity", millions(5),
                                        display_check="NOT_ON_FACE")
    i9 = _by_id(evaluate_filing(LIBRARY, _ctx(pool)))["I9"][0]
    assert i9.status == FAIL and "temporary_equity (not a face row)" in i9.omitted


def test_i5_adds_equity_method_income_only_for_the_before_equity_method_basis():
    pool = _balanced()
    pool["net_income"] = obs("net_income", millions(110))
    pool["equity_method_income"] = tag_only("equity_method_income", millions(10))
    assert _by_id(evaluate_filing(LIBRARY, _ctx(pool)))["I5"][0].status == FAIL
    pool["pretax_income_before_equity_method"] = pool.pop("pretax_income")
    ctx = Context(FILING, pool, {"pretax_basis": "pretax_income_before_equity_method"}, NO_NCI, 6)
    assert _by_id(evaluate_filing(LIBRARY, ctx))["I5"][0].status == PASS


def test_i6_compares_the_cash_flow_starting_row():
    cf = row(0, "Net income", texts=("100", "90"), statement="CF")
    facts = [fact("us-gaap:NetIncomeLoss", millions(100), cell=(1, 0, 3))]
    preds = [Prediction(cf.row_id, (("net_income", 0.9),), "t")]
    result = _by_id(evaluate_filing(LIBRARY, _ctx(_balanced(), rows=[cf], predictions=preds,
                                                  facts=facts)))["I6"][0]
    assert result.status == PASS
    wrong = row(0, "Net income", texts=("80", "90"), statement="CF")
    result = _by_id(evaluate_filing(LIBRARY, _ctx(_balanced(), rows=[wrong], predictions=preds)
                                    ))["I6"][0]
    assert result.status == FAIL and result.residual == millions(-20)


def test_i7_ordering_and_i12_sanity():
    pool = _balanced()
    pool["total_current_assets"] = obs("total_current_assets", millions(950))
    results = _by_id(evaluate_filing(LIBRARY, _ctx(pool)))
    assert results["I7"][0].status == FAIL
    assert "short_term_investments" in results["I7"][0].omitted
    pool["capital_expenditure"] = obs("capital_expenditure", millions(-5))
    sanity = {r.subject: r.status for r in _by_id(evaluate_filing(LIBRARY, _ctx(pool)))["I12"]}
    assert sanity["non_negative(capital_expenditure)"] == FAIL
    assert sanity["positive(revenue)"] == PASS


def test_tolerance_rule():
    ops = Operands(values=[("+a", millions(1000)), ("-b", millions(-999))])
    assert tolerance(SPECS["I1"], ops, 6) == max(Decimal(1_000_000), millions(1000) / 1000)
    assert tolerance(SPECS["I9"], ops, 6) == 0


def test_prior_year_compares_each_channel():
    later = {"revenue": obs("revenue", millions(100), document_value=millions(100),
                            tag_value=millions(100)),
             "inventory": obs("inventory", millions(50), document_value=millions(50),
                              tag_value=millions(50)),
             "gross_profit": obs("gross_profit")}
    earlier = {"revenue": obs("revenue", millions(100), document_value=millions(100),
                              tag_value=millions(100)),
               "inventory": obs("inventory", millions(40), document_value=millions(40),
                                tag_value=millions(40)),
               "gross_profit": obs("gross_profit")}
    results = {r.subject: r for r in prior_year_results(SPECS["I8"], FILING, later, earlier,
                                                        ["revenue", "inventory", "gross_profit"])}
    assert results["revenue"].status == PASS
    assert results["inventory"].status == FAIL and results["inventory"].residual == millions(10)
    assert "gross_profit" not in results  # presented by neither filing


def test_acceptance_of_document_only_values():
    pool = _balanced()
    pool["total_liabilities"] = pending("total_liabilities", millions(500))
    pool["research_and_development"] = pending("research_and_development", millions(10))
    pool["inventory"] = pending("inventory", millions(1000))  # breaks I7
    results = evaluate_filing(LIBRARY, _ctx(pool))
    settled = apply_acceptance(pool, results)
    assert settled["total_liabilities"].accepted
    assert settled["research_and_development"].reason == "NO_IDENTITY_CHECK"
    assert (settled["inventory"].accepted, settled["inventory"].reason) == (False,
                                                                            "IDENTITY_FAILED")
    i9 = next(r for r in results if r.identity_id == "I9")
    assert i9.provisional == ("total_liabilities",)


def test_every_variant_is_tested_for_acceptance():
    # I4 variant 1 (revenue - total costs) fails, variant 2 (GP - opex) passes (#69).
    pool = _balanced()
    pool["total_costs_and_expenses"] = obs("total_costs_and_expenses", millions(800))
    pool["operating_income"] = pending("operating_income", millions(150))
    pool["operating_expenses"] = pending("operating_expenses", millions(250))
    results = evaluate_filing(LIBRARY, _ctx(pool))
    i4 = _by_id(results)["I4"][0]
    assert i4.status == PASS and i4.detail == "variant 2"  # reported: the passing variant
    assert [(v.variant, v.status) for v in i4.variants] == [(1, FAIL), (2, PASS)]
    settled = apply_acceptance(pool, results)
    # operating income enters the failing variant: not accepted, although variant 2 passes
    assert (settled["operating_income"].accepted, settled["operating_income"].reason) == (
        False, "IDENTITY_FAILED")
    # operating expenses enter only the passing variant: accepted
    assert settled["operating_expenses"].accepted


def test_a_value_only_in_a_non_computable_variant_has_no_check():
    pool = _balanced()
    pool["operating_expenses"] = pending("operating_expenses", millions(250))
    del pool["gross_profit"]  # variant 2 cannot be computed; variant 1 has no total costs
    results = evaluate_filing(LIBRARY, _ctx(pool))
    assert _by_id(results)["I4"][0].status == NOT_COMPUTABLE
    assert apply_acceptance(pool, results)["operating_expenses"].reason == "NO_IDENTITY_CHECK"


def test_expand_follows_formula_inputs():
    pool = {"revenue": obs("revenue", 1), "cost_of_revenue": obs("cost_of_revenue", 1),
            "gross_profit": obs("gross_profit", 0, classification="CALCULATION",
                                inputs=("revenue", "cost_of_revenue"))}
    assert expand("gross_profit", pool) == {"revenue", "cost_of_revenue"}
    assert expand("revenue", pool) == {"revenue"}
    assert END  # the fixture period is the filing's own
