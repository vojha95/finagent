"""The Correlation Agent loop for one filing (blueprint 8.8; Phase 5).

check -> diagnose -> plan -> act -> re-check, with guardrails and escalation:

1. run_identities: the agent's checks (configs/correlation_agent.yaml; I8 is not used). R is the
   sum of each FAIL's excess over its tolerance. A REVIEW failure (I11) never triggers.
2. Suspects: changeable values (finagent.verify.workspace) entering at least one FAIL check,
   ranked by how many FAIL checks they enter (more first), then by M1 probability (lower first).
3. Actions per suspect: swap to each candidate row, rescale by each configured factor, flip
   the sign. Score = probability x (R - R'): a swap's probability is M1's p(item | row); a
   rescale or flip has p(item | current row) x its prior. Swap guards (#109): a candidate row
   below the configured M1 probability, or a row that already supplies another item in the
   filing's own period (one item per row), is refused before it is evaluated.
4. Guardrails: an action is admissible only if R' < R strictly, it brings at least one failing
   check within tolerance (a partial reduction alone is rejected; #103, decided), no passing
   check stops passing (I11 included), and in product mode an action on a DISAGREE value makes
   it agree with the tag (the tag is the target; the value is always the document's own cell).
   The best admissible action is applied; then re-check.
5. Stop when no check fails, no action is admissible, or max_actions were applied.
6. Settle: a changed value is CORRECTED only if every check it enters passes at the end (and,
   for a DISAGREE value, it agrees with the tag); otherwise its original reading is kept and
   it is REVIEW, as are the values left in failing checks. If reverting breaks a value kept as
   CORRECTED, every change is reverted.

Never a plug, never a missing value set to zero: values come only from displayed cells.
"""

import time
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any

from finagent.records import AgentAction, AgentResult, AgentValue, CheckResult, TraceStep
from finagent.verify.agent_tools import (ToolError, candidates, entered, flip_sign, mark_review,
                                         rescale, run_identities, swap)
from finagent.verify.workspace import FilingState

NO_FAILING_CHECK = "NO_FAILING_CHECK"
NO_ADMISSIBLE_ACTION = "NO_ADMISSIBLE_ACTION"
ACTION_LIMIT = "ACTION_LIMIT"
ACTION_TOOLS = ("swap", "rescale", "flip_sign")


@dataclass(frozen=True, eq=False)
class Option:
    """One candidate action, evaluated."""

    tool: str
    item: str
    row_id: str | None
    factor: Decimal | None
    probability: float
    state: FilingState
    rank: tuple[int, int, int]  # suspect rank, tool order, candidate order (tie-breaks)
    residual_after: Decimal
    broken: tuple[str, ...]
    agrees: bool  # product mode, DISAGREE item: agrees with the tag after the action
    resolved: tuple[str, ...] = ()  # failing checks brought within tolerance by the action
    guard: str | None = None  # why a swap was refused before evaluation (#109)

    def admissible(self, residual: Decimal, needs_agreement: bool) -> bool:
        return self.guard is None and self.residual_after < residual and bool(self.resolved) \
            and not self.broken and (self.agrees or not needs_agreement)

    def score(self, residual: Decimal) -> float:
        return self.probability * float(residual - self.residual_after)


class Trace:
    def __init__(self) -> None:
        self.steps: list[TraceStep] = []

    def add(self, tool: str, arguments: dict[str, Any], eligible: tuple[str, ...],
            decision: str, status: str, observations: tuple[dict[str, Any], ...],
            started: float) -> None:
        self.steps.append(TraceStep(len(self.steps) + 1, tool, arguments, eligible, decision,
                                    status, (), observations,
                                    round((time.perf_counter() - started) * 1000, 3)))


def _checks_text(checks: tuple[CheckResult, ...], only_failing: bool = False
                 ) -> tuple[dict[str, Any], ...]:
    return tuple({"check": c.check_id, "status": c.status, "residual": str(c.residual),
                  "tolerance": str(c.tolerance)}
                 for c in checks if not only_failing or c.status == "FAIL")


