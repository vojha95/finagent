"""investigate(question, as_of, ...): the planner's entry point (blueprint 8.15-8.17; Phase 6.4).

compile the question (finagent.planner.compile) -> CLARIFICATION, or goals -> expand defaults,
prerequisites and conditions (finagent.planner.registry) -> the loop with the tools and the
gate (finagent.planner.loop) -> answer (finagent.planner.render) -> trace (jsonl + sqlite,
finagent.planner.trace, when a directory is given).

as_of is required and reaches every tool call and every trace entry. Held-out issuers may be
named in a question (they resolve), but no data is loaded for them: their goals are GAP
HELD_OUT_ISSUER without a tool call. No external AI API is used anywhere.
"""

import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from finagent.planner.compile import (CompilerResources, Parser, compile_question,
                                      load_trained_parser)
from finagent.planner.gate import GateDecision
from finagent.planner.loop import Loop, plain, run_status
from finagent.planner.registry import (actions_for, condition_goal, expand_defaults,
                                       prerequisite)
from finagent.planner.render import Comparison, compare, render
from finagent.planner.slots import read_scenario
from finagent.planner.state import RunState
from finagent.planner.text import normalise
from finagent.planner.trace import write_trace
from finagent.records import AnswerObservation, Compilation, Goal, Outcome, TraceStep

CLARIFICATION = "CLARIFICATION"
SCENARIO_KINDS = ("forecast", "liquidity", "debt")


@dataclass(frozen=True)
class GoalResult:
    goal: Goal
    requested: bool
    status: str
    reason: str
    detail: str
    observations: tuple[AnswerObservation, ...]
    decisions: tuple[GateDecision, ...]
    steps: tuple[int, ...]
    gaps: tuple[tuple[str, str], ...] = ()

    def record(self) -> dict[str, Any]:
        return plain({**asdict(self.goal), "requested": self.requested, "status": self.status,
                      "reason": self.reason, "detail": self.detail, "steps": list(self.steps),
                      "observations": [asdict(o) for o in self.observations],
                      "gate": [asdict(d) for d in self.decisions]})


@dataclass(frozen=True)
class Investigation:
    run_id: str
    question: str
    as_of: date
    compilation: Compilation
    status: str  # COMPLETE / PARTIAL / EVIDENCE_GAP / CLARIFICATION
    stop_reason: str  # goals_resolved / evidence_gap / no_eligible_action / step_limit / ...
    goals: tuple[GoalResult, ...]  # requested goals first, then prerequisites
    trace: tuple[TraceStep, ...]
    answer: str
    comparisons: tuple[Comparison, ...]
    max_steps: int
    reused: int
    scenario_defaulted: bool = False

    @property
    def steps_used(self) -> int:
        return len(self.trace)

    @property
    def requested(self) -> tuple[GoalResult, ...]:
        return tuple(g for g in self.goals if g.requested)


def check_inputs(as_of: Any, max_steps: int, low: int, high: int) -> None:
    if not isinstance(as_of, date) or isinstance(as_of, datetime):
        raise ValueError(f"investigate needs an as_of date, got {as_of!r}")
    if not isinstance(max_steps, int) or not low <= max_steps <= high:
        raise ValueError(f"step budget {max_steps!r} outside {low}-{high}")


def scenario_defaulted(question: str, goals: tuple[Goal, ...],
                       resources: CompilerResources) -> bool:
    """A scenario goal was compiled but the question names no scenario (#120 a)."""
    if not any(g.kind in SCENARIO_KINDS for g in goals):
        return False
    name, ambiguous = read_scenario(normalise(question), resources.vocabulary)
    return not name and not ambiguous


def make_resolver(tools: Any, as_of: date) -> Callable[[str, Any], int | None]:
    """(issuer, period) -> fiscal year from the issuer's filings by as_of (memoised per run)."""
    memo: dict[tuple[str, Any], int | None] = {}

    def resolve(issuer: str, period: Any) -> int | None:
        if (issuer, period) not in memo:
            try:
                memo[(issuer, period)] = tools.resolve_year(issuer, period, as_of)
            except Exception:  # held out or unknown: the tool call reports it
                memo[(issuer, period)] = None
        return memo[(issuer, period)]

    return resolve


