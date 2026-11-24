"""Run state of one investigation (blueprint 8.17; Phase 6.4): typed goals with their status,
outcome and gate decisions, the tool calls made (for reuse) and the trace."""

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from finagent.planner.gate import GateDecision
from finagent.records import AnswerObservation, Goal, Outcome, TraceStep

START = "start"
MISSING_PRIMARY = "MISSING_PRIMARY"
RESOLVED = ("VERIFIED", "SKIPPED")


@dataclass
class GoalState:
    """One goal: `trigger` is what the next action must match ("start", or the reason code of
    a MISSING_PRIMARY outcome); terminal once it has a final status."""

    goal: Goal
    requested: bool  # asked for in the question (else a prerequisite or condition goal)
    status: str = START
    trigger: str = START
    terminal: bool = False
    reason: str = ""
    detail: str = ""
    observations: tuple[AnswerObservation, ...] = ()
    decisions: tuple[GateDecision, ...] = ()
    gaps: tuple[tuple[str, str], ...] = ()
    attempted: set[str] = field(default_factory=set)
    steps: list[int] = field(default_factory=list)

    def update(self, outcome: Outcome, decisions: tuple[GateDecision, ...] = ()) -> None:
        """Take a (gated) outcome; MISSING_PRIMARY keeps the goal open for its fallback."""
        self.status, self.reason, self.detail = outcome.status, outcome.reason, outcome.detail
        self.observations = tuple(outcome.observations)
        self.decisions = self.decisions + decisions
        self.terminal = outcome.status != MISSING_PRIMARY
        self.trigger = outcome.reason if outcome.status == MISSING_PRIMARY else START


@dataclass
class Call:
    """A tool call made in this run: its raw outcome and its trace step."""

    outcome: Outcome
    step: int


@dataclass
class RunState:
    question: str
    as_of: date
    goals: dict[Goal, GoalState] = field(default_factory=dict)
    calls: dict[tuple, Call] = field(default_factory=dict)
    trace: list[TraceStep] = field(default_factory=list)
    reused: list[dict[str, Any]] = field(default_factory=list)

    def add(self, goal: Goal, requested: bool) -> GoalState:
        """The goal's state, created on first use (a requested goal stays requested)."""
        if goal not in self.goals:
            self.goals[goal] = GoalState(goal, requested)
        elif requested:
            self.goals[goal].requested = True
        return self.goals[goal]

    def open(self) -> list[GoalState]:
        return [s for s in self.goals.values() if not s.terminal]

    def requested(self) -> list[GoalState]:
        return [s for s in self.goals.values() if s.requested]

    @property
    def steps_used(self) -> int:
        return len(self.trace)
