"""E8 evidence replays (blueprint 10.6): spec, scoring categories, metrics, and one replay on
view-aware stub tools (no evidence is read)."""

import json
from dataclasses import dataclass, field, replace
from decimal import Decimal

import pytest
import yaml
from planner_support import resources
from statements_support import obs
from tools_support import AS_OF, StubTools, answer, by_year, filings

from finagent.config import ConfigError
from finagent.evaluation import e8
from finagent.evaluation.e8 import CaseResult, Reference
from finagent.records import Outcome
from finagent.statements.replay import (FULL, NO_COMPANYFACTS, NO_DOCUMENT,
                                        SINGLE_CHANNEL_INPUTS, UNTAGGED, EvidenceView)

R = resources()
SPEC = e8.load_e8_spec()
FS = filings()
F25 = by_year(FS, 2025)
BILLION = Decimal("1000000000")
REF = Reference(2025, F25.accession, F25.filed, F25.end,
                {f.accession: (f.fiscal_year, f.filed) for f in FS},
                {"revenue": obs("revenue", BILLION, tag_value=BILLION, document_value=BILLION,
                                scale=6),
                 "inventory": obs("inventory", None, status="DISAGREE",
                                  channels=("document", "tag"), tag_value=Decimal(7),
                                  document_value=Decimal(8))})


def case(condition="hidden:document", kind="fact", metric="revenue", status="VERIFIED",
         reason="AGREE", answers=(), root=None, actions=("reported_verified",), rejected=(),
         detail="") -> CaseResult:
    root_kind, root_reason = root or (kind, reason)
    return CaseResult("MSFT", condition, kind, metric, status, reason, detail, root_kind,
                      root_reason, tuple(answers), tuple(actions), tuple(rejected))


def fact(value=BILLION, channels=("document", "tag"), flags=(), **kw):
    return answer(F25, "revenue", value, channels=channels, flags=flags, **kw)


def projected(value=1100.0, flags=()):
    return answer(F25, "revenue", value, classification="CALCULATION_FROM_MODEL_ASSUMPTION",
                  fiscal_year=2026, projected=True, scenario="Base", flags=flags)


def classify(c, complete, view):
    return e8.classify(c, complete, view, REF, SPEC, AS_OF)


COMPLETE_FACT = case("complete", answers=(fact(),))


# ---------------------------------------------------------------- spec

def test_spec_loads_with_the_complete_view_first():
    assert SPEC.conditions == (FULL, NO_COMPANYFACTS, UNTAGGED, NO_DOCUMENT)
    assert set(SPEC.goals) == set(e8.KINDS)
    assert SPEC.goals["forecast"].scenario == "Base" and SPEC.goals["credit"].metrics
    assert "NO_IDENTITY_CHECK" in SPEC.evidence_reasons["fact"]
    assert "NOT_REPORTED" not in SPEC.evidence_reasons["fact"]


def _write(tmp_path, change):
    data = yaml.safe_load(e8.E8_PATH.read_text())
    change(data)
    path = tmp_path / "e8.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


@pytest.mark.parametrize("change", [
    lambda d: d.update(conditions=["hidden:document", "complete"]),
    lambda d: d.update(conditions=["complete", "hidden:weather"]),
    lambda d: d["goals"].update(debt={"metrics": [], "max_steps": 12}),
    lambda d: d["goals"]["fact"].update(metrics=["weather"]),
    lambda d: d["goals"]["credit"].update(scenario="Base"),
    lambda d: d["goals"]["forecast"].update(scenario=""),
    lambda d: d["goals"]["fact"].update(max_steps=31),
])
def test_spec_rejects(tmp_path, change):
    with pytest.raises(ConfigError):
        e8.load_e8_spec(_write(tmp_path, change))


def test_goal_set_and_tickers():
    assert e8.goal_set("MSFT", 2025, SPEC.goals["statements"])[0].period == 2025
    forecast = e8.goal_set("MSFT", 2025, SPEC.goals["forecast"])
    assert {g.period for g in forecast} == {"latest"} and {g.scenario for g in forecast} == \
        {"Base"}
    e8.check_tickers(["MSFT"], {"MSFT": "validation"})
    with pytest.raises(ValueError, match="held out"):
        e8.check_tickers(["HOLD"], {"HOLD": "held_out"})
    with pytest.raises(ValueError):
        e8.check_tickers(["AAPL"], {"AAPL": "train"})


# ---------------------------------------------------------------- categories

