"""E5 planner evaluation (blueprint 10.4): normalisation, slot counts, answer comparison, the
gate and fabrication check, metrics, report, and one question end to end on stub tools (no
evidence is read, no gold question is run)."""

import json
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

from planner_support import resources
from tools_support import AS_OF, StubTools, answer, by_year, filings

from finagent.evaluation import e5
from finagent.evaluation.e5 import Pinned, QuestionScore
from finagent.planner.compile import GrammarParser
from finagent.planner.gate import GateDecision
from finagent.records import Clarification, Compilation, Goal

R = resources()
FS = filings()
F25 = by_year(FS, 2025)
ORIGINS = {"MSFT": 2025, "AMZN": 2025}
UNIVERSE = {"MSFT": "validation", "AMZN": "validation", "ZZZZ": "held_out"}
BILLION = Decimal("1000000000")


def gold(**kw):
    base = {"issuer": "MSFT", "period": 2024, "kind": "fact", "metric": "revenue",
            "scenario": "", "target": None, "conditional": False}
    return {**base, **kw}


def record(question="What was Microsoft's revenue in FY2024?", goals=None, expected="GOALS",
           category="", end_to_end=True, **kw):
    return {"id": "Q900", "cell": "C900", "question": question, "selected_issuer": "",
            "author": "vineet", "style": "paraphrase", "trap_type": "", "expected": expected,
            "clarification_category": category,
            "goals": [gold()] if goals is None else goals, "end_to_end": end_to_end,
            "notes": "", **kw}


def score(**kw) -> QuestionScore:
    base = dict(id="Q1", cell="C1", author="vineet", group="fact", variant="", question="q",
                selected_issuer="", expected="GOALS", gold_category="", end_to_end=True,
                gold=(("fact", "MSFT", "revenue", 2024, "", None, False),),
                predicted=(("fact", "MSFT", "revenue", 2024, "", None, False),),
                outcome="GOALS", predicted_category="", reason="", clause="", clauses=(),
                goals_match=True, compiled_correct=True, status="COMPLETE",
                stop_reason="goals_resolved", answer="a", reference_status="COMPLETE",
                answer_correct=True, answer_differences=(), values=1, problems=(),
                seconds=0.1)
    return QuestionScore(**{**base, **kw})


# ---------------------------------------------------------------- goals and slots

def test_normalise_goal_resolves_projected_years_on_both_sides():
    blank = Goal("MSFT", "latest", "forecast", "revenue", "Base", None)
    explicit = replace(blank, target=2026)
    assert e5.normalise_goal(blank, ORIGINS) == e5.normalise_goal(explicit, ORIGINS)
    assert e5.normalise_goal(replace(blank, target="+2"), ORIGINS)[5] == 2027
    assert e5.normalise_goal(replace(blank, target=2027), ORIGINS) != \
        e5.normalise_goal(blank, ORIGINS)
    fact = Goal("MSFT", "latest", "fact", "revenue")
    assert e5.normalise_goal(fact, ORIGINS) == ("fact", "MSFT", "revenue", "latest", "", None,
                                                False)  # period compares strictly
    assert e5.normalise_goal(blank, {})[5] is None  # unknown origin: left as written


def test_origin_years_reads_validation_issuers_only():
    tools = StubTools()
    assert e5.origin_years(tools, ["MSFT", "ZZZZ"], AS_OF, UNIVERSE) == {"MSFT": 2025,
                                                                          "ZZZZ": None}
    assert not tools.calls


def test_slot_counts_and_differences():
    g = (("fact", "MSFT", "revenue", 2024, "", None, False),
         ("fact", "AMZN", "revenue", 2024, "", None, False))
    p = (("fact", "MSFT", "revenue", "latest", "", None, False),)
    counts = e5.slot_counts(g, p)
    assert counts["kind"] == (1, 0, 1) and counts["issuer"] == (1, 0, 1)
    assert counts["period"] == (0, 1, 2)
    assert e5.slot_counts(g, ())["metric"] == (0, 0, 2)
    assert e5.slot_counts((), p)["metric"] == (0, 1, 0)
    diffs = e5.slot_differences(g, p)
    assert any(d.startswith("issuer: gold ['AMZN']") for d in diffs)
    assert any(d.startswith("period:") for d in diffs)
    assert e5.describe_goal(g[0]) == "MSFT fact revenue p=2024"


