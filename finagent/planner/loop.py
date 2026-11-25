"""The investigation loop (blueprint 8.15 loop rules; Phase 6.4; the prototype's loop, ported).

Each iteration:
1. conditional goals: a debt goal whose liquidity shortfall goal is VERIFIED with a shortfall
   above zero is "met" (its statements prerequisite is added); no shortfall makes it SKIPPED;
   a liquidity goal that ends without a verified value makes it GAP;
2. eligible actions: for every open goal, the registry's actions of its kind whose trigger is
   the goal's trigger, not yet tried for that goal, whose prerequisite goal is VERIFIED; a
   prerequisite that ended unverified turns the goal into a GAP with the prerequisite's reason;
3. the eligible action with the lowest priority runs (ties: goal order). An identical call made
   earlier in the run is reused, not repeated (recorded on the earlier trace step, not counted
   against the budget); otherwise it costs one step of the budget;
4. the outcome goes through the observation gate and updates the goal.

The loop stops when no action is eligible or the budget is spent. Stop reasons: goals_resolved,
evidence_gap (every goal ended, not all verified or skipped), no_eligible_action, step_limit.
"""

import json
import time
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import date
from typing import Any

from finagent.config import (AnswerSpec, IntentVocabulary, PlannerConfig, load_retrieval_config,
                             load_signal_config)
from finagent.planner.gate import GateContext, GateDecision, build_context, gate
from finagent.planner.registry import (Resolver, actions_for, arguments, condition_goal,
                                       prerequisite, signature)
from finagent.planner.state import RESOLVED, Call, GoalState, RunState
from finagent.records import Action, Goal, Outcome, TraceStep
from finagent.retrieval.pinned import PinnedChunks, QuoteCheck, quote_checker

VERIFIED, GAP, SKIPPED, INVALID = "VERIFIED", "GAP", "SKIPPED", "INVALID"
GOALS_RESOLVED, EVIDENCE_GAP = "goals_resolved", "evidence_gap"
NO_ELIGIBLE_ACTION, STEP_LIMIT = "no_eligible_action", "step_limit"


def plain(value: Any) -> Any:
    """JSON-safe copy (dates and Decimals as strings)."""
    return json.loads(json.dumps(value, default=str))


def goal_record(goal: Goal) -> dict[str, Any]:
    return plain(asdict(goal))


def describe(goal: Goal) -> str:
    parts = [goal.issuer, goal.kind, goal.metric or "-", f"period={goal.period}"]
    if goal.scenario:
        parts.append(f"scenario={goal.scenario}")
    if goal.kind in ("forecast", "liquidity", "debt"):
        parts.append(f"target={goal.target}")
    if goal.conditional:
        parts.append("conditional")
    return " ".join(parts)


def condition(goal: Goal, state: RunState) -> str:
    """met / unmet / wait / unknown for a conditional (debt) goal."""
    source = state.goals.get(condition_goal(goal))
    if source is None:
        return "unknown"
    if not source.terminal:
        return "wait"
    if source.status != VERIFIED or not source.observations:
        return "unknown"
    value = source.observations[0].value
    return "met" if value is not None and value > 0 else "unmet"


def explain_answers(metric: str, config: PlannerConfig) -> AnswerSpec:
    """An explain answer: the metric is checked as a fact (the fact entry), its change from the
    previous year as the explain entry (7.3)."""
    fact, change = config.answers["fact"], config.answers["explain"]
    return AnswerSpec(fact.unit, fact.classification,
                      {**fact.by_metric, f"{metric}_change": (change.unit, change.classification)})


def gate_context(goal: Goal, as_of: date, tools: Any, config: PlannerConfig,
                 vocabulary: IntentVocabulary,
                 quote_check: QuoteCheck | None = None) -> GateContext:
    """The gate's view of one goal: requested and allowed metrics, the issuer's filings."""
    answers = config.answers[goal.kind]
    if goal.kind == "statements" or (not goal.metric and answers.metrics):  # #155: debt, risk
        metrics, required, allowed = answers.metrics, answers.required, answers.metrics
    elif goal.kind == "explain":
        metrics = required = (goal.metric, f"{goal.metric}_change")
        allowed = tuple(vocabulary.metrics.get("fact", {})) + (f"{goal.metric}_change",)
        answers = explain_answers(goal.metric, config)
    else:
        metrics = (goal.metric,) if goal.metric else ()
        required = metrics
        allowed = tuple(vocabulary.metrics.get(goal.kind, {}))
    return build_context(goal, as_of, tools.filings(goal.issuer, as_of), answers, allowed,
                         metrics, required, quote_check)


