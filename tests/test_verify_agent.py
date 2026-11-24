"""Correlation Agent (8.8, Phase 5): edits, tools, the loop, product and evaluation modes, the
final check (BC22) and the evaluation-mode NCI rule (BC23). All filings are synthetic."""

import inspect
import re
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from statements_support import ACCESSION, END, FILING, fact, millions, obs, row, tag_outcome

from finagent.config import ConfigError, load_agent_config, load_universe
from finagent.document.channel import read_document_channel
from finagent.records import DocumentEdit, Prediction
from finagent.statements.build import StatementConfigs, StatementSet
from finagent.statements.verify_set import CORRECTED, INJECTED, observe_item
from finagent.verify.agent_tools import (ToolError, candidates, flip_sign, mark_review, rescale,
                                         run_identities, swap)
from finagent.verify.correlation_agent import (ACTION_LIMIT, NO_ADMISSIBLE_ACTION,
                                               NO_FAILING_CHECK, options, run_agent, settle,
                                               suspects)
from finagent.verify.edits import EditError, apply_edits, edited_reading
from finagent.verify.reconcile import final_check
from finagent.verify.workspace import ABSENT, FilingState

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = StatementConfigs.load()
AGENT = load_agent_config()
AS_OF = date(2026, 6, 30)
NO_NCI = {c: ABSENT for c, _ in CONFIGS.nci_concepts}

# A small filing whose identities hold (in millions): BS I1, I2, I7, I9 and IS I3.
LINES = {
    "cash_and_cash_equivalents": ("BS", 1, "Cash and cash equivalents", "100"),
    "total_current_assets": ("BS", 2, "Total current assets", "300"),
    "total_assets": ("BS", 3, "Total assets", "900"),
    "total_current_liabilities": ("BS", 4, "Total current liabilities", "200"),
    "total_liabilities": ("BS", 5, "Total liabilities", "500"),
    "total_equity": ("BS", 6, "Total equity", "400"),
    "total_liabilities_and_equity": ("BS", 7, "Total liabilities and equity", "900"),
    "revenue": ("IS", 1, "Revenue", "1,000"),
    "cost_of_revenue": ("IS", 2, "Cost of revenue", "600"),
    "gross_profit": ("IS", 3, "Gross profit", "400"),
}


def _rows(drop=(), texts=None):
    texts = texts or {}
    return {item: row(pos, label, (texts.get(item, value), "1"), statement=statement)
            for item, (statement, pos, label, value) in LINES.items() if item not in drop}


def _predictions(rows, probabilities=None, extra=()):
    probabilities = probabilities or {}
    found = [Prediction(r.row_id, ((item, probabilities.get(item, 0.9)), ("OTHER", 0.05)), "t")
             for item, r in rows.items()]
    return found + list(extra)


def _state(drop=(), texts=None, probabilities=None, extra=(), injections=(), agent=AGENT):
    rows = _rows(drop, texts)
    return FilingState.evaluation(FILING, list(rows.values()), _predictions(rows, probabilities,
                                                                            extra),
                                  CONFIGS, agent, injections)


def _inject(item, factor=1, negate=False, row_id=None):
    return DocumentEdit(ACCESSION, END, item, row_id or _rows()[item].row_id, Decimal(factor),
                        negate, source="injection")


def _failing(state):
    return {c.check_id for c in state.view.checks if c.status == "FAIL"}


def _steps(result):
    return [(s.tool, s.arguments, s.decision, s.status, s.observations) for s in result.trace]


# ---------------------------------------------------------------- edits (in-memory hook)

def test_edited_reading_rescales_flips_and_swaps_and_keeps_the_original():
    rows = _rows()
    by_id = {r.row_id: r for r in rows.values()}
    spec = CONFIGS.concept_map["total_liabilities"]
    original = read_document_channel(rows.values(), _predictions(rows), FILING,
                                     [spec])["total_liabilities"]
    tl = rows["total_liabilities"].row_id
    scaled = edited_reading(DocumentEdit(ACCESSION, END, "total_liabilities", tl,
                                         Decimal(1000)), by_id, spec, original)
    assert scaled.value == millions(500) * 1000 and scaled.scale == original.scale + 3
    assert (scaled.original_value, scaled.original_row_id) == (millions(500), tl)
    flipped = edited_reading(DocumentEdit(ACCESSION, END, "total_liabilities", tl,
                                          negate=True), by_id, spec, scaled)
    assert flipped.value == -millions(500)
    assert flipped.original_value == millions(500)  # the first reading, not the scaled one
    swapped = edited_reading(DocumentEdit(ACCESSION, END, "total_liabilities",
                                          rows["total_current_liabilities"].row_id),
                             by_id, spec, original)
    assert swapped.value == millions(200) and swapped.row_id.endswith(":BS:4")


