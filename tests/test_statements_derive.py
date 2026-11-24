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


PROXY = DerivationSpec("ebit_proxy", "formula", ((1, "pretax_basis"), (1, "interest_expense")),
                       when_not_presented=("operating_income",), flag="EBIT_PROXY")
OPEX = DerivationSpec("operating_expense_after_cost_of_sales", "formula",
                      ((1, "gross_profit"), (-1, "ebit_basis")))
EBIT = Derivations({"pretax_basis": ("pretax_income",),
                    "ebit_basis": ("operating_income", "ebit_proxy")}, (PROXY, OPEX))


def test_ebit_proxy_only_without_an_operating_income_line():
    # #68: single-step income statement -> EBIT proxy = pre-tax basis + interest expense
    pool = {"pretax_income": obs("pretax_income", 90), "interest_expense": tag_only(
        "interest_expense", 10), "gross_profit": obs("gross_profit", 300),
        "operating_income": obs("operating_income", None, status="GAP", accepted=False,
                                reason=NOT_REPORTED)}
    result, bases = derive_all(FILING, EBIT, pool, provisional=False)
    proxy = result["ebit_proxy"]
    assert proxy.value == Decimal(100) and proxy.classification == "CALCULATION"
    assert "EBIT_PROXY" in proxy.flags and proxy.basis.startswith("EBIT_PROXY")
    assert bases["ebit_basis"] == "ebit_proxy"
    opex = result["operating_expense_after_cost_of_sales"]
    assert opex.value == Decimal(200) and "EBIT_PROXY" in opex.flags  # the flag is carried
    assert opex.basis == "ebit_basis=ebit_proxy"


def test_ebit_proxy_never_replaces_a_presented_operating_income():
    base = {"pretax_income": obs("pretax_income", 90),
            "interest_expense": tag_only("interest_expense", 10),
            "gross_profit": obs("gross_profit", 300)}
    presented = dict(base, operating_income=obs("operating_income", 120))
    result, bases = derive_all(FILING, EBIT, presented, provisional=False)
    assert "ebit_proxy" not in result and bases["ebit_basis"] == "operating_income"
    assert result["operating_expense_after_cost_of_sales"].value == Decimal(180)
    # presented but disagreeing: no proxy, no EBIT (never a fallback over a presented line)
    disputed = dict(base, operating_income=obs("operating_income", None, status="DISAGREE",
                                                 accepted=False, reason="VALUES_DIFFER"))
    result, bases = derive_all(FILING, EBIT, disputed, provisional=False)
    assert "ebit_proxy" not in result and bases["ebit_basis"] is None


def test_ebit_proxy_needs_interest_expense():
    pool = {"pretax_income": obs("pretax_income", 90)}
    result, _ = derive_all(FILING, EBIT, pool, provisional=False)
    assert result["ebit_proxy"].value is None and result["ebit_proxy"].reason == INPUT_GAP


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


def test_stop_at_disagree_never_falls_back_past_a_disputed_reading():
    alternatives = ("interest_expense", "interest_expense_gross")
    pool = {"interest_expense": obs("interest_expense", None, status="DISAGREE",
                                    accepted=False, reason="VALUES_DIFFER"),
            "interest_expense_gross": tag_only("interest_expense_gross", 5)}
    assert choose_basis(pool, alternatives, stop_at_disagree=True) is None
    assert choose_basis(pool, alternatives) == "interest_expense_gross"
    pool["interest_expense"] = obs("interest_expense", reason=NOT_REPORTED)  # a GAP
    assert choose_basis(pool, alternatives, stop_at_disagree=True) == "interest_expense_gross"


def test_ebit_proxy_uses_the_interest_basis_from_config():
    from finagent.config import load_derivations
    derivations = load_derivations()
    proxy = next(r for r in derivations.rules if r.derivation_id == "ebit_proxy")
    assert (1, "interest_basis") in proxy.terms
    assert derivations.bases["interest_basis"][0] == "interest_expense"
    assert "interest_basis" in derivations.stop_at_disagree