def test_score_compilation_goals_and_clarifications():
    goals = (Goal("MSFT", 2024, "fact", "revenue"),)
    ok = e5.score_compilation(record(), Compilation("q", "m0", goals), ORIGINS)
    assert ok["goals_match"] and ok["compiled_correct"] and ok["outcome"] == "GOALS"
    wrong = e5.score_compilation(record(), Compilation("q", "m0", (replace(goals[0],
                                                                           period=2023),)),
                                 ORIGINS)
    assert not wrong["goals_match"] and not wrong["compiled_correct"]
    stop = Compilation("q", "m0", clarification=Clarification("ADVICE", "advice", "no", ""))
    clar = record(goals=[], expected="CLARIFICATION", category="advice", end_to_end=False)
    assert e5.score_compilation(clar, stop, ORIGINS)["compiled_correct"]
    other = {**clar, "clarification_category": "unsupported"}
    assert not e5.score_compilation(other, stop, ORIGINS)["compiled_correct"]
    missed = e5.score_compilation(record(), stop, ORIGINS)
    assert missed["outcome"] == "CLARIFICATION" and missed["predicted"] == ()
    assert not missed["compiled_correct"]


# ---------------------------------------------------------------- answers

def _inv(results):
    return SimpleNamespace(requested=tuple(results), goals=tuple(results))


def _result(goal, status="VERIFIED", reason="AGREE", observations=(), accepted=True):
    decisions = tuple(GateDecision(o.metric, accepted) for o in observations)
    return SimpleNamespace(goal=goal, status=status, reason=reason, observations=observations,
                           decisions=decisions)


def test_answer_key_compares_values_and_outcomes():
    g = Goal("MSFT", 2024, "fact", "revenue")
    f24 = by_year(FS, 2024)
    one = _inv([_result(g, observations=(answer(f24, "revenue", BILLION),))])
    same = _inv([_result(g, observations=(answer(f24, "revenue", Decimal("1.0E+9")),))])
    assert e5.answer_differences(e5.answer_key(one, ORIGINS), e5.answer_key(same, ORIGINS)) == []
    other = _inv([_result(g, observations=(answer(f24, "revenue", Decimal(7)),))])
    diffs = e5.answer_differences(e5.answer_key(other, ORIGINS), e5.answer_key(one, ORIGINS))
    assert len(diffs) == 2 and diffs[0].startswith("missing:")
    latest = Goal("MSFT", "latest", "fact", "revenue")
    assert e5.resolved_year(latest, ORIGINS) == 2025
    assert e5.resolved_year(Goal("MSFT", "latest", "forecast", "", "Base", "+3"), ORIGINS) \
        == 2028


# ---------------------------------------------------------------- pinned values and audit

def pinned() -> Pinned:
    p = Pinned()
    p.facts[("MSFT", 2025, "revenue")] = {BILLION}
    p.accepted[("MSFT", 2025, "revenue")] = BILLION
    p.accepted[("MSFT", 2024, "revenue")] = Decimal(900000000)
    p.lines[("MSFT", "Base", 2026, "revenue")] = 1100.0
    p.lines[("MSFT", "Base", 2026, "cfo")] = 300.0
    p.lines[("MSFT", "Base", 2026, "capex")] = 210.0
    p.ratios[("MSFT", 2025, "current_ratio")] = 1.5
    p.ratios[("MSFT", 2025, "ebit_to_gross_interest")] = None
    p.debt[("MSFT", "Base", 2026)] = {"scoped_dscr": 1.5, "cash_available": 80.0}
    p.identities[("MSFT", 2025, "I9")] = Decimal("0.000")
    p.signals[("MSFT", 2025)] = {"R1": ("FIRED", {"receivables_growth_gap": 22.2}),
                                 "R2": ("NOT_FIRED", {})}
    return p


def test_pinned_check_per_kind():
    p = pinned()
    proj = dict(classification="CALCULATION_FROM_MODEL_ASSUMPTION", fiscal_year=2026,
                projected=True, scenario="Base")
    assert p.check("fact", answer(F25, "revenue", BILLION)) is None
    assert "none of the pinned" in p.check("fact", answer(F25, "revenue", Decimal(5)))
    assert p.check("explain", answer(F25, "revenue_change", Decimal(100000000))) is None
    assert p.check("statements", answer(F25, "I9", Decimal(0))) is None
    assert p.check("forecast", answer(F25, "revenue", 1100.0, **proj)) is None
    assert p.check("forecast", answer(F25, "free_cash_flow", 90.0, **proj)) is None
    assert p.check("liquidity", answer(F25, "revenue", 1101.0, **proj))
    assert p.check("credit", answer(F25, "current_ratio", 1.5, unit="x")) is None
    assert p.check("credit", answer(F25, "ebit_to_gross_interest", None, unit="x")) is None
    assert p.check("credit", answer(F25, "dso", 1.0, unit="days")).startswith("no pinned")
    assert p.check("debt", answer(F25, "cash_available_for_debt_service", 80.0, **proj)) is None
    assert p.check("risk", answer(F25, "signals_fired", Decimal(1))) is None
    assert p.check("risk", answer(F25, "R1.receivables_growth_gap", 22.20001)) is None
    assert "pinned state" in p.check("risk", answer(F25, "R2.dio_change", 1.0))