def test_an_edit_onto_a_blank_cell_or_unknown_row_raises():
    rows = _rows(texts={"total_current_liabilities": ""})
    by_id = {r.row_id: r for r in rows.values()}
    spec = CONFIGS.concept_map["total_liabilities"]
    original = read_document_channel(rows.values(), _predictions(rows), FILING,
                                     [spec])["total_liabilities"]
    with pytest.raises(EditError):
        edited_reading(DocumentEdit(ACCESSION, END, "total_liabilities",
                                    rows["total_current_liabilities"].row_id), by_id, spec,
                       original)
    with pytest.raises(EditError):
        edited_reading(DocumentEdit(ACCESSION, END, "total_liabilities", "nowhere"), by_id,
                       spec, original)


def test_apply_edits_is_in_memory_one_edit_per_item_and_ignores_other_filings():
    rows = _rows()
    documents = read_document_channel(rows.values(), _predictions(rows), FILING,
                                      CONFIGS.concept_map.values())
    before = dict(documents)
    edit = _inject("total_liabilities", 1000)
    edited = apply_edits(documents, [edit, replace(edit, accession="other")], FILING,
                         list(rows.values()), CONFIGS.concept_map)
    assert documents == before  # the input is not changed
    assert edited["total_liabilities"].value == millions(500) * 1000
    assert edited["total_liabilities"].edit == edit
    with pytest.raises(EditError):
        apply_edits(documents, [edit, replace(edit, negate=True)], FILING, list(rows.values()),
                    CONFIGS.concept_map)


def test_observe_item_flags_a_corrected_or_injected_reading_and_keeps_the_original():
    rows = _rows()
    by_id = {r.row_id: r for r in rows.values()}
    spec = CONFIGS.concept_map["total_liabilities"]
    original = read_document_channel(rows.values(), _predictions(rows), FILING,
                                     [spec])["total_liabilities"]
    tl = rows["total_liabilities"].row_id
    agent_edit = DocumentEdit(ACCESSION, END, "total_liabilities", tl, negate=True,
                              actions=("flip_sign",))
    tag = tag_outcome("VERIFIED", [str(-millions(500))], item="total_liabilities")
    o = observe_item(FILING, spec, edited_reading(agent_edit, by_id, spec, original), tag,
                     by_id, ("BS",))
    assert (o.status, o.reason, o.value) == ("VERIFIED", "AGREE", -millions(500))
    assert CORRECTED in o.flags and o.correction == "agent:flip_sign"
    assert (o.corrected_from_value, o.corrected_from_row) == (millions(500), tl)
    injected = observe_item(FILING, spec, edited_reading(replace(agent_edit, source="injection"),
                                                         by_id, spec, original), None, by_id,
                            ("BS",))
    assert INJECTED in injected.flags and CORRECTED not in injected.flags


# ---------------------------------------------------------------- workspace and tools

def test_a_balanced_filing_has_no_failing_check_and_the_agent_does_nothing():
    state = _state()
    assert not _failing(state)
    assert {"I1", "I2", "I3", "I9"} <= {c.check_id for c in state.view.checks}
    result = run_agent(state, AS_OF)
    assert result.stop_reason == NO_FAILING_CHECK and not result.actions and not result.values


def test_evaluation_mode_has_no_tag_input_and_follows_bc23_for_nci():
    parameters = inspect.signature(FilingState.evaluation).parameters
    assert not {"tags", "facts", "model_inputs", "nci", "inputs"} & set(parameters)
    state = _state()
    assert (state.tags, state.facts, state.model_inputs) == ({}, (), {})
    assert state.nci == {}  # total_equity is read: NCI status unknown, no assumption
    no_equity = _state(drop=("total_equity",))
    assert no_equity.nci == NO_NCI  # BC23: no total-equity and no NI-incl-NCI line read


