"""The investigation loop and investigate() on stub tools (blueprint 8.15 loop rules; no
evidence is read)."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest
from planner_support import StubClassifier, held_out_name, resources
from tools_support import AS_OF, KeywordClassifier, StubTools, answer, by_year, missing_primary

from finagent.planner.compile import TrainedParser, compile_question
from finagent.planner.investigate import investigate, scenario_defaulted
from finagent.planner.loop import condition, describe, run_status
from finagent.planner.state import GoalState, RunState
from finagent.records import Compilation, Goal, Outcome, Quote

R = resources()


def run(question, tools=None, label="fact", **kw):
    parser = TrainedParser(KeywordClassifier(kw.pop("keywords", {}), label), 0.8)
    return investigate(question, AS_OF, tools=tools or StubTools(), parser=parser,
                       resources=R, **kw)


def test_fact_goal_verified_in_one_step():
    r = run("What was Microsoft's revenue in fiscal 2024?")
    assert (r.status, r.stop_reason, r.steps_used, r.reused) == ("COMPLETE", "goals_resolved", 1,
                                                                 0)
    step = r.trace[0]
    assert step.tool == "reported_verified" and step.as_of == AS_OF.isoformat()
    assert step.arguments == {"issuer": "MSFT", "period": 2024, "metric": "revenue",
                              "as_of": "2026-11-24"}
    assert step.gate == ({"metric": "revenue", "accepted": True, "code": ""},)
    assert "MSFT FY2024 revenue: USD 1,000.0m" in r.answer


def test_missing_primary_runs_the_tag_only_fallback():
    tools = StubTools(overrides={"reported_verified": missing_primary("DOC_MISSING")})
    r = run("What was Microsoft's revenue in fiscal 2024?", tools)
    assert [s.tool for s in r.trace] == ["reported_verified", "reported_tag_only"]
    assert r.trace[0].status == "MISSING_PRIMARY" and r.status == "COMPLETE"
    assert "SINGLE_CHANNEL" in r.requested[0].observations[0].flags
    assert "Primary reading missing (DOC_MISSING)" in r.trace[1].decision


def test_tag_missing_runs_document_only_and_gaps_when_not_accepted():  # BC25
    tools = StubTools(overrides={"reported_verified": missing_primary("TAG_MISSING")})
    r = run("What was Microsoft's revenue in fiscal 2024?", tools)
    assert [s.tool for s in r.trace] == ["reported_verified", "reported_document_only"]
    assert (r.status, r.stop_reason) == ("EVIDENCE_GAP", "evidence_gap")
    assert r.requested[0].reason == "NO_IDENTITY_CHECK"


def test_unknown_trigger_stops_with_no_eligible_action():
    tools = StubTools(overrides={"reported_verified": missing_primary("SOMETHING_ELSE")})
    r = run("What was Microsoft's revenue in fiscal 2024?", tools)
    assert r.stop_reason == "no_eligible_action"
    assert r.requested[0].reason == "STOPPED_NO_ELIGIBLE_ACTION"


def test_prerequisite_then_reuse_for_default_metrics():
    r = run("Forecast Microsoft for next year.", label="forecast")
    assert [s.tool for s in r.trace] == ["reconcile", "project"]
    assert r.reused == 2 and len(r.trace[1].reused_by) == 2
    assert [g.goal.metric for g in r.requested] == ["revenue", "free_cash_flow", "cash"]
    assert all(g.status == "VERIFIED" for g in r.requested)
    assert r.scenario_defaulted and "Base scenario is used" in r.answer
    statements = [g for g in r.goals if not g.requested]
    assert statements[0].goal == Goal("MSFT", 2025, "statements")


def test_precompiled_goals_skip_the_compiler():  # E8 replays
    goals = (Goal("MSFT", "latest", "forecast", "revenue", "Base"),)
    compilation = Compilation("E8 replay", "fixed-goals", goals, issuers=("MSFT",))
    r = investigate("E8 replay", AS_OF, tools=StubTools(), resources=R, compilation=compilation,
                    parser=TrainedParser(StubClassifier("advice", 0.99), 0.8))  # never asked
    assert r.status == "COMPLETE" and r.compilation is compilation
    assert [s.tool for s in r.trace] == ["reconcile", "project"]
    assert not r.scenario_defaulted and "Base scenario is used" not in r.answer


def test_failed_prerequisite_turns_dependants_into_gaps():
    tools = StubTools(overrides={"reconcile": lambda **a: Outcome("INVALID",
                                                                  "FINAL_CHECK_INVALID")})
    r = run("Show Microsoft's credit ratios for the latest year.", tools, label="credit")
    assert [s.tool for s in r.trace] == ["reconcile"]
    assert all(g.status == "GAP" and g.reason == "PREREQUISITE_INVALID" for g in r.requested)
    assert r.status == "EVIDENCE_GAP"


def test_credit_gap_and_not_meaningful():  # #120 b, c
    r = run("Show Microsoft's credit ratios for the latest year.", label="credit")
    by_metric = {g.goal.metric: g for g in r.requested}
    assert by_metric["current_ratio"].status == "VERIFIED"
    assert (by_metric["quick_ratio"].status, by_metric["quick_ratio"].reason) == ("GAP",
                                                                                 "INPUT_GAP")
    assert by_metric["ebit_to_gross_interest"].observations[0].value is None
    assert "n.m. (non-positive denominator)" in r.answer
    assert r.status == "PARTIAL" and r.stop_reason == "evidence_gap"


def conditional(tools):
    return run("Assess Microsoft's severe liquidity next year; if there is a shortfall, check "
               "debt service.", tools, keywords={"debt": "debt", "liquidity": "liquidity"})


def test_conditional_debt_skipped_without_shortfall():
    r = conditional(StubTools(shortfall=0.0))
    debt = [g for g in r.requested if g.goal.kind == "debt"][0]
    assert (debt.status, debt.reason) == ("SKIPPED", "NO_SHORTFALL")
    assert "debt_service_scoped" not in [s.tool for s in r.trace] and r.status == "COMPLETE"


def test_conditional_debt_runs_when_shortfall():
    r = conditional(StubTools(shortfall=25.0))
    assert [s.tool for s in r.trace] == ["reconcile", "liquidity", "debt_service_scoped"]
    debt = [g for g in r.requested if g.goal.kind == "debt"][0]
    assert (debt.status, debt.reason) == ("VERIFIED", "SCOPED_DEBT_SERVICE")
    assert {o.metric for o in debt.observations} == {"scoped_dscr", "cfo_less_capex",
                                                      "debt_service"}
    assert "condition met" in r.trace[-1].decision and r.status == "COMPLETE"
    assert "MSFT FY2026 scoped dscr [Severe]: 1.50x" in r.answer
    assert "Withheld | MSFT comprehensive dscr [Severe]: EVIDENCE_GAP " \
           "COMPREHENSIVE_DSCR_WITHHELD (reason one. Reason two.)." in r.answer
    assert "finance lease payments" not in r.answer  # a NOT_APPLIED gap is not a withheld one


def test_debt_goal_rejects_an_unlisted_metric():
    def extra(issuer, period, scenario, target, as_of):
        out = StubTools()._debt_service_scoped(issuer, period, scenario, target, as_of)
        bad = replace(out.observations[0], metric="ebit_to_gross_interest")
        return replace(out, observations=out.observations + (bad,))

    r = conditional(StubTools(shortfall=25.0, overrides={"debt_service_scoped": extra}))
    debt = [g for g in r.requested if g.goal.kind == "debt"][0]
    assert debt.status == "VERIFIED"  # an unlisted metric is not selected by the gate
    assert "ebit_to_gross_interest" not in {o.metric for o in debt.observations}


def test_debt_goal_without_its_required_metric_is_invalid():
    def no_dscr(issuer, period, scenario, target, as_of):
        out = StubTools()._debt_service_scoped(issuer, period, scenario, target, as_of)
        return replace(out, observations=out.observations[1:])

    r = conditional(StubTools(shortfall=25.0, overrides={"debt_service_scoped": no_dscr}))
    debt = [g for g in r.requested if g.goal.kind == "debt"][0]
    assert (debt.status, debt.reason) == ("INVALID", "NO_OBSERVATION_FOR_METRIC")


def test_conditional_debt_gap_when_liquidity_unverified():
    tools = StubTools(overrides={"liquidity": lambda **a: Outcome("INVALID", "FORECAST_INVALID")})
    r = conditional(tools)
    debt = [g for g in r.requested if g.goal.kind == "debt"][0]
    assert (debt.status, debt.reason) == ("GAP", "CONDITION_UNKNOWN")


def test_comparison_difference_and_both_accessions():
    def verified(issuer, period, metric, as_of):
        f = by_year(StubTools().filings(issuer, as_of), period)
        value = Decimal(300) if issuer == "MSFT" else Decimal(100)
        return Outcome("VERIFIED", "AGREE", (answer(f, metric, value),))

    tools = StubTools(overrides={"reported_verified": verified})
    r = run("Compare Microsoft and Amazon net income for FY2024.", tools)
    assert len(r.comparisons) == 1 and r.comparisons[0].value == Decimal(200)
    assert r.comparisons[0].classification == "CALCULATION"
    assert "issuer-specific" in r.answer and r.comparisons[0].left.accession in r.answer


def test_step_budget_zero_and_limit():
    r = run("Forecast Microsoft for next year.", label="forecast", max_steps=0)
    assert (r.steps_used, r.stop_reason, r.status) == (0, "step_limit", "EVIDENCE_GAP")
    r = run("Forecast Microsoft for next year.", label="forecast", max_steps=1)
    assert r.steps_used == 1 and r.stop_reason == "step_limit"
    assert all(g.reason == "STOPPED_STEP_LIMIT" for g in r.requested)
    assert run("Forecast Microsoft for next year.", label="forecast",
               max_steps=30).stop_reason == "goals_resolved"


@pytest.mark.parametrize("bad", [-1, 31, 2.5])
def test_step_budget_out_of_range_raises(bad):
    with pytest.raises(ValueError):
        run("What was Microsoft's revenue in fiscal 2024?", max_steps=bad)


@pytest.mark.parametrize("as_of", ["2026-11-24", datetime(2026, 11, 24), None])
def test_as_of_must_be_a_date(as_of):
    with pytest.raises(ValueError):
        investigate("What was Microsoft's revenue in fiscal 2024?", as_of,  # type: ignore
                    tools=StubTools(), resources=R,
                    parser=TrainedParser(StubClassifier("fact", 0.99), 0.8))


def test_gate_refuses_a_value_from_a_filing_after_as_of():
    def leaky(issuer, period, metric, as_of):  # ignores as_of: answers from the FY2024 10-K
        f = by_year(StubTools().fs[issuer], 2024)
        return Outcome("VERIFIED", "AGREE", (answer(f, metric, Decimal(1)),))

    r = investigate("What was Microsoft's revenue in fiscal 2024?", date(2025, 1, 15),
                    tools=StubTools(overrides={"reported_verified": leaky}), resources=R,
                    parser=TrainedParser(StubClassifier("fact", 0.99), 0.8))
    assert r.trace[0].arguments["as_of"] == "2025-01-15"
    assert r.trace[0].arguments["period"] == 2024  # FY2024 is not filed by then: unresolved
    # no FY2024 10-K exists by as_of, so the period end cannot match (the first rule that fails)
    assert (r.requested[0].status, r.requested[0].reason) == ("INVALID", "PERIOD_END_MISMATCH")
    assert r.answer.startswith("Rejected")


def test_tool_exception_is_invalid_not_a_value():
    def boom(**args):
        raise RuntimeError("no store")

    r = run("What was Microsoft's revenue in fiscal 2024?",
            StubTools(overrides={"reported_verified": boom}))
    assert (r.requested[0].status, r.requested[0].reason) == ("INVALID", "TOOL_ERROR")


def test_clarification_runs_no_tool():
    tools = StubTools()
    r = investigate("Should I buy Microsoft shares?", AS_OF, tools=tools, resources=R,
                    parser=TrainedParser(StubClassifier("advice", 0.99), 0.8))
    assert (r.status, r.stop_reason, r.steps_used) == ("CLARIFICATION", "clarification", 0)
    assert tools.calls == [] and "ADVICE" in r.answer


def test_held_out_issuer_is_never_read():
    _, name = held_out_name()
    tools = StubTools()
    r = investigate(f"What was {name}'s revenue in fiscal 2024?", AS_OF, tools=tools,
                    resources=R, parser=TrainedParser(StubClassifier("fact", 0.99), 0.8))
    assert tools.calls == []
    assert r.compilation.clarification is not None or all(
        g.reason == "HELD_OUT_ISSUER" for g in r.requested)


def test_held_out_issuer_is_served_only_with_include_held_out():
    """BC36 (D2b): the app serves held-out companies; the default path never reads them."""
    ticker, name = held_out_name()
    question = f"What was {name}'s revenue in fiscal 2024?"
    parser = TrainedParser(StubClassifier("fact", 0.99), 0.8)
    compiled = compile_question(question, parser, R, ticker)
    assert compiled.held_out == (ticker,)
    tools = StubTools()
    r = investigate(question, AS_OF, tools=tools, resources=R, compilation=compiled,
                    include_held_out=True)
    assert tools.calls and all(g.reason != "HELD_OUT_ISSUER" for g in r.requested)
    refused = StubTools()
    r = investigate(question, AS_OF, tools=refused, resources=R, compilation=compiled)
    assert refused.calls == [] and all(g.reason == "HELD_OUT_ISSUER" for g in r.requested)


def test_trace_written(tmp_path):
    from finagent.planner.trace import read_runs, read_steps

    r = run("Forecast Microsoft for next year.", label="forecast", trace_dir=tmp_path,
            run_id="t1")
    runs = read_runs(tmp_path)
    assert runs[0]["run_id"] == "t1" and runs[0]["as_of"] == "2026-11-24"
    assert len(read_steps(tmp_path, "t1")) == r.steps_used
    assert (tmp_path / "trace.jsonl").read_text().count('"type": "step"') == r.steps_used


# ---------------------------------------------------------------- helpers

def test_condition_and_run_status():
    debt = Goal("MSFT", "latest", "debt", "", "Base", None, True)
    state = RunState("q", AS_OF)
    assert condition(debt, state) == "unknown"
    s = state.add(Goal("MSFT", "latest", "liquidity", "liquidity_shortfall", "Base", None),
                  False)
    assert condition(debt, state) == "wait"
    f = by_year(StubTools().filings("MSFT", AS_OF), 2025)
    o = answer(f, "liquidity_shortfall", 10.0, classification="CALCULATION_FROM_MODEL_ASSUMPTION",
               fiscal_year=2026, projected=True)
    s.update(Outcome("VERIFIED", "ok", (o,)))
    assert condition(debt, state) == "met"
    s.update(Outcome("VERIFIED", "ok", (replace(o, value=0.0),)))
    assert condition(debt, state) == "unmet"
    done = GoalState(debt, True, status="SKIPPED")
    assert run_status([done]) == "COMPLETE" and run_status([]) == "EVIDENCE_GAP"
    assert "conditional" in describe(debt)


def test_goal_state_keeps_missing_primary_open():
    s = GoalState(Goal("MSFT", 2024, "fact", "revenue"), True)
    s.update(Outcome("MISSING_PRIMARY", "DOC_MISSING"))
    assert (s.terminal, s.trigger) == (False, "DOC_MISSING")
    s.update(Outcome("VERIFIED", "TAG_ONLY"))
    assert (s.terminal, s.trigger) == (True, "start")


def test_scenario_defaulted():
    goals = (Goal("MSFT", "latest", "forecast", "revenue", "Base"),)
    assert scenario_defaulted("Forecast Microsoft revenue", goals, R)
    assert not scenario_defaulted("Forecast Microsoft revenue in the base case", goals, R)
    assert not scenario_defaulted("Microsoft revenue", (Goal("MSFT", 2024, "fact", "revenue"),),
                                  R)


def test_risk_goal_answers_fired_signals_with_checked_quotes():  # 7.3, #155, #157
    checked = []

    def checks(kind):
        return lambda o, f: checked.append((kind, o.metric)) or None

    r = run("What are the main risk signals for Microsoft?", label="risk", quote_checks=checks)
    risk = r.requested[0]
    assert (risk.goal.kind, risk.status) == ("risk", "VERIFIED")
    assert {o.metric for o in risk.observations} == {"signals_fired",
                                                     "R1.receivables_growth_gap",
                                                     "R5.funded_debt_to_ebitda"}
    assert checked == [("risk", "R1.receivables_growth_gap")]  # only quoted values are checked
    assert "  Quote | ITEM_7, chunk " in r.answer and "longer payment terms." in r.answer
    assert "  Evidence gap | R5: the filing does not explain the movement" in r.answer
    assert "Other signals | MSFT: R2 GAP: NO_COGS the filer reports no cogs line; R3 " \
           "NOT_FIRED." in r.answer
    assert "+22.2 pp" in r.answer and "signals fired: 1" in r.answer


def test_pinned_quote_checks_and_explain_answers():
    from types import SimpleNamespace

    from finagent.planner.loop import explain_answers, pinned_quote_checks

    assert pinned_quote_checks(StubTools())("risk") is None  # no store: nothing checkable
    checks = pinned_quote_checks(SimpleNamespace(store=object()))
    assert callable(checks("risk")) and callable(checks("fact"))  # built lazily, nothing read
    spec = explain_answers("free_cash_flow", R.planner)
    assert spec.expected("free_cash_flow") == ("USD", ("CALCULATION",))  # as a fact
    assert spec.expected("revenue") == ("USD", ("REPORTED_FACT",))
    assert spec.expected("free_cash_flow_change") == ("USD", ("CALCULATION",))


def test_risk_quotes_without_a_check_are_rejected():
    r = run("What are the main risk signals for Microsoft?", label="risk")  # no store
    assert (r.requested[0].status, r.requested[0].reason) == ("INVALID", "QUOTE_NOT_CHECKABLE")


def test_explain_gap_still_shows_the_verified_figure():  # D7
    r = run("Why did Microsoft's revenue increase in fiscal 2024?", label="explain")
    explain = r.requested[0]
    assert (explain.goal.kind, explain.status, explain.reason) == ("explain", "GAP",
                                                                   "EVIDENCE_GAP")
    assert [s.tool for s in r.trace] == ["reported_verified", "explain_movement"]
    assert "Verified figure | MSFT FY2024 revenue: USD 1,000.0m" in r.answer
    assert r.status == "EVIDENCE_GAP"


def test_explain_answer_with_its_change_and_quote():
    def explained(issuer, period, metric, as_of):
        f = StubTools()._f(issuer, period, as_of)
        q = Quote(f"{f.accession}:ITEM_7:0001", f.accession, "Revenue increased due to cloud.",
                  "ITEM_7", 0.5, "sha")
        return Outcome("VERIFIED", "EXPLAINED", (
            answer(f, metric, Decimal(1000)),
            answer(f, f"{metric}_change", Decimal(100), classification="CALCULATION",
                   quotes=(q,))))

    r = run("Why did Microsoft's revenue increase in fiscal 2024?", StubTools(
        overrides={"explain_movement": explained}), label="explain",
        quote_checks=lambda kind: (lambda o, f: None if kind == "explain" else "NO"))
    explain = r.requested[0]
    assert explain.status == "VERIFIED" and [o.metric for o in explain.observations] == [
        "revenue", "revenue_change"]
    assert "Quote | ITEM_7" in r.answer and "Revenue increased due to cloud." in r.answer