def seed_state(state: RunState, goals: tuple[Goal, ...], compilation: Compilation,
               resources: CompilerResources, resolve: Callable[[str, Any], int | None]) -> None:
    """Requested goals, then their prerequisites and condition goals."""
    for g in goals:
        s = state.add(g, True)
        if g.issuer in compilation.held_out:
            s.update(Outcome("GAP", "HELD_OUT_ISSUER",
                             detail="held-out companies are never read (9.2)"))
    for g in goals:
        if g.issuer in compilation.held_out:
            continue
        if g.conditional:
            state.add(condition_goal(g), False)
            continue
        for action in actions_for(g.kind, resources.planner):
            if action.prerequisite:
                state.add(prerequisite(g, action.prerequisite, resolve), False)


def results(state: RunState) -> tuple[GoalResult, ...]:
    ordered = state.requested() + [s for s in state.goals.values() if not s.requested]
    return tuple(GoalResult(s.goal, s.requested, s.status, s.reason, s.detail, s.observations,
                            s.decisions, tuple(s.steps), s.gaps) for s in ordered)


def clarification(question: str, as_of: date, compilation: Compilation, run_id: str,
                  max_steps: int) -> Investigation:
    c = compilation.clarification
    assert c is not None
    return Investigation(run_id, question, as_of, compilation, CLARIFICATION, "clarification",
                         (), (), f"Clarification needed ({c.reason}): {c.message}", (),
                         max_steps, 0)


def investigate(question: str, as_of: date, *, tools: Any = None, parser: Parser | None = None,
                resources: CompilerResources | None = None, selected_issuer: str | None = None,
                max_steps: int | None = None, trace_dir: Path | str | None = None,
                run_id: str | None = None,
                compilation: Compilation | None = None,
                quote_checks: Callable[[str], Any] | None = None) -> Investigation:
    """Answer one question as of `as_of` (module docstring). compilation: goals compiled
    elsewhere (E8 replays measure the evidence path, not the compiler); the question is then only
    recorded, and the goals name their own scenarios (no Base-default note). quote_checks: the
    gate's quote check per goal kind (default: chunks rebuilt from the tools' pinned store,
    #157; tests pass their own)."""
    resources = resources or CompilerResources.load()
    budget = resources.planner.step_budget
    max_steps = budget.default if max_steps is None else max_steps
    check_inputs(as_of, max_steps, budget.min, budget.max)
    run_id = run_id or uuid.uuid4().hex[:12]
    precompiled = compilation is not None
    if compilation is None:
        parser = parser or load_trained_parser(resources)
        compilation = compile_question(question, parser, resources, selected_issuer)
    if compilation.clarification is not None:
        result = clarification(question, as_of, compilation, run_id, max_steps)
    else:
        if tools is None:
            from finagent.tools import FinAgentTools

            tools = FinAgentTools.default()
        goals = expand_defaults(compilation.goals, resources.planner)
        state = RunState(question, as_of)
        resolve = make_resolver(tools, as_of)
        seed_state(state, goals, compilation, resources, resolve)
        stop = Loop(state, tools, resources.planner, resources.vocabulary, resolve,
                    max_steps, quote_checks=quote_checks).run()
        found = results(state)
        requested = [g for g in found if g.requested]
        answers = [(g.goal, o) for g in requested if g.status == "VERIFIED"
                   for o in g.observations]
        comparisons = tuple(compare(answers)) if compilation.comparison else ()
        defaulted = not precompiled and scenario_defaulted(question, goals, resources)
        result = Investigation(run_id, question, as_of, compilation,
                               run_status(state.requested()), stop, found, tuple(state.trace),
                               render(requested, comparisons, defaulted, support=found),
                               comparisons,
                               max_steps, len(state.reused), defaulted)
    if trace_dir is not None:
        write_trace(result, trace_dir)
    return result