def test_evaluation_mode_trace_is_identical_with_scrambled_tags():
    state = _state(injections=[_inject("total_liabilities", 1000)])
    scrambled = replace(
        state, tags={"total_liabilities": tag_outcome("VERIFIED", ["123"],
                                                      item="total_liabilities")},
        facts=(fact("us-gaap:Liabilities", "777", cell=(1, 5, 0)),),
        model_inputs={"retained_earnings": obs("retained_earnings", 5)})
    a, b = run_agent(state, AS_OF), run_agent(scrambled, AS_OF)
    assert _steps(a) == _steps(b) and a.values == b.values and a.edits == b.edits


def test_tools_refuse_a_filing_filed_after_as_of():
    state = _state()
    early = date(2026, 1, 15)
    for call in (lambda: run_identities(state, early),
                 lambda: candidates(state, "total_liabilities", early),
                 lambda: flip_sign(state, "total_liabilities", early),
                 lambda: rescale(state, "total_liabilities", Decimal(1000), early),
                 lambda: swap(state, "total_liabilities", "x", early),
                 lambda: mark_review(state, ["total_liabilities"], early)):
        with pytest.raises(ToolError):
            call()


def test_tools_never_act_on_a_missing_value_or_a_blank_row():
    state = _state(drop=("total_equity",))
    assert state.reading("total_equity").value is None
    for call in (lambda: flip_sign(state, "total_equity", AS_OF),
                 lambda: rescale(state, "total_equity", Decimal(1000), AS_OF),
                 lambda: swap(state, "total_equity", _rows()["total_assets"].row_id, AS_OF)):
        with pytest.raises(ToolError):
            call()
    blank = _state(texts={"total_current_liabilities": ""})
    with pytest.raises(ToolError):
        swap(blank, "total_liabilities", _rows()["total_current_liabilities"].row_id, AS_OF)
    with pytest.raises(ToolError):
        rescale(state, "total_liabilities", Decimal(7), AS_OF)  # not a configured factor


def test_rescale_is_one_unit_correction_and_flip_twice_returns_to_the_reading():
    state = _state()
    up = rescale(state, "total_liabilities", Decimal(1000), AS_OF)
    with pytest.raises(ToolError):
        rescale(up, "total_liabilities", Decimal(1000), AS_OF)
    back = rescale(up, "total_liabilities", Decimal("0.001"), AS_OF)
    assert not back.edits  # at the starting reading the edit is dropped
    flipped = flip_sign(state, "total_liabilities", AS_OF)
    assert flipped.edits["total_liabilities"].actions == ("flip_sign",)
    assert not flip_sign(flipped, "total_liabilities", AS_OF).edits


def test_candidates_are_other_rows_with_the_item_in_their_top_k():
    rows = _rows()
    extra = [Prediction(rows["total_current_liabilities"].row_id,
                        (("total_current_liabilities", 0.6), ("total_liabilities", 0.3)), "t")]
    state = _state(extra=extra)
    found = candidates(state, "total_liabilities", AS_OF)
    assert [c.row_id for c in found] == [rows["total_current_liabilities"].row_id]
    assert found[0].probability == 0.3 and found[0].value == millions(200)
    narrow = _state(extra=extra, agent=load_agent_config(top_k=1))
    assert candidates(narrow, "total_liabilities", AS_OF) == []


# ---------------------------------------------------------------- the loop

def test_agent_corrects_a_thousand_fold_error():
    state = _state(injections=[_inject("total_liabilities", 1000)])
    assert {"I2", "I9"} <= _failing(state)
    result = run_agent(state, AS_OF)
    assert result.stop_reason == NO_FAILING_CHECK and result.actions_used == 1
    (action,) = result.actions
    assert (action.tool, action.item, action.factor) == ("rescale", "total_liabilities",
                                                        Decimal("0.001"))
    (value,) = result.values
    assert (value.status, value.final_value) == ("CORRECTED", millions(500))
    assert value.original_value == millions(500) * 1000
    assert [s.tool for s in result.trace] == ["run_identities", "candidates", "rescale",
                                              "run_identities"]


def test_agent_corrects_a_sign_error():
    result = run_agent(_state(injections=[_inject("gross_profit", negate=True)]), AS_OF)
    assert [(a.tool, a.item) for a in result.actions] == [("flip_sign", "gross_profit")]
    (value,) = result.values
    assert (value.status, value.final_value) == ("CORRECTED", millions(400))


