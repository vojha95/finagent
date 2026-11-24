"""Configuration-driven derivations: INPUT_GAP, never-overwrite, bases, weakest status."""

import re
from decimal import Decimal

from statements_support import FILING, fact, millions, obs, pending, row, tag_only

from finagent.config import DerivationSpec, Derivations
from finagent.statements.derive import (
    DERIVED,
    FINANCE_LEASES_INCLUDED,
    INPUT_GAP,
    choose_basis,
    derive_all,
    derive_face_block,
    derive_formula,
    finalise_pending,
    resolve,
    usable,
)
from finagent.statements.verify_set import NOT_REPORTED, PENDING

GP = DerivationSpec("gross_profit", "formula", ((1, "revenue"), (-1, "cost_of_revenue")),
                    unless_presented=True)
FCF = DerivationSpec("free_cash_flow", "formula", ((1, "operating_cash_flow"),
                                                   (-1, "capital_expenditure")))
DEBT = DerivationSpec("funded_debt", "formula", ((1, "long_term_debt"),),
                      ((1, "long_term_debt_current"), (1, "commercial_paper")),
                      flag_finance_leases=True)


def test_formula_value_status_and_inputs():
    pool = {"revenue": obs("revenue", 100), "cost_of_revenue": tag_only("cost_of_revenue", 60)}
    gp = derive_formula(FILING, GP, pool, {}, provisional=False)
    assert gp.value == Decimal(40) and gp.classification == "CALCULATION"
    assert (gp.status, gp.channels, gp.reason, gp.accepted) == ("SINGLE_CHANNEL", ("tag",),
                                                                DERIVED, True)
    assert gp.inputs == ("revenue", "cost_of_revenue")


def test_missing_input_is_input_gap_never_zero():
    pool = {"operating_cash_flow": obs("operating_cash_flow", 50),
            "capital_expenditure": obs("capital_expenditure", status="DISAGREE", accepted=False,
                                       reason="VALUES_DIFFER")}
    fcf = derive_formula(FILING, FCF, pool, {}, provisional=False)
    assert (fcf.status, fcf.reason, fcf.value) == ("GAP", INPUT_GAP, None)
    assert "capital_expenditure" in fcf.inputs


def test_inputs_never_reported_make_the_result_not_reported():
    pool = {"operating_cash_flow": obs("operating_cash_flow", reason=NOT_REPORTED),
            "capital_expenditure": obs("capital_expenditure", reason=NOT_REPORTED)}
    assert derive_formula(FILING, FCF, pool, {}, False).reason == NOT_REPORTED


def test_presented_value_is_never_overwritten():
    pool = {"revenue": obs("revenue", 100), "cost_of_revenue": obs("cost_of_revenue", 60),
            "gross_profit": obs("gross_profit", status="DISAGREE", accepted=False,
                                reason="VALUES_DIFFER")}
    assert derive_formula(FILING, GP, pool, {}, provisional=False) is None
    result, _ = derive_all(FILING, Derivations({}, (GP,)), pool, provisional=False)
    assert result["gross_profit"].status == "DISAGREE"


def test_optional_terms_omitted_listed_or_gap():
    pool = {"long_term_debt": obs("long_term_debt", 100),
            "long_term_debt_current": obs("long_term_debt_current", 10),
            "commercial_paper": obs("commercial_paper")}  # not presented
    debt = derive_formula(FILING, DEBT, pool, {}, provisional=False)
    assert debt.value == Decimal(110) and "OMITTED:commercial_paper" in debt.flags
    pool["commercial_paper"] = obs("commercial_paper", status="DISAGREE", accepted=False,
                                   reason="VALUES_DIFFER")
    assert derive_formula(FILING, DEBT, pool, {}, provisional=False).reason == INPUT_GAP


