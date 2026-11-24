"""E2 (blueprint 10.2, Phase 5c): injections, one case end to end, metrics, bootstrap, the
held-out refusal, the e2 config and the run files. All filings are synthetic."""

import json
import random
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from statements_support import FILING, millions, row, tag_outcome

from finagent.config import ConfigError, HeldOutError, load_agent_config, load_e2_config, \
    load_universe
from finagent.evaluation.e1 import CORRECT, SCALE, FieldResult
from finagent.evaluation.e2 import (CLEAN, CORRUPTED, AnswerKey, Case, CaseResult, Injection,
                                    bootstrap_rate, case_record, condition_metrics,
                                    evaluate_case, injection_rows, load_cases, newly_failing,
                                    plan_injections, render_report, run_condition, run_e2,
                                    score, value_outcomes, write_run)
from finagent.records import DocumentEdit, Prediction
from finagent.statements.build import StatementConfigs
from finagent.verify.correlation_agent import ACTION_LIMIT, NO_FAILING_CHECK
from finagent.verify.workspace import FilingState

CONFIGS = StatementConfigs.load()
AGENT = load_agent_config()
E2 = load_e2_config()
AS_OF = date(2026, 6, 30)

# A small consistent filing (in millions): I1, I2, I3, I7 and I9 hold; total current
# liabilities enters no identity.
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


def _rows():
    return {item: row(pos, label, (value, "1"), statement=statement)
            for item, (statement, pos, label, value) in LINES.items()}


def _case(fiscal_year=2025, second=None):
    """second = (row item, item): that row also has `item` second in its top-3."""
    rows = _rows()
    predictions = []
    for item, r in rows.items():
        ranked = ((item, 0.9), ("OTHER", 0.05))
        if second and second[0] == item:
            ranked = ((item, 0.6), (second[1], 0.3), ("OTHER", 0.05))
        predictions.append(Prediction(r.row_id, ranked, "t"))
    return Case(replace(FILING, fiscal_year=fiscal_year), tuple(rows.values()),
                tuple(predictions))


def _key():
    tags = {item: tag_outcome("VERIFIED", [millions(value.replace(",", ""))], item=item)
            for item, (_, _, _, value) in LINES.items()}
    return AnswerKey(tags, frozenset(LINES), {})


def _state(case, injections=()):
    return FilingState.evaluation(case.filing, case.rows, case.predictions, CONFIGS, AGENT,
                                  injections)


def _injection(case, item, kind, factor=1, negate=False, row_item=None):
    rows = {r.row_id: r for r in case.rows}
    row_id = next(r for r in rows if r.endswith(f":{LINES[row_item or item][0]}:"
                                                f"{LINES[row_item or item][1]}"))
    edit = DocumentEdit(case.filing.accession, case.filing.end, item, row_id, Decimal(factor),
                        negate, source="injection", actions=(kind,))
    clean = _state(case).reading(item).value
    injected = _state(case, [edit]).reading(item).value
    truth = millions(LINES[item][3].replace(",", ""))
    return Injection(case.filing.issuer, case.filing.fiscal_year, kind, kind, item, edit, truth,
                     clean, injected)


# ---------------------------------------------------------------- injections

def test_injection_rows_give_each_type():
    case = _case(second=("total_current_liabilities", "total_liabilities"))
    state = _state(case)
    truth = millions(500)
    own = state.reading("total_liabilities").row_id
    rng = random.Random(0)
    assert injection_rows(state, "total_liabilities", "rescale_1000", truth, rng, AS_OF) == \
        [(own, Decimal(1000), False)]
    assert injection_rows(state, "total_liabilities", "flip_sign", truth, rng, AS_OF) == \
        [(own, Decimal(1), True)]
    tcl = state.reading("total_current_liabilities").row_id
    assert injection_rows(state, "total_liabilities", "second_candidate", truth, rng,
                          AS_OF) == [(tcl, Decimal(1), False)]
    ((row_id, factor, negate),) = injection_rows(state, "total_liabilities", "wrong_row",
                                                 truth, rng, AS_OF)
    assert row_id not in {own, tcl} and ":BS:" in row_id and (factor, negate) == (1, False)
    # No other candidate row: second_candidate is not possible.
    assert injection_rows(_state(_case()), "total_liabilities", "second_candidate", truth,
                          rng, AS_OF) == []