def test_agent_swaps_to_the_second_candidate_row():
    rows = _rows()
    # The current-liabilities row is M1's top total_liabilities row: the channel reads 200.
    extra = [Prediction(rows["total_current_liabilities"].row_id,
                        (("total_liabilities", 0.95), ("total_current_liabilities", 0.04)), "t")]
    rows_ = [r for i, r in rows.items()]
    predictions = [p for p in _predictions(rows)
                   if p.row_id != rows["total_current_liabilities"].row_id] + extra
    state = FilingState.evaluation(FILING, rows_, predictions, CONFIGS, AGENT)
    assert state.reading("total_liabilities").value == millions(200)
    result = run_agent(state, AS_OF)
    (action,) = result.actions
    assert (action.tool, action.row_id) == ("swap", rows["total_liabilities"].row_id)
    (value,) = result.values
    assert value.final_value == millions(500) and value.final_row_id == action.row_id


def test_an_action_that_breaks_a_passing_check_is_not_admissible():
    state = _state(injections=[_inject("total_assets", 1000)])
    assert {"I1", "I9"} <= _failing(state) and state.view.check("I2").status == "PASS"
    residual = state.view.residual
    found = options(state, suspects(state), AS_OF)
    # Total liabilities x1,000 lowers R (I9 moves closer) but breaks I2.
    tl_up = next(o for o in found if o.item == "total_liabilities" and o.factor == Decimal(1000))
    assert tl_up.residual_after < residual and "I2" in tl_up.broken
    assert not tl_up.admissible(residual, False)
    assert not any(o.admissible(residual, False) for o in found if o.broken)
    result = run_agent(state, AS_OF)
    assert [(a.tool, a.item) for a in result.actions] == [("rescale", "total_assets")]


def test_action_limit_stops_and_leaves_the_rest_for_review():
    agent = load_agent_config(max_actions=1)
    state = _state(injections=[_inject("total_liabilities", 1000),
                               _inject("gross_profit", negate=True)], agent=agent)
    result = run_agent(state, AS_OF)
    assert result.stop_reason == ACTION_LIMIT and result.actions_used == 1
    status = {v.item: v.status for v in result.values}
    assert status["total_liabilities"] == "CORRECTED"
    assert status["gross_profit"] == "REVIEW"
    review = next(v for v in result.values if v.item == "gross_profit")
    assert review.final_value == review.original_value == -millions(400)  # reading kept
    assert any(check == "I3" and s == "FAIL" for check, s, _ in review.checks)
    assert len(run_agent(replace(state, agent=AGENT), AS_OF).actions) <= AGENT.max_actions


def test_no_plug_and_missing_stays_missing():
    # Total liabilities is not shown and TA != TLE: no displayed value can fix I1.
    state = _state(drop=("total_liabilities",), texts={"total_liabilities_and_equity": "905"})
    assert "I1" in _failing(state)
    result = run_agent(state, AS_OF)
    assert result.stop_reason == NO_ADMISSIBLE_ACTION and not result.edits
    assert {v.status for v in result.values} == {"REVIEW"}
    assert all(v.final_value == v.original_value for v in result.values)
    assert state.reading("total_liabilities").value is None


def test_settle_reverts_a_change_whose_checks_still_fail():
    state = _state()
    wrong = flip_sign(state, "total_liabilities", AS_OF)
    final, kept, dropped = settle(wrong)
    assert (kept, dropped) == ([], ["total_liabilities"]) and not final.edits


def test_every_value_the_agent_writes_is_a_displayed_cell():
    state = _state(injections=[_inject("total_liabilities", 1000),
                               _inject("gross_profit", negate=True)])
    result = run_agent(state, AS_OF)
    cells = {millions(v.replace(",", "")) for *_, v in LINES.values()}
    for v in result.values:
        assert abs(v.final_value) in cells | {c * 1000 for c in cells} | \
            {c / 1000 for c in cells}


# ---------------------------------------------------------------- product mode

def _product(tl_tag, drop=(), extra=()):
    rows = _rows(drop)
    tl = rows["total_current_liabilities"].row_id
    predictions = [p for p in _predictions(rows) if p.row_id != tl] + list(extra or [
        Prediction(tl, (("total_liabilities", 0.95), ("total_current_liabilities", 0.04)), "t")])
    documents = read_document_channel(rows.values(), predictions, FILING,
                                      CONFIGS.concept_map.values())
    tags = {item: tag_outcome("VERIFIED", [str(millions(v.replace(",", "")))], item=item)
            for item, (*_, v) in LINES.items() if item in documents}
    tags["total_liabilities"] = tag_outcome("VERIFIED", [str(tl_tag)], item="total_liabilities")
    return FilingState(FILING, "product", CONFIGS, AGENT, tuple(rows.values()),
                       tuple(predictions), documents, tags=tags, nci={}, located=("BS", "IS"),
                       scale=6)