def suspects(state: FilingState) -> list[str]:
    """Changeable values entering a FAIL check: most FAIL checks first, then least probable."""
    checks = state.view.checks
    found = []
    for item in state.changeable:
        if state.reading(item).value is None:
            continue
        failing = sum(1 for c in entered(checks, item) if c.status == "FAIL")
        if failing:
            found.append((-failing, state.reading(item).probability or 0.0, item))
    return [item for *_, item in sorted(found)]


def _broken(before: tuple[CheckResult, ...], after: tuple[CheckResult, ...]) -> tuple[str, ...]:
    now = {c.check_id: c.status for c in after}
    return tuple(c.check_id for c in before if c.status == "PASS" and now.get(c.check_id) != "PASS")


def _resolved(before: tuple[CheckResult, ...], after: tuple[CheckResult, ...]
              ) -> tuple[str, ...]:
    now = {c.check_id: c.status for c in after}
    return tuple(c.check_id for c in before if c.status == "FAIL" and now.get(c.check_id) == "PASS")


def _evaluate(state: FilingState, new: FilingState, tool: str, item: str, row_id: str | None,
              factor: Decimal | None, p: float, rank: tuple[int, int, int]) -> Option:
    agrees = True
    if item in new.disagree:
        check = new.view.check(f"D:{item}")
        agrees = check is not None and check.status == "PASS"
    return Option(tool, item, row_id, factor, p, new, rank, new.view.residual,
                  _broken(state.view.checks, new.view.checks), agrees,
                  _resolved(state.view.checks, new.view.checks))


def swap_guard(state: FilingState, item: str, row_id: str, probability: float) -> str | None:
    """Why a swap of the item onto the row is refused (#109), or None."""
    floor = state.agent.swap_min_probability
    if probability < floor:
        return f"M1 p={probability:.4f} below {floor}"
    taken = sorted(i for i, r in state.documents.items()
                   if i != item and r.value is not None and r.row_id == row_id)
    return "row supplies " + ", ".join(taken) if taken else None


def options(state: FilingState, items: list[str], as_of: date) -> list[Option]:
    """Every action on every suspect, evaluated on a copy of the state; a guarded swap is
    listed with its reason and not evaluated."""
    config = state.agent
    found: list[Option] = []
    for s_rank, item in enumerate(items):
        current = state.reading(item).probability or 0.0
        for c_rank, c in enumerate(candidates(state, item, as_of)):
            rank = (s_rank, 0, c_rank)
            guard = swap_guard(state, item, c.row_id, c.probability)
            if guard is not None:
                found.append(Option("swap", item, c.row_id, None, c.probability, state, rank,
                                    state.view.residual, (), False, (), guard))
                continue
            found.append(_evaluate(state, swap(state, item, c.row_id, as_of), "swap", item,
                                   c.row_id, None, c.probability, rank))
        for f_rank, factor in enumerate(config.rescale_factors):
            try:
                new = rescale(state, item, factor, as_of)
            except ToolError:  # beyond one unit correction
                continue
            found.append(_evaluate(state, new, "rescale", item, None, factor,
                                   current * config.rescale_prior, (s_rank, 1, f_rank)))
        found.append(_evaluate(state, flip_sign(state, item, as_of), "flip_sign", item, None,
                               None, current * config.flip_prior, (s_rank, 2, 0)))
    return found


def resolved(state: FilingState, item: str) -> bool:
    """Every check the item enters passes (at least one is computed), and a DISAGREE value
    agrees with the tag."""
    checks = entered(state.view.checks, item)
    if any(c.status == "FAIL" for c in checks) or not any(c.status == "PASS" for c in checks):
        return False
    if item in state.disagree:
        check = state.view.check(f"D:{item}")
        return check is not None and check.status == "PASS"
    return True


def settle(state: FilingState) -> tuple[FilingState, list[str], list[str]]:
    """(final state, items CORRECTED, items reverted)."""
    keep = sorted(i for i in state.edits if resolved(state, i))
    drop = sorted(i for i in state.edits if i not in keep)
    if not drop:
        return state, keep, drop
    reverted = replace(state, edits={i: state.edits[i] for i in keep})
    if all(resolved(reverted, i) for i in keep):
        return reverted, keep, drop
    return replace(state, edits={}), [], sorted(state.edits)