def test_same_value_with_the_right_flag_is_correct():
    tag_only = case(answers=(fact(channels=("tag",), flags=("SINGLE_CHANNEL",)),),
                    actions=("reported_verified", "reported_tag_only"))
    assert classify(tag_only, COMPLETE_FACT, NO_DOCUMENT).category == "SAME_VALUE"
    same = case("hidden:companyfacts", answers=(fact(),))
    assert classify(same, COMPLETE_FACT, NO_COMPANYFACTS).correct


def test_wrong_flag_and_leak():
    unflagged = case(answers=(fact(channels=("tag",)),))
    assert classify(unflagged, COMPLETE_FACT, NO_DOCUMENT).category == "WRONG_FLAG"
    leak = case(answers=(fact(),))  # both channels cited with the document hidden
    assert classify(leak, COMPLETE_FACT, NO_DOCUMENT).category == "LEAK"
    model = case(kind="forecast", answers=(projected(),))
    complete = case("complete", kind="forecast", answers=(projected(),))
    assert classify(model, complete, NO_DOCUMENT).category == "WRONG_FLAG"
    flagged = case(kind="forecast", answers=(projected(flags=(SINGLE_CHANNEL_INPUTS,)),))
    assert classify(flagged, complete, NO_DOCUMENT).category == "SAME_VALUE"
    assert classify(flagged, complete, NO_COMPANYFACTS).category == "WRONG_FLAG"


def test_a_different_value_is_fabricated_unless_display_rounding():
    other = case(answers=(fact(BILLION + 1, channels=("tag",), flags=("SINGLE_CHANNEL",)),))
    s = classify(other, COMPLETE_FACT, NO_DOCUMENT)
    assert (s.category, s.fabricated) == ("VALUE_DIFFERS", True)
    rounded = case("hidden:companyfacts+inline_tags",
                   answers=(fact(BILLION + 400_000, channels=("document",),
                                 flags=("SINGLE_CHANNEL",)),))
    assert classify(rounded, COMPLETE_FACT, UNTAGGED).category == "SAME_VALUE"
    far = replace(rounded, answers=(fact(BILLION + 600_000, channels=("document",),
                                         flags=("SINGLE_CHANNEL",)),))
    assert classify(far, COMPLETE_FACT, UNTAGGED).fabricated
    model = case(kind="forecast", answers=(projected(1100.5, (SINGLE_CHANNEL_INPUTS,)),))
    complete = case("complete", kind="forecast", answers=(projected(),))
    assert classify(model, complete, NO_DOCUMENT).category == "VALUE_DIFFERS"


def test_stale_values():
    late = case(answers=(fact(channels=("tag",), flags=("SINGLE_CHANNEL",),
                              fiscal_year=2024),))
    s = classify(late, COMPLETE_FACT, NO_DOCUMENT)
    assert (s.category, s.stale) == ("STALE", True) and "fiscal year 2024" in s.problems[0]
    old = by_year(FS, 2024)
    moved = case(answers=(replace(fact(channels=("tag",), flags=("SINGLE_CHANNEL",)),
                                  accession=old.accession),))
    assert classify(moved, COMPLETE_FACT, NO_DOCUMENT).category == "STALE"
    ratio = case(kind="credit", metric="current_ratio", answers=(replace(answer(
        F25, "current_ratio", 1.5, unit="x", classification="CALCULATION",
        flags=(SINGLE_CHANNEL_INPUTS,)), sources=("unknown-accession",)),))
    assert classify(ratio, None, NO_DOCUMENT).category == "STALE"


def test_new_values_are_traced_to_the_complete_readings():
    complete = case("complete", metric="inventory", status="CONFLICT", reason="VALUES_DIFFER")
    tag = case(metric="inventory", answers=(replace(fact(Decimal(7), channels=("tag",),
                                                         flags=("SINGLE_CHANNEL",)),
                                                    metric="inventory"),))
    s = classify(tag, complete, NO_DOCUMENT)
    assert (s.category, s.fabricated) == ("NEW_VALUE", False)
    made_up = replace(tag, answers=(replace(tag.answers[0], value=Decimal(9)),))
    assert classify(made_up, complete, NO_DOCUMENT).fabricated