def test_plan_injections_is_reproducible_rotates_types_and_makes_the_item_wrong():
    cases = [_case(year, second=("total_current_liabilities", "total_liabilities"))
             for year in (2021, 2022, 2023, 2024)]
    keys = {c.key: _key() for c in cases}
    first = plan_injections(cases, keys, CONFIGS, AGENT, E2, AS_OF)
    again = plan_injections(list(reversed(cases)), keys, CONFIGS, AGENT, E2, AS_OF)
    assert first == again
    assert [first[c.key].assigned for c in cases] == list(E2.injection_types)
    for case in cases:
        injection = first[case.key]
        injected = _state(case, [injection.edit])
        result = {r.item: r for r in score(case, keys[case.key], injected.documents)}
        assert result[injection.item].outcome != CORRECT
        assert injection.injected_value == injected.reading(injection.item).value
        assert injection.true_value == injection.clean_value


def test_a_case_with_no_eligible_item_gets_no_injection():
    case = _case()
    key = AnswerKey({}, frozenset(), {})
    assert plan_injections([case], {case.key: key}, CONFIGS, AGENT, E2, AS_OF) == \
        {case.key: None}
    assert run_condition([case], {case.key: key}, CONFIGS, AGENT, AS_OF,
                         {case.key: None}) == []


# ---------------------------------------------------------------- one case

def test_a_located_injection_is_detected_and_corrected():
    case = _case()
    injection = _injection(case, "total_liabilities", "rescale_1000", factor=1000)
    result = evaluate_case(case, _key(), CONFIGS, AGENT, AS_OF, injection, CORRUPTED)
    assert (result.visible, result.detectable, result.detected, result.flagged,
            result.corrected) == (True, True, True, True, True)
    assert result.fixed == ("total_liabilities",) and result.wrongly_changed == ()
    assert result.values == (("total_liabilities", "CORRECTED"),)
    assert result.actions_used == 1 and result.stop_reason == NO_FAILING_CHECK
    assert result.checks_after[0] == result.checks_after[1] > result.checks_before[0]
    assert "total_liabilities" in result.checked


def test_an_injection_no_check_reads_is_invisible():
    case = _case()
    injection = _injection(case, "total_current_liabilities", "rescale_1000", factor=1000)
    result = evaluate_case(case, _key(), CONFIGS, AGENT, AS_OF, injection, CORRUPTED)
    assert (result.visible, result.detectable, result.detected, result.flagged,
            result.corrected) == (False, False, False, False, False)
    assert result.actions_used == 0 and result.before == result.after
    assert "total_current_liabilities" not in result.checked


def test_clean_condition_leaves_a_consistent_filing_alone():
    case = _case()
    (result,) = run_condition([case], {case.key: _key()}, CONFIGS, AGENT, AS_OF)
    assert result.condition == CLEAN and result.injection is None
    assert (result.visible, result.detectable, result.detected, result.corrected) == \
        (None, None, None, None)
    assert result.actions_used == 0 and result.values == () and result.before == result.after
    assert all(f.outcome == CORRECT for f in result.before)


def test_newly_failing_ignores_a_failure_the_clean_readings_already_have():
    case = _case()
    clean = _state(case)
    injected = _state(case, [_injection(case, "total_liabilities", "x", 1000).edit])
    assert newly_failing(clean, clean) == []
    assert {c.check_id for c in newly_failing(clean, injected)} >= {"I2", "I9"}


# ---------------------------------------------------------------- metrics

def _field(issuer, item, outcome, year=2025):
    return FieldResult(issuer, year, item, outcome, Decimal(1), Decimal(1))