def test_audit_finds_unaccepted_unvalidated_late_and_untraced_values():
    tools = StubTools()
    g = Goal("MSFT", 2025, "fact", "revenue")
    good = _inv([_result(g, observations=(answer(F25, "revenue", BILLION),))])
    assert e5.audit(good, tools, AS_OF, pinned()) == (1, [])
    bad = replace(answer(F25, "revenue", Decimal(5), validated=False, channels=("tag",)),
                  accession="0000000000-00-000000")
    checked, problems = e5.audit(_inv([_result(g, observations=(bad,), accepted=False)]),
                                 tools, AS_OF, pinned())
    text = " ".join(problems)
    assert checked == 1 and "no accepted gate decision" in text and "not validated" in text
    assert "not a 10-K" in text and "untraced" in text
    single = answer(F25, "revenue", BILLION, validated=False, channels=("tag",),
                    flags=("SINGLE_CHANNEL",))
    assert e5.audit(_inv([_result(g, observations=(single,))]), tools, AS_OF, pinned())[1] == []


def test_pinned_load_reads_only_the_given_tickers(tmp_path):
    runs = tmp_path
    (runs / "s").mkdir()
    (runs / "f").mkdir()
    (runs / "c").mkdir()
    (runs / "i").mkdir()
    obs = {"fiscal_year": 2025, "metric": "revenue", "value": "10", "tag_value": "10",
           "document_value": "1.0E+1", "original_value": None, "candidates": [],
           "accepted": True}
    (runs / "s" / "statements.json").write_text(json.dumps({"companies": [
        {"issuer": "MSFT", "observations": [obs]},
        {"issuer": "ZZZZ", "observations": [obs]}]}))
    (runs / "s" / "identities.csv").write_text(
        "issuer,fiscal_year,identity_id,residual\nMSFT,2025,I9,0\nZZZZ,2025,I9,1\n")
    (runs / "f" / "projections.csv").write_text(
        "ticker,split,scenario,fiscal_year,line,value\nMSFT,validation,Base,2026,revenue,11.5\n")
    (runs / "c" / "ratios.csv").write_text(
        "ticker,split,fiscal_year,ratio_id,value,status,detail\n"
        "MSFT,validation,2025,current_ratio,,NOT_MEANINGFUL,x\n")
    (runs / "c" / "debt_service.csv").write_text(
        "ticker,split,issuer,scenario,fiscal_year,scoped_dscr,status,detail\n"
        "MSFT,validation,MSFT,Base,2026,1.5,VERIFIED,ok\n")
    (runs / "i" / "signals.csv").write_text(
        "ticker,fiscal_year,signal,state,reason,detail,values\n"
        "MSFT,FY2025,R1,FIRED,,,dso_change=3.3; receivables_growth_gap=11.5\n")
    p = Pinned.load(runs, ["MSFT"], "s", "f", "c", "i")
    assert p.facts == {("MSFT", 2025, "revenue"): {Decimal(10)}}
    assert p.identities == {("MSFT", 2025, "I9"): Decimal(0)}
    assert p.lines[("MSFT", "Base", 2026, "revenue")] == 11.5
    assert p.ratios[("MSFT", 2025, "current_ratio")] is None
    assert p.debt[("MSFT", "Base", 2026)]["scoped_dscr"] == 1.5
    assert p.signals[("MSFT", 2025)]["R1"] == ("FIRED", {"dso_change": 3.3,
                                                         "receivables_growth_gap": 11.5})


# ---------------------------------------------------------------- metrics and report