def test_product_mode_changes_only_disagree_and_document_only_values():
    state = _product(millions(500))
    assert state.disagree == {"total_liabilities"}
    assert state.changeable == {"total_liabilities"}
    assert state.view.check("D:total_liabilities").status == "FAIL"


def test_product_mode_disagree_resolves_to_the_tag_agreeing_cell():
    state = _product(millions(500))
    result = run_agent(state, AS_OF)
    (action,) = result.actions
    assert action.tool == "swap"
    (value,) = result.values
    assert (value.status, value.final_value) == ("CORRECTED", millions(500))
    assert value.final_row_id == _rows()["total_liabilities"].row_id
    assert dict((c, s) for c, s, _ in value.checks)["D:total_liabilities"] == "PASS"


def test_product_mode_never_copies_the_tag_value():
    # The tag shows 510: no displayed cell agrees with it, so nothing changes.
    state = _product(millions(510))
    result = run_agent(state, AS_OF)
    assert result.stop_reason == NO_ADMISSIBLE_ACTION and not result.edits
    (value,) = [v for v in result.values if v.item == "total_liabilities"]
    assert value.status == "REVIEW" and value.final_value == millions(200)
    swap_option = next(o for o in result.trace[1].observations if o["tool"] == "swap")
    assert swap_option["agrees"] is False and swap_option["admissible"] is False


# ---------------------------------------------------------------- final check (BC22)

def _set(**changes):
    values = {
        "total_assets": 900, "total_liabilities": 500, "total_equity": 400,
        "statement_cash_opening": 90, "operating_cash_flow": 120, "investing_cash_flow": -70,
        "financing_cash_flow": -40, "statement_cash": 100,
        "retained_earnings_opening": 300, "net_income": 100, "retained_earnings": 370,
        "dividends_charged_to_re": 30,
    }
    values.update(changes)
    observations = {(2025, m): obs(m, millions(v)) for m, v in values.items() if v is not None}
    return StatementSet("SYNT", "0000999999", AS_OF, (FILING,), observations, [],
                        {2025: {}}, "t", nci={2025: NO_NCI})


def _check(s):
    return final_check(s, 2025, CONFIGS.identities, AGENT)


def test_final_check_verified_when_i9_and_i10_are_exactly_zero():
    c = _check(_set())
    assert c.status == "VERIFIED" and not c.review
    assert {r.identity_id: r.status for r in c.identities} == {"I9": "PASS", "I10": "PASS",
                                                               "I11": "PASS"}


def test_final_check_is_invalid_for_any_non_zero_i9_or_i10():
    c = _check(_set(total_assets=Decimal("900.000001")))  # within the default tolerance
    assert c.status == "INVALID" and "I9 residual" in c.reason


def test_final_check_is_gap_when_i9_or_i10_is_not_computable():
    c = _check(_set(investing_cash_flow=None))
    assert c.status == "GAP" and "investing_cash_flow" in c.reason


def test_final_check_i11_is_review_and_does_not_block():
    c = _check(_set(retained_earnings=Decimal(371)))
    assert c.status == "VERIFIED" and c.review and c.review[0].startswith("I11 residual")
    no_distributions = _check(_set(dividends_charged_to_re=None))
    i11 = next(r for r in no_distributions.identities if r.identity_id == "I11")
    assert i11.status == "NOT_COMPUTABLE" and no_distributions.status == "VERIFIED"


# ---------------------------------------------------------------- config and guards

def test_agent_config_validation(tmp_path):
    assert AGENT.max_actions == 5 and "I8" not in AGENT.identities
    assert load_agent_config(rescale_prior=0.25).rescale_prior == 0.25
    with pytest.raises(ConfigError):
        load_agent_config(rescale_prior=1.5)
    with pytest.raises(ConfigError):
        load_agent_config(identities=["I1", "I8"])
    with pytest.raises(ConfigError):
        load_agent_config(rescale_factors=[Decimal(1)])


def test_no_company_specific_code_in_the_verify_package():
    tickers = set(load_universe())
    pattern = re.compile(r"\b(ticker|issuer|cik)\s*(==|!=|in)\s*['\"(\[{]")
    for path in (ROOT / "finagent" / "verify").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert not pattern.search(source), path
        assert not {t for t in tickers if re.search(rf"['\"]{t}['\"]", source)}, path