def _result(issuer, before, after, *, injection=None, values=(), actions=0,
            stop=NO_FAILING_CHECK, located=None, visible=None, detected=None, corrected=None,
            flagged=None, checks=((5, 5), (5, 5))):
    b = tuple(_field(issuer, i, o) for i, o in before.items())
    a = tuple(_field(issuer, i, o) for i, o in after.items())
    right_b = {i: o == CORRECT for i, o in before.items()}
    right_a = {i: o == CORRECT for i, o in after.items()}
    return CaseResult(issuer, 2025, "acc", CORRUPTED if injection else CLEAN, 0.5, injection,
                      located, detected, corrected, actions, stop, tuple(values), b, a,
                      checks[0], checks[1],
                      tuple(i for i in right_b if right_b[i] and not right_a[i]),
                      tuple(i for i in right_b if not right_b[i] and right_a[i]), (),
                      tuple(before), visible, flagged)


def _fake_injection(issuer, kind, item="revenue"):
    edit = DocumentEdit("acc", FILING.end, item, "r", source="injection")
    return Injection(issuer, 2025, kind, kind, item, edit, Decimal(1), Decimal(1), Decimal(2))


def test_condition_metrics_on_hand_built_results():
    results = [
        # A: the injected revenue is corrected; gross profit wrongly changed.
        _result("A", {"revenue": SCALE, "gross_profit": CORRECT},
                {"revenue": CORRECT, "gross_profit": SCALE},
                injection=_fake_injection("A", "rescale_1000"),
                values=(("revenue", "CORRECTED"), ("gross_profit", "CORRECTED")), actions=2,
                located=True, visible=True, detected=True, corrected=True, flagged=True,
                checks=((3, 5), (5, 5))),
        # B: invisible; the agent stops at the action limit and flags a right value.
        _result("B", {"revenue": SCALE, "cost_of_revenue": CORRECT},
                {"revenue": SCALE, "cost_of_revenue": CORRECT},
                injection=_fake_injection("B", "flip_sign"),
                values=(("cost_of_revenue", "REVIEW"),), actions=5, stop=ACTION_LIMIT,
                located=False, visible=False, detected=False, corrected=False, flagged=False,
                checks=((4, 5), (4, 5))),
    ]
    m = condition_metrics(results, draws=200)
    assert m["cases"] == 2 and m["fields"] == 4
    assert (m["field_accuracy_before"], m["field_accuracy_after"]) == (0.5, 0.5)
    assert m["mcnemar"]["before_only_correct"] == 1 and m["mcnemar"]["after_only_correct"] == 1
    assert (m["wrongly_changed"], m["fixed"]) == (1, 1)
    assert m["wrongly_changed_rate"] == 0.5
    assert m["values"] == {"CORRECTED": 2, "REVIEW": 1}
    assert m["value_outcomes"] == {"CORRECTED": {"right_before": 1, "wrong_before": 1},
                                   "REVIEW": {"right_before": 1}}
    assert (m["actions_mean"], m["actions_max"]) == (3.5, 5)
    assert m["action_limit_share"]["k"] == 1
    assert (m["identity_pass_rate_before"], m["identity_pass_rate_after"]) == (0.7, 0.9)
    assert m["all_checks_pass_after"]["k"] == 1
    assert m["fields_in_a_check"] == 1.0
    assert m["injected"]["corrected"]["k"] == 1 and m["injected"]["visible"]["k"] == 1
    assert set(m["by_type"]) == {"rescale_1000", "flip_sign"}
    assert m["by_type"]["rescale_1000"]["corrected"]["rate"] == 1.0
    assert m["by_detectability"]["located"]["cases"] == 1
    assert m["by_detectability"]["invisible"]["cases"] == 1
    assert m["by_detectability"]["visible_not_located"]["cases"] == 0


def test_clean_metrics_have_no_injection_block():
    m = condition_metrics([_result("A", {"revenue": CORRECT}, {"revenue": CORRECT})], 50)
    assert "injected" not in m and m["mcnemar"]["p_value"] == 1.0


def test_value_outcomes_counts_unscored_values():
    r = _result("A", {"revenue": CORRECT}, {"revenue": CORRECT},
                values=(("ppe_net", "REVIEW"),))
    assert value_outcomes([r]) == {"REVIEW": {"unscored": 1}}