def pinned_quote_checks(tools: Any) -> Callable[[str], QuoteCheck | None]:
    """goal kind -> the gate's quote check over the tools' pinned evidence store (#157): the
    sections a kind may quote (risk: Items 7 and 8; explain: Item 7; any other: none). Without
    a store no quote can be checked (the gate then rejects any quote)."""
    store = getattr(tools, "store", None)
    state: dict[str, Any] = {}

    def check(kind: str) -> QuoteCheck | None:
        if store is None:
            return None
        if not state:
            state["pinned"] = PinnedChunks(store, load_retrieval_config())
            state["evidence"] = load_signal_config().evidence
        ev = state["evidence"]
        sections = {"risk": ev.sections, "explain": ev.explain_sections}.get(kind, ())
        return quote_checker(state["pinned"], sections, ev.max_words)

    return check


def decision_text(goal: Goal, action: Action, state: GoalState, run: RunState) -> str:
    if goal.conditional:
        source = run.goals[condition_goal(goal)].observations[0]
        return (f"Liquidity shortfall {source.value:,.0f} USD in FY{source.fiscal_year} "
                f"({goal.scenario}); condition met, run the debt-service action.")
    if state.trigger != "start":
        return f"Primary reading missing ({state.trigger}); try the approved fallback."
    if action.prerequisite:
        return f"Goal unmet; prerequisite {action.prerequisite} is VERIFIED."
    return "Goal unmet; eligible evidence action."


def gate_records(decisions: tuple[GateDecision, ...]) -> tuple[dict[str, Any], ...]:
    return tuple({"metric": d.metric, "accepted": d.accepted, "code": d.code}
                 for d in decisions)