def test_refusals():
    complete_gap = case("complete", status="GAP", reason="NOT_REPORTED")
    assert classify(case(status="GAP", reason="NOT_REPORTED"), complete_gap,
                    NO_DOCUMENT).category == "SAME_OUTCOME"
    untagged = "hidden:companyfacts+inline_tags"
    assert classify(case(untagged, status="GAP", reason="NO_IDENTITY_CHECK"), COMPLETE_FACT,
                    UNTAGGED).category == "EVIDENCE_GAP"
    assert classify(case(untagged, status="GAP", reason="NOT_REPORTED"), COMPLETE_FACT,
                    UNTAGGED).category == "OTHER_REASON"
    chained = case(untagged, kind="forecast", status="GAP", reason="PREREQUISITE_GAP",
                   root=("statements", "FINAL_CHECK_GAP"))
    assert classify(chained, None, UNTAGGED).category == "EVIDENCE_GAP"
    invalid = case(untagged, kind="statements", metric="", status="INVALID",
                   reason="FINAL_CHECK_INVALID")
    assert classify(invalid, None, UNTAGGED).category == "OTHER_REASON"
    error = case(status="INVALID", reason="TOOL_ERROR", detail="ValueError: boom")
    assert classify(error, COMPLETE_FACT, NO_DOCUMENT).category == "ERROR"
    rejected = case(status="INVALID", reason="UNIT_MISMATCH", rejected=("UNIT_MISMATCH",))
    assert classify(rejected, COMPLETE_FACT, NO_DOCUMENT).category == "GATE_REJECTED"


def test_optional_residual_not_returned_is_still_the_same_value():
    def residuals(*ids, flags=()):
        return tuple(answer(F25, i, Decimal(0), classification="CALCULATION", flags=flags)
                     for i in ids)

    complete = case("complete", kind="statements", metric="",
                    answers=residuals("I9", "I10", "I11"))
    degraded = case(kind="statements", metric="",
                    answers=residuals("I9", "I10", flags=(SINGLE_CHANNEL_INPUTS,)))
    s = classify(degraded, complete, NO_DOCUMENT)
    assert s.category == "SAME_VALUE" and "I11" in s.problems[0] and not s.fabricated


# ---------------------------------------------------------------- metrics

def test_metrics_rates_fallback_and_reference():
    cases = [COMPLETE_FACT,
             case(answers=(fact(channels=("tag",), flags=("SINGLE_CHANNEL",)),),
                  actions=("reported_verified", "reported_tag_only")),
             case("hidden:companyfacts+inline_tags", status="GAP", reason="NOT_REPORTED",
                  actions=("reported_verified",)),
             case("hidden:companyfacts", answers=(fact(),))]
    scored = e8.score(cases, {"MSFT": REF}, SPEC, AS_OF)
    m = e8.metrics(scored, SPEC)
    assert m["complete"]["with_value"] == 1 and m["complete"]["problems"] == 0
    assert m["hidden:document"]["correct"]["rate"] == 1.0
    assert m["hidden:document"]["fallback_correct"]["n"] == 1
    assert m["hidden:companyfacts+inline_tags"]["categories"] == {"OTHER_REASON": 1}
    assert m["degraded"]["correct"]["k"] == 2 and m["degraded"]["correct"]["n"] == 3
    assert m["degraded"]["fabricated"] == 0 and m["degraded"]["by_kind"]["fact"]["n"] == 3


def test_pinned_problems(tmp_path):
    statements = tmp_path / "statements.json"
    statements.write_text(json.dumps({"companies": [
        {"issuer": "MSFT", "observations": [
            {"fiscal_year": 2025, "metric": "revenue", "value": str(BILLION), "accepted": True}]},
        {"issuer": "OTHER", "observations": []}]}))
    projections = tmp_path / "projections.csv"
    projections.write_text("ticker,split,scenario,fiscal_year,line,value\n"
                           "MSFT,validation,Base,2026,revenue,1100.0\n"
                           "MSFT,validation,Severe,2026,revenue,900.0\n")
    cases = [COMPLETE_FACT, case("complete", kind="forecast", answers=(projected(),)),
             case("complete", kind="forecast", metric="cash", status="GAP", reason="X")]
    p = e8.pinned_problems(cases, {"MSFT": REF}, statements, projections, "complete", 1e-9)
    assert p["checked"] == 3 and p["differences"] == []
    wrong = [replace(COMPLETE_FACT, answers=(fact(BILLION + 1),))]
    assert e8.pinned_problems(wrong, {"MSFT": REF}, statements, None, "complete",
                              1e-9)["differences"]


# ---------------------------------------------------------------- one replay on stub tools

@dataclass
class _Set:
    readings: dict

    def get(self, year, metric):
        return self.readings.get(metric)