def test_summarise_metrics_and_clarification_rates():
    rs = [score(),
          score(id="Q2", goals_match=False, compiled_correct=False, answer_correct=False,
                predicted=(("fact", "MSFT", "revenue", 2023, "", None, False),)),
          score(id="Q3", author="canonical", outcome="CLARIFICATION", predicted=(),
                goals_match=False, compiled_correct=False, answer_correct=False,
                reason="LOW_CONFIDENCE", predicted_category="unsupported"),
          score(id="Q4", group="advice", expected="CLARIFICATION", gold_category="advice",
                gold=(), predicted=(), outcome="CLARIFICATION", predicted_category="advice",
                end_to_end=False, answer_correct=None, values=0),
          score(id="Q5", group="advice", expected="CLARIFICATION", gold_category="advice",
                gold=(), outcome="GOALS", goals_match=False, compiled_correct=False,
                end_to_end=False, answer_correct=None)]
    s = e5.summarise(rs)
    assert s["goal_accuracy"]["k"] == 1 and s["goal_accuracy"]["n"] == 3
    assert s["coverage"]["k"] == 2 and s["precision"]["rate"] == 0.5
    assert s["false_clarification"]["k"] == 1
    assert s["clarification"]["advice"]["correct"]["k"] == 1
    assert s["clarification"]["advice"]["clarified"]["k"] == 1
    assert s["answered_instead_of_clarifying"]["k"] == 1
    assert s["answer_correct"]["k"] == 1 and s["answer_correct"]["n"] == 3
    assert s["slots"]["period"]["fn"] == 2 and s["slots"]["metric"]["fp"] == 1
    m = e5.metrics(rs)
    assert set(m["by_author"]) == {"vineet", "canonical"} and set(m["by_group"]) == {"fact",
                                                                                     "advice"}
    assert e5.wilson(0, 0) is None and e5.wilson(5, 10)[0] < 0.5 < e5.wilson(5, 10)[1]


def test_mcnemar_and_causes():
    test = e5.mcnemar({"a": True, "b": False, "c": False}, {"a": True, "b": True, "c": True})
    assert (test["a_only_correct"], test["b_only_correct"]) == (0, 2) and test["p_value"] == 0.5
    assert e5.cause(score(outcome="CLARIFICATION", reason="LOW_CONFIDENCE",
                          predicted_category="unsupported", clause="x")).startswith(
        "clarified LOW_CONFIDENCE")
    assert "expected a advice" in e5.cause(score(expected="CLARIFICATION",
                                                 gold_category="advice"))
    assert e5.cause(score(goals_match=False, predicted=())).startswith("kind:")
    assert e5.cause(score(answer_differences=("missing: x",))).startswith("answer differs")
    assert e5.is_error(score(answer_correct=False)) and not e5.is_error(score())


def _header(run_id, compiler):
    return {"run_id": run_id, "compiler": compiler, "compiler_name": compiler,
            "threshold": 0.875 if compiler == "m1" else None, "git_commit": "abc",
            "git_dirty": True, "seconds": 1.0, "gold_file": "gold/intents_test.jsonl",
            "gold_sha256": "a" * 64, "grid_sha256": "b" * 64, "as_of": "2026-11-24",
            "evidence_manifest_sha256": "c" * 64, "pinned": {"statements": "s"}}


def test_write_load_and_render(tmp_path):
    rs = [score(), score(id="Q2", compiled_correct=False, goals_match=False,
                         predicted=(), outcome="CLARIFICATION", reason="LOW_CONFIDENCE",
                         predicted_category="unsupported", answer_correct=False,
                         problems=("MSFT fact revenue FY2024: untraced",),
                         diagnostics=("'x' -> fact 0.500",))]
    e5.write_run(tmp_path / "run", _header("e5-t2", "m1"), rs, e5.metrics(rs))
    loaded = e5.load_scores(tmp_path / "run" / "questions.jsonl")
    assert loaded == rs
    text = e5.render_report([(_header("e5-t1", "m0"), [score()]),
                             (_header("e5-t2", "m1"), loaded)], {"#208": ["Q2"]})
    assert "LLM fallback (blueprint 10.4 pass^k, k = 3): not run" in text
    assert "## 7. Every e5-t2 error (1)" in text and "Q2 #208" in text
    assert "untraced" in text and "'x' -> fact 0.500" in text
    assert "blank target and an explicit year equal to the first projected year" in text
    assert "McNemar" in text and "## 8. e5-t1 errors (0)" in text


# ---------------------------------------------------------------- one question end to end

def test_run_question_end_to_end_on_stub_tools(tmp_path):
    tools = StubTools()
    p = pinned()
    p.facts[("MSFT", 2024, "revenue")] = {BILLION}
    r = e5.run_question(record(), "fact", tools=tools, parser=GrammarParser(), resources=R,
                        as_of=AS_OF, universe=UNIVERSE, pinned=p, run_id="e5-test",
                        trace_dir=tmp_path)
    assert r.compiled_correct and r.goals_match and r.answer_correct
    assert r.values == 1 and r.problems == () and r.reference_status == "COMPLETE"
    clar = record(question="Should I buy Microsoft?", goals=[], expected="CLARIFICATION",
                  category="advice", end_to_end=False)
    c = e5.run_question(clar, "advice", tools=tools, parser=GrammarParser(), resources=R,
                        as_of=AS_OF, universe=UNIVERSE, pinned=p, run_id="e5-test",
                        diagnose=lambda q: ("diag",))
    assert c.compiled_correct and c.outcome == "CLARIFICATION" and c.answer_correct is None
    assert c.diagnostics == ("diag",) and c.reference_status == ""