def run_agent(state: FilingState, as_of: date) -> AgentResult:
    """Run the loop on one filing; returns CORRECTED / REVIEW values, edits, actions, trace."""
    trace = Trace()
    config = state.agent
    started = time.perf_counter()
    before = run_identities(state, as_of)
    trace.add("run_identities", {"accession": state.filing.accession, "mode": state.mode},
              ("run_identities",), "start: evaluate the identity checks", "OK",
              _checks_text(before, only_failing=True), started)
    applied: list[AgentAction] = []
    stop = NO_FAILING_CHECK
    while True:
        residual = state.view.residual
        if not any(c.status == "FAIL" for c in state.view.checks):
            stop = NO_FAILING_CHECK
            break
        if len(applied) >= config.max_actions:
            stop = ACTION_LIMIT
            break
        started = time.perf_counter()
        items = suspects(state)
        found = options(state, items, as_of)
        admissible = [o for o in found if o.admissible(residual, o.item in state.disagree)]
        chosen = {id(o) for o in admissible}
        trace.add("candidates", {"suspects": items}, ("candidates",) + ACTION_TOOLS,
                  f"{len(items)} suspect(s) in failing checks; {len(found)} action(s) "
                  f"evaluated, {len(admissible)} admissible", "OK",
                  tuple({"tool": o.tool, "item": o.item, "row_id": o.row_id,
                         "factor": str(o.factor) if o.factor else None,
                         "probability": round(o.probability, 4),
                         "residual_after": str(o.residual_after), "broken": list(o.broken),
                         "resolves": list(o.resolved), "agrees": o.agrees,
                         "guard": o.guard, "admissible": id(o) in chosen}
                        for o in found), started)
        if not admissible:
            stop = NO_ADMISSIBLE_ACTION
            break
        best = max(admissible, key=lambda o: (o.score(residual), tuple(-x for x in o.rank)))
        started = time.perf_counter()
        before_value = state.reading(best.item).value
        state = best.state
        action = AgentAction(best.tool, best.item, best.row_id, best.factor, before_value,
                             state.reading(best.item).value, best.probability, residual,
                             best.residual_after, best.score(residual))
        applied.append(action)
        trace.add(best.tool, {"item": best.item, "row_id": best.row_id,
                              "factor": str(best.factor) if best.factor else None},
                  tuple(sorted({o.tool for o in admissible})),
                  f"best score p={best.probability:.4f} x (R {residual} -> "
                  f"{best.residual_after}) among {len(admissible)} admissible action(s)",
                  "APPLIED", ({"value_before": str(before_value),
                               "value_after": str(action.value_after)},), started)
        started = time.perf_counter()
        trace.add("run_identities", {"accession": state.filing.accession},
                  ("run_identities",), "re-check after the action", "OK",
                  _checks_text(state.view.checks, only_failing=True), started)

    started = time.perf_counter()
    final, kept, dropped = settle(state)
    values = [_corrected(final, item) for item in kept]
    review = sorted((set(dropped) | set(suspects(final))) - set(kept))
    if review:
        values += mark_review(final, review, as_of,
                              f"unresolved after {len(applied)} action(s): {stop}")
        trace.add("mark_review", {"items": review}, ("mark_review",),
                  "escalate: values left inconsistent (original readings kept)", "REVIEW",
                  _checks_text(final.view.checks, only_failing=True), started)
    edits = tuple(replace(final.edits[i], detail=_edit_detail(final, i)) for i in kept)
    return AgentResult(final.filing.issuer, final.filing.fiscal_year, final.filing.accession,
                       final.mode, as_of, tuple(values), edits, tuple(applied), before,
                       final.view.checks, tuple(trace.steps), stop)


def _corrected(state: FilingState, item: str) -> AgentValue:
    original, now = state.original[item], state.reading(item)
    return AgentValue(state.filing.issuer, state.filing.fiscal_year, item, "CORRECTED",
                      original.value, original.row_id, now.value, now.row_id,
                      state.edits[item].actions,
                      tuple((c.check_id, c.status, c.residual)
                            for c in entered(state.view.checks, item)),
                      _edit_detail(state, item))


def _edit_detail(state: FilingState, item: str) -> str:
    original, now = state.original[item], state.reading(item)
    return (f"{'+'.join(state.edits[item].actions)}: {original.value} (row {original.row_id}) "
            f"-> {now.value} (row {now.row_id})")