@dataclass
class ViewStubTools(StubTools):
    """Stub tools whose answers follow the view, as the replay hook makes the real tools do."""

    view: EvidenceView = FULL
    reconciliations: dict = field(default_factory=dict)
    forecasts: dict = field(default_factory=dict)

    def statement_set(self, issuer, as_of):
        return _Set(REF.readings)

    def _reported_verified(self, issuer, period, metric, as_of):
        if not self.view.document:
            return Outcome("MISSING_PRIMARY", "DOC_MISSING")
        if self.view.untagged:
            return Outcome("MISSING_PRIMARY", "TAG_MISSING")
        return super()._reported_verified(issuer, period, metric, as_of)

    def _reported_tag_only(self, issuer, period, metric, as_of):
        f = self._f(issuer, period, as_of)
        return Outcome("VERIFIED", "TAG_ONLY", (answer(
            f, metric, BILLION, validated=False, channels=("tag",),
            flags=("TAG_ONLY", "SINGLE_CHANNEL")),))

    def _reconcile(self, issuer, period, as_of):
        if self.view.untagged:
            return Outcome("GAP", "FINAL_CHECK_GAP", detail="I10 not computable")
        out = super()._reconcile(issuer, period, as_of)
        if self.view.single_channel:
            out = replace(out, observations=tuple(replace(o, flags=(SINGLE_CHANNEL_INPUTS,))
                                                  for o in out.observations))
        return out

    def _projected(self, issuer, scenario, target, as_of, values):
        out = super()._projected(issuer, scenario, target, as_of, values)
        if self.view.single_channel:
            out = replace(out, observations=tuple(replace(o, flags=(SINGLE_CHANNEL_INPUTS,))
                                                  for o in out.observations))
        return out

    def _credit_ratios(self, issuer, period, as_of):
        out = super()._credit_ratios(issuer, period, as_of)
        if self.view.single_channel:
            out = replace(out, observations=tuple(
                replace(o, flags=o.flags + (SINGLE_CHANNEL_INPUTS,)) for o in out.observations))
        return out


def small_spec():
    goals = {"fact": e8.GoalSpec("fact", ("revenue",), max_steps=30),
             "statements": e8.GoalSpec("statements", ()),
             "forecast": e8.GoalSpec("forecast", ("revenue", "cash"), "Base"),
             "credit": e8.GoalSpec("credit", ("current_ratio", "ebit_to_gross_interest",
                                              "quick_ratio"))}
    return replace(SPEC, goals=goals)


def test_replay_on_stub_tools(tmp_path):
    spec = small_spec()
    tools = ViewStubTools()
    tools.reconciliations[("MSFT", AS_OF, FULL)] = "cached"
    cases, ref, runs = e8.run_company(tools, "MSFT", AS_OF, spec, R, "e8-test", tmp_path)
    assert ref is not None and ref.origin == 2025 and not tools.reconciliations  # dropped
    assert len(runs) == len(spec.conditions) * len(spec.goals)
    assert all(r["stop_reason"] != "step_limit" for r in runs)
    scored = e8.score(cases, {"MSFT": ref}, spec, AS_OF)
    m = e8.metrics(scored, spec)
    assert m["degraded"]["fabricated"] == 0 and m["degraded"]["stale"] == 0
    assert m["hidden:document"]["correct"]["rate"] == 1.0
    assert m["hidden:document"]["fallback_correct"]["n"] == 1
    untagged = m["hidden:companyfacts+inline_tags"]
    # fact NO_IDENTITY_CHECK, statements FINAL_CHECK_GAP, and every forecast and credit goal
    # through the failed statements prerequisite
    assert untagged["categories"] == {"EVIDENCE_GAP": 7}
    by_case = {(x.case.condition, x.case.kind, x.case.metric): x for x in scored}
    assert by_case[("hidden:companyfacts+inline_tags", "forecast", "revenue")].case.root_kind \
        == "statements"
    assert by_case[("hidden:document", "credit", "quick_ratio")].category == "SAME_OUTCOME"
    text = e8.render_report({"run_id": "e8-test", "git_commit": "abc", "git_dirty": False,
                             "evidence_manifest_sha256": "0" * 64, "as_of": "2026-11-24",
                             "model_version": "stub", "companies": ["MSFT"],
                             "origin_years": {"MSFT": 2025}, "bootstrap_draws": 10,
                             "conditions": [v.name for v in spec.conditions]},
                            m, scored, {"checked": 0, "differences": []}, runs)
    assert "## 1. Correct rate per condition" in text and "hidden:document" in text
    e8.write_run(tmp_path / "run", {"run_id": "e8-test"}, scored, m, runs)
    assert (tmp_path / "run" / "cases.jsonl").read_text().count("\n") == len(scored)