def test_finance_lease_flag():
    pool = {"long_term_debt": obs("long_term_debt", 100,
                                  concepts=("us-gaap:LongTermDebtAndCapitalLeaseObligations",))}
    lease = frozenset({"us-gaap:LongTermDebtAndCapitalLeaseObligations"})
    assert FINANCE_LEASES_INCLUDED in derive_formula(FILING, DEBT, pool, {}, False, lease).flags
    assert FINANCE_LEASES_INCLUDED not in derive_formula(FILING, DEBT, pool, {}, False).flags


def test_provisional_mode_uses_pending_values_and_stays_pending():
    pool = {"revenue": obs("revenue", 100), "cost_of_revenue": pending("cost_of_revenue", 60)}
    assert derive_formula(FILING, GP, pool, {}, provisional=False).reason == INPUT_GAP
    gp = derive_formula(FILING, GP, pool, {}, provisional=True)
    assert gp.value == Decimal(40) and not gp.accepted and PENDING in gp.flags
    assert gp.channels == ("document",)


def test_basis_prefers_an_accepted_value_and_is_recorded():
    pool = {"pretax_income": pending("pretax_income", 90),
            "pretax_income_before_equity_method": tag_only("pretax_income_before_equity_method",
                                                           97)}
    alternatives = ("pretax_income", "pretax_income_before_equity_method")
    assert choose_basis(pool, alternatives) == "pretax_income_before_equity_method"
    assert choose_basis({"pretax_income": pending("pretax_income", 90)}, alternatives) == \
        "pretax_income"
    assert choose_basis({}, alternatives) is None
    rule = DerivationSpec("nonoperating_net", "formula",
                          ((1, "pretax_basis"), (-1, "operating_income")))
    pool["operating_income"] = obs("operating_income", 80)
    result, bases = derive_all(FILING, Derivations({"pretax_basis": alternatives}, (rule,)), pool,
                               provisional=False)
    assert bases == {"pretax_basis": "pretax_income_before_equity_method"}
    assert result["nonoperating_net"].value == Decimal(17)
    assert result["nonoperating_net"].basis == "pretax_basis=pretax_income_before_equity_method"
    assert resolve("pretax_basis", pool, bases).metric == "pretax_income_before_equity_method"


def test_face_block_derivation_and_anchor():
    stops = (re.compile("^common stock", re.IGNORECASE),)
    rule = DerivationSpec("total_liabilities", "face_block_sum", unless_presented=True,
                          statement="BS", after_item="total_current_liabilities",
                          stop_patterns=stops, include_anchor=True)
    rows = [row(0, "Total current liabilities", ("100", "90"), is_total=True),
            row(1, "Long-term debt", ("50", "40")), row(2, "Common stock", ("5", "5"))]
    facts = [fact("us-gaap:LC", millions(100), cell=(1, 0, 3)),
             fact("us-gaap:D", millions(50), cell=(1, 1, 3))]
    pool = {"total_current_liabilities": obs("total_current_liabilities", millions(100),
                                             row_id=rows[0].row_id),
            "total_liabilities": obs("total_liabilities")}
    tl = derive_face_block(FILING, rule, pool, rows, facts)
    assert (tl.value, tl.status, tl.accepted) == (millions(150), "VERIFIED", True)
    assert tl.inputs == (rows[0].row_id, rows[1].row_id) and "Common stock" in tl.detail
    doc_only = derive_face_block(FILING, rule, pool, rows, facts[:1])
    assert doc_only.status == "SINGLE_CHANNEL" and PENDING in doc_only.flags
    pool["total_liabilities"] = obs("total_liabilities", millions(150))
    assert derive_face_block(FILING, rule, pool, rows, facts) is None  # presented


def test_usable_and_finalise_pending():
    value = pending("x", 5)
    assert usable(value, provisional=True) and not usable(value, provisional=False)
    assert not usable(obs("x"), provisional=True)
    accepted = finalise_pending(value, True, "DOCUMENT_ONLY")
    assert accepted.accepted and PENDING not in accepted.flags
    rejected = finalise_pending(value, False, "IDENTITY_FAILED")
    assert (rejected.accepted, rejected.reason) == (False, "IDENTITY_FAILED")