def test_bootstrap_rate_resamples_companies():
    assert bootstrap_rate([]) is None
    assert bootstrap_rate([("A", True), ("B", True)], draws=100) == (1.0, 1.0)
    cases = [("A", True), ("A", True), ("B", False), ("C", True), ("D", False)]
    low, high = bootstrap_rate(cases, draws=500, seed=1)
    assert 0 <= low <= 0.6 <= high <= 1
    assert bootstrap_rate(cases, draws=500, seed=1) == (low, high)


# ---------------------------------------------------------------- inputs and config

def test_load_cases_refuses_a_held_out_company_before_opening_anything():
    universe = load_universe()
    held = next(t for t, split in universe.items() if split == "held_out")
    with pytest.raises(HeldOutError):
        load_cases(None, [held], None, CONFIGS, AS_OF, (), universe)


def test_load_e2_config_reads_and_validates(tmp_path):
    assert E2.injection_types == ("wrong_row", "rescale_1000", "flip_sign", "second_candidate")
    assert E2.prior_sensitivity == (0.25, 0.75) and E2.bootstrap_draws > 0
    bad = tmp_path / "agent.yaml"
    bad.write_text("e2: {seed: 1, injection_types: [delete_row]}\n")
    with pytest.raises(ConfigError):
        load_e2_config(bad)
    bad.write_text("e2: {seed: 1, injection_types: [flip_sign], prior_sensitivity: [1.5]}\n")
    with pytest.raises(ConfigError):
        load_e2_config(bad)
    bad.write_text("e2: {injection_types: [flip_sign]}\n")
    with pytest.raises(ConfigError):
        load_e2_config(bad)


# ---------------------------------------------------------------- the full run and its files

def test_run_e2_shares_injections_across_priors_and_writes_the_run(tmp_path):
    cases = [_case(year) for year in (2022, 2023)]
    keys = {c.key: _key() for c in cases}
    run = run_e2(cases, keys, CONFIGS, AGENT, replace(E2, bootstrap_draws=50), AS_OF)
    assert run.priors == (0.5, 0.25, 0.75)
    assert set(run.results) == {(p, c) for p in run.priors for c in (CLEAN, CORRUPTED)}
    for prior in run.priors:
        assert {r.prior for r in run.results[prior, CORRUPTED]} == {prior}
        assert [r.injection for r in run.results[prior, CORRUPTED]] == \
            [run.injections[c.key] for c in cases]
    header = {"run_id": "e2-test", "git_commit": "abc", "git_dirty": True,
              "evidence_manifest_sha256": "0" * 64, "model_version": "m", "dataset_version": "v",
              "as_of": AS_OF.isoformat(), "split": "validation", "companies": ["SYNT"],
              "injection_types": list(E2.injection_types), "seed": E2.seed,
              "bootstrap_draws": 50}
    write_run(tmp_path / "e2-test", run, header)
    lines = (tmp_path / "e2-test" / "cases.jsonl").read_text().splitlines()
    assert len(lines) == 3 * 2 * len(cases)
    assert json.loads(lines[0])["fields"]
    metrics = json.loads((tmp_path / "e2-test" / "metrics.json").read_text())
    assert set(metrics) == {"0.5", "0.25", "0.75"}
    assert (tmp_path / "e2-test" / "injections.csv").read_text().count("\n") == 1 + len(cases)
    text = render_report(run, header)
    for heading in ("## 2. Results at prior 0.5", "## 3. Injected errors", "## 5. Prior "):
        assert heading in text
    assert "SYNT FY2022" in text


def test_case_record_is_json_with_every_field():
    case = _case()
    injection = _injection(case, "total_liabilities", "rescale_1000", factor=1000)
    record = case_record(evaluate_case(case, _key(), CONFIGS, AGENT, AS_OF, injection,
                                       CORRUPTED))
    text = json.dumps(record)
    assert record["injection"]["item"] == "total_liabilities" and record["corrected"] is True
    tl = next(f for f in record["fields"] if f["item"] == "total_liabilities")
    assert (tl["before"], tl["after"]) == ("SCALE", "CORRECT") and "500" in text