class Loop:
    """One run of the loop over a RunState (module docstring)."""

    def __init__(self, state: RunState, tools: Any, config: PlannerConfig,
                 vocabulary: IntentVocabulary, resolve: Resolver, max_steps: int,
                 clock: Callable[[], float] = time.perf_counter,
                 quote_checks: Callable[[str], QuoteCheck | None] | None = None) -> None:
        self.state, self.tools, self.config, self.vocabulary = state, tools, config, vocabulary
        self.resolve, self.max_steps, self.clock = resolve, max_steps, clock
        self.quote_checks = quote_checks or pinned_quote_checks(tools)

    # ------------------------------------------------------------ goal updates
    def settle_conditions(self) -> None:
        for s in list(self.state.goals.values()):
            if s.terminal or not s.goal.conditional:
                continue
            c = condition(s.goal, self.state)
            if c == "unknown":
                s.update(Outcome(GAP, "CONDITION_UNKNOWN",
                                 detail="the liquidity goal ended without a verified shortfall"))
            elif c == "unmet":
                s.update(Outcome(SKIPPED, "NO_SHORTFALL",
                                 detail="the verified scenario shows no cash shortfall"))
            elif c == "met":
                for a in actions_for(s.goal.kind, self.config):
                    if a.prerequisite:
                        self.state.add(prerequisite(s.goal, a.prerequisite, self.resolve), False)

    def eligible(self) -> list[tuple[int, int, GoalState, Action]]:
        found = []
        for index, s in enumerate(list(self.state.goals.values())):
            if s.terminal or (s.goal.conditional and condition(s.goal, self.state) != "met"):
                continue
            for action in actions_for(s.goal.kind, self.config):
                if action.trigger != s.trigger or action.name in s.attempted:
                    continue
                if action.prerequisite:
                    dep = self.state.goals.get(prerequisite(s.goal, action.prerequisite,
                                                            self.resolve))
                    if dep is None:
                        continue
                    if dep.status != VERIFIED:
                        if dep.terminal:
                            s.update(Outcome(GAP, f"PREREQUISITE_{dep.status}",
                                             detail=f"{action.prerequisite}: {dep.reason}"
                                             + (f" ({dep.detail})" if dep.detail else "")))
                            break
                        continue
                found.append((action.priority, index, s, action))
        return found

    # ------------------------------------------------------------ one step
    def call(self, action: Action, args: dict[str, Any]) -> tuple[Outcome, float]:
        start = self.clock()
        try:
            outcome = getattr(self.tools, action.name)(**args, as_of=self.state.as_of)
        except Exception as exc:  # reported in the trace, never hidden; no value accepted
            outcome = Outcome(INVALID, "TOOL_ERROR", detail=f"{type(exc).__name__}: {exc}")
        return outcome, (self.clock() - start) * 1000.0

    def gated(self, s: GoalState, raw: Outcome) -> tuple[Outcome, tuple[GateDecision, ...]]:
        try:
            ctx = gate_context(s.goal, self.state.as_of, self.tools, self.config,
                               self.vocabulary, self.quote_checks(s.goal.kind)
                               if any(getattr(o, "quotes", ()) for o in raw.observations)
                               else None)
            return gate(raw, ctx)
        except Exception as exc:
            return Outcome(INVALID, "GATE_ERROR", detail=f"{type(exc).__name__}: {exc}"), ()

    def step(self, s: GoalState, action: Action, eligible: list[str]) -> bool:
        """Run or reuse one action; False when the budget is spent."""
        run = self.state
        args = arguments(s.goal, action, self.resolve)
        key = signature(action, args, run.as_of)
        if key in run.calls:
            call = run.calls[key]
            outcome, decisions = self.gated(s, call.outcome)
            s.attempted.add(action.name)
            s.gaps = call.outcome.gaps
            s.update(outcome, decisions)
            s.steps.append(call.step)
            rejected = [d.code for d in decisions if not d.accepted]
            note = f"{describe(s.goal)}: {outcome.status}" + (
                f" (gate {', '.join(rejected)})" if rejected else "")
            run.trace[call.step - 1] = replace(run.trace[call.step - 1],
                                               reused_by=run.trace[call.step - 1].reused_by
                                               + (note,))
            run.reused.append({"step": call.step, "goal": goal_record(s.goal),
                               "status": outcome.status})
            return True
        if run.steps_used >= self.max_steps:
            return False
        decision = decision_text(s.goal, action, s, run)
        s.attempted.add(action.name)
        raw, latency = self.call(action, args)
        outcome, decisions = self.gated(s, raw)
        number = run.steps_used + 1
        run.calls[key] = Call(raw, number)
        run.trace.append(TraceStep(
            number, action.name, plain(dict(args, as_of=run.as_of)), tuple(eligible), decision,
            outcome.status, (), tuple(plain(asdict(o)) for o in outcome.observations),
            round(latency, 1), goal_record(s.goal), outcome.reason, run.as_of.isoformat(),
            gate_records(decisions)))
        s.gaps = raw.gaps
        s.update(outcome, decisions)
        s.steps.append(number)
        return True

    # ------------------------------------------------------------ the loop
    def run(self) -> str:
        while True:
            self.settle_conditions()
            eligible = self.eligible()
            if not eligible:
                break
            _, _, s, action = min(eligible, key=lambda e: (e[0], e[1]))
            if not self.step(s, action, [a.name for _, _, _, a in eligible]):
                break
        return self.finish()

    def finish(self) -> str:
        """Close open goals as gaps and name the stop reason."""
        open_goals = self.state.open()
        if open_goals:
            stop = STEP_LIMIT if self.state.steps_used >= self.max_steps else NO_ELIGIBLE_ACTION
            for s in open_goals:
                s.update(Outcome(GAP, f"STOPPED_{stop.upper()}",
                                 detail=f"investigation stopped: {stop.replace('_', ' ')}"))
            return stop
        if all(s.status in RESOLVED for s in self.state.requested()):
            return GOALS_RESOLVED
        return EVIDENCE_GAP


def run_status(states: list[GoalState]) -> str:
    """COMPLETE (all verified or skipped), PARTIAL (some verified) or EVIDENCE_GAP."""
    if states and all(s.status in RESOLVED for s in states):
        return "COMPLETE"
    if any(s.status == VERIFIED for s in states):
        return "PARTIAL"
    return "EVIDENCE_GAP"
