"""E8 evidence replays (blueprint 10.6; #119, #137; Phase 6.6).

For each validation company and each goal kind with a tool today (fact on a fixed metric list,
statements, forecast, credit, liquidity), at the origin year (the latest 10-K filed by as_of),
the planner runs once per evidence view (finagent.statements.replay): complete; Company Facts
hidden; both tag sources hidden (a fully untagged filing); document reading hidden. Every run goes
through finagent.planner.investigate (pre-compiled goals: the compiler is E5's subject) with the
tools reading the statement build under that view, so the gate checks every answer as usual.

Scoring (configs/e8.yaml, fixed before e8-0001): each requested goal of a degraded view is
compared with the same goal of the complete run.

- correct: SAME_VALUE (every returned value equals the complete run's, with the right
  single-channel flag), SAME_OUTCOME (the complete run's own GAP / INVALID / CONFLICT, same reason)
  or EVIDENCE_GAP (no value; the reason, followed through a failed prerequisite, names the missing
  evidence);
- not correct, no value returned: OTHER_REASON (a refusal whose reason does not name the missing
  evidence), ERROR (TOOL_ERROR / GATE_ERROR), GATE_REJECTED (the gate refused the tool's value);
- not correct, a value returned: WRONG_FLAG, VALUE_DIFFERS (a fabricated value), STALE (wrong
  fiscal year, wrong accession, filed after as_of, wrong period end), LEAK (the answer cites a
  channel the view hides);
- NEW_VALUE: the complete run returned no value but the degraded run did; listed separately and
  traced to the complete run's channel readings (an untraceable one counts as fabricated).

The fallback-correctness rate is the correct rate over degraded fact goals whose trace ran a
fallback action (reported_tag_only, reported_document_only). Confidence intervals are percentile
bootstrap intervals resampling companies (finagent.evaluation.e2.bootstrap_rate). The complete
run is also compared with the pinned runs (statements-0008, forecast-0006) when they are given.

Validation companies only; a held-out ticker is refused before any evidence is read.
"""

import csv
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from finagent.config import (CONFIG_DIR, GOAL_KINDS, HELD_OUT, ConfigError, IntentVocabulary,
                             PlannerConfig, _load_yaml)
from finagent.evaluation.e2 import bootstrap_rate
from finagent.planner.investigate import GoalResult, Investigation, investigate
from finagent.planner.loop import plain
from finagent.planner.registry import actions_for
from finagent.records import AnswerObservation, Compilation, Goal, Observation
from finagent.statements.replay import E8_CONDITIONS, SINGLE_CHANNEL_INPUTS, EvidenceView
from finagent.tools import SINGLE_CHANNEL

E8_PATH = CONFIG_DIR / "e8.yaml"
VALIDATION = "validation"
KINDS = ("fact", "statements", "forecast", "credit", "liquidity")  # kinds with a tool today
PROJECTED = ("forecast", "liquidity")
FALLBACK_ACTIONS = ("reported_tag_only", "reported_document_only")
CORRECT = ("SAME_VALUE", "SAME_OUTCOME", "EVIDENCE_GAP")
UNSAFE = ("WRONG_FLAG", "VALUE_DIFFERS", "STALE", "LEAK")
CATEGORIES = CORRECT + ("OTHER_REASON", "ERROR", "GATE_REJECTED") + UNSAFE + ("NEW_VALUE",)
ERRORS = ("TOOL_ERROR", "GATE_ERROR")
PREREQUISITE = "PREREQUISITE_"
NO_VALUE_STATUSES = ("GAP", "INVALID", "CONFLICT", "SKIPPED")


# ---------------------------------------------------------------- spec

@dataclass(frozen=True)
class GoalSpec:
    kind: str
    metrics: tuple[str, ...]
    scenario: str = ""
    max_steps: int = 12


@dataclass(frozen=True)
class E8Spec:
    conditions: tuple[EvidenceView, ...]
    goals: dict[str, GoalSpec]
    evidence_reasons: dict[str, frozenset[str]]
    float_tolerance: float
    bootstrap_draws: int
    seed: int


def load_e8_spec(path: Path | str = E8_PATH, vocabulary: IntentVocabulary | None = None,
                 planner: PlannerConfig | None = None) -> E8Spec:
    """configs/e8.yaml, checked against the intent vocabulary, the scenarios and planner.yaml."""
    from finagent.config import load_intent_vocabulary, load_planner_config, load_scenarios

    data = _load_yaml(path)
    vocabulary = vocabulary or load_intent_vocabulary()
    planner = planner or load_planner_config()
    views = {v.name: v for v in E8_CONDITIONS}
    try:
        conditions = tuple(views[str(n)] for n in data["conditions"])
        goals = {str(k): _goal_spec(str(k), raw, vocabulary) for k, raw in data["goals"].items()}
        reasons = {str(k): frozenset(str(r) for r in v)
                   for k, v in data["evidence_reasons"].items()}
        spec = E8Spec(conditions, goals, reasons, float(data["float_tolerance"]),
                      int(data["bootstrap_draws"]), int(data["seed"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"e8 config: {exc}") from exc
    if not conditions or not conditions[0].complete or len(set(conditions)) != len(conditions):
        raise ConfigError("e8 conditions: distinct views, the complete view first")
    if set(goals) - set(KINDS) or set(reasons) - set(goals):
        raise ConfigError(f"e8 goals: kinds with a tool today {KINDS}; reasons for those kinds")
    scenarios = {s.name for s in load_scenarios()}
    budget = planner.step_budget
    for g in goals.values():
        if (g.kind in PROJECTED) != bool(g.scenario) or (g.scenario and g.scenario not in
                                                           scenarios):
            raise ConfigError(f"e8 goals.{g.kind}: a scenario exactly for {PROJECTED}")
        if not budget.min <= g.max_steps <= budget.max:
            raise ConfigError(f"e8 goals.{g.kind}: max_steps outside {budget.min}-{budget.max}")
    if spec.bootstrap_draws < 1 or spec.float_tolerance < 0:
        raise ConfigError("e8: bootstrap_draws >= 1 and float_tolerance >= 0")
    return spec


def _goal_spec(kind: str, raw: Mapping[str, Any], vocabulary: IntentVocabulary) -> GoalSpec:
    known = tuple(vocabulary.metrics.get(kind, {}))
    listed = raw.get("metrics")
    metrics = known if listed == "all" else tuple(str(m) for m in listed or ())
    if set(metrics) - set(known):
        raise ConfigError(f"e8 goals.{kind}: unknown metrics {sorted(set(metrics) - set(known))}")
    if kind != "statements" and not metrics:
        raise ConfigError(f"e8 goals.{kind}: needs metrics")
    return GoalSpec(kind, metrics, str(raw.get("scenario") or ""), int(raw.get("max_steps", 12)))


# ---------------------------------------------------------------- goals and runs

def goal_set(issuer: str, origin: int, spec: GoalSpec) -> tuple[Goal, ...]:
    """The requested goals of one kind at the origin year (projections: the next year)."""
    if spec.kind == "statements":
        return (Goal(issuer, origin, "statements"),)
    if spec.kind in PROJECTED:
        return tuple(Goal(issuer, "latest", spec.kind, m, spec.scenario) for m in spec.metrics)
    return tuple(Goal(issuer, origin, spec.kind, m) for m in spec.metrics)  # type: ignore[arg-type]


@dataclass(frozen=True)
class CaseResult:
    """One requested goal under one view."""

    issuer: str
    condition: str
    kind: str
    metric: str
    status: str
    reason: str
    detail: str
    root_kind: str  # the goal whose reason explains the outcome (through a failed prerequisite)
    root_reason: str
    answers: tuple[AnswerObservation, ...]
    actions: tuple[str, ...]  # the tools that served the goal, in order
    rejected: tuple[str, ...]  # gate codes of refused values
    gaps: tuple[tuple[str, str], ...] = ()

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.issuer, self.kind, self.metric)

    @property
    def has_value(self) -> bool:
        return self.status == "VERIFIED" and bool(self.answers)


def root_reason(g: GoalResult, inv: Investigation, planner: PlannerConfig) -> tuple[str, str]:
    """(kind, reason) of the goal that explains g's outcome: a failed prerequisite is followed."""
    kind, reason, seen = g.goal.kind, g.reason, 0
    while reason.startswith(PREREQUISITE) and seen < len(inv.goals):
        prereq = next((a.prerequisite for a in actions_for(kind, planner) if a.prerequisite), "")
        dep = next((d for d in inv.goals if not d.requested and d.goal.kind == prereq
                    and d.goal.issuer == g.goal.issuer), None)
        if dep is None:
            break
        kind, reason, seen = dep.goal.kind, dep.reason, seen + 1
    return kind, reason


def cases_from(inv: Investigation, view: EvidenceView, planner: PlannerConfig
               ) -> list[CaseResult]:
    tools = {s.step: s.tool for s in inv.trace}
    found = []
    for g in inv.requested:
        kind, reason = root_reason(g, inv, planner)
        found.append(CaseResult(
            g.goal.issuer, view.name, g.goal.kind, g.goal.metric, g.status, g.reason, g.detail,
            kind, reason, g.observations, tuple(tools[n] for n in g.steps if n in tools),
            tuple(d.code for d in g.decisions if not d.accepted), g.gaps))
    return found


@dataclass(frozen=True)
class Reference:
    """What the complete run's statement set says about one company at its origin year."""

    origin: int
    accession: str
    filed: date
    period_end: date
    filings: dict[str, tuple[int, date]]  # accession -> (fiscal year, filed), filed by as_of
    readings: dict[str, Observation] = field(default_factory=dict)  # metric -> observation


def company_reference(tools: Any, issuer: str, origin: int, as_of: date,
                      metrics: Iterable[str]) -> Reference:
    filings = tools.filings(issuer, as_of)
    f = next(f for f in filings if f.fiscal_year == origin)
    s = tools.statement_set(issuer, as_of)
    readings = {m: o for m in metrics if (o := s.get(origin, m)) is not None}
    return Reference(origin, f.accession, f.filed, f.end,
                     {x.accession: (x.fiscal_year, x.filed) for x in filings}, readings)


def run_company(tools: Any, issuer: str, as_of: date, spec: E8Spec, resources: Any,
                run_id: str, trace_dir: Path | None = None
                ) -> tuple[list[CaseResult], Reference | None, list[dict[str, Any]]]:
    """Every view and goal kind for one company; the tools' caches for it are dropped after."""
    origin = tools.resolve_year(issuer, "latest", as_of)
    if origin is None:
        return [], None, []
    cases: list[CaseResult] = []
    runs: list[dict[str, Any]] = []
    reference = None
    for view in spec.conditions:
        viewed = replace(tools, view=view)
        for kind, gs in spec.goals.items():
            goals = goal_set(issuer, origin, gs)
            question = f"E8 {issuer} FY{origin} {kind} ({view.name})"
            inv = investigate(question, as_of, tools=viewed, resources=resources,
                              max_steps=gs.max_steps, trace_dir=trace_dir,
                              run_id=f"{run_id}:{issuer}:{view.name}:{kind}",
                              compilation=Compilation(question, "e8-fixed-goals", goals,
                                                      issuers=(issuer,)))
            cases.extend(cases_from(inv, view, resources.planner))
            runs.append({"issuer": issuer, "condition": view.name, "kind": kind,
                         "status": inv.status, "stop_reason": inv.stop_reason,
                         "steps": inv.steps_used, "max_steps": inv.max_steps,
                         "reused": inv.reused})
        if view.complete:
            reference = company_reference(viewed, issuer, origin, as_of,
                                          spec.goals["fact"].metrics if "fact" in spec.goals
                                          else ())
    for cache in (tools.reconciliations, tools.forecasts):
        for key in [k for k in cache if k[0] == issuer]:
            del cache[key]
    return cases, reference, runs


def check_tickers(tickers: Sequence[str], universe: Mapping[str, str]) -> None:
    bad = sorted(t for t in tickers if universe.get(t) != VALIDATION)
    if bad:
        raise ValueError(f"E8 runs validation companies only; refused {bad}"
                         + (" (held out)" if any(universe.get(t) == HELD_OUT for t in bad)
                            else ""))


# ---------------------------------------------------------------- scoring

def expected_year(kind: str, origin: int) -> int:
    return origin + 1 if kind in PROJECTED else origin


def stale_problems(c: CaseResult, ref: Reference, as_of: date) -> list[str]:
    """Wrong fiscal year, wrong accession, filed after as_of or a wrong period end."""
    problems = []
    year = expected_year(c.kind, ref.origin)
    for a in c.answers:
        tag = f"{a.metric}:"
        if a.fiscal_year != year:
            problems.append(f"{tag} fiscal year {a.fiscal_year}, expected {year}")
        if a.accession != ref.accession:
            problems.append(f"{tag} accession {a.accession}, expected {ref.accession}")
        if a.filed is None or a.filed > as_of or a.filed != ref.filed:
            problems.append(f"{tag} filed {a.filed} (origin filed {ref.filed}, as_of {as_of})")
        end = None if c.kind in PROJECTED else ref.period_end
        if a.fiscal_period_end != end:
            problems.append(f"{tag} period end {a.fiscal_period_end}, expected {end}")
        for source in a.sources:
            known = ref.filings.get(source)
            if known is None or known[0] > ref.origin or known[1] > as_of:
                problems.append(f"{tag} cites {source}, not a filing by as_of up to the origin")
    return problems


def leak_problems(c: CaseResult, view: EvidenceView) -> list[str]:
    """A fact answer citing a channel the view hides (other answers cite no channels)."""
    problems: list[str] = []
    if c.kind != "fact":
        return problems
    for a in c.answers:
        if not view.document and "document" in a.channels:
            problems.append(f"{a.metric}: document channel cited with the document hidden")
        if view.untagged and "tag" in a.channels:
            problems.append(f"{a.metric}: tag channel cited with both tag sources hidden")
    return problems


def flag_problems(c: CaseResult, view: EvidenceView) -> list[str]:
    """Facts: SINGLE_CHANNEL exactly when one channel read the value. Model outputs, ratios and
    residuals: SINGLE_CHANNEL_INPUTS exactly when the view hides a whole channel (#119, #144)."""
    problems = []
    for a in c.answers:
        if c.kind == "fact":
            single = len(a.channels) == 1
            if single != (SINGLE_CHANNEL in a.flags):
                problems.append(f"{a.metric}: channels {list(a.channels)}, flags {list(a.flags)}")
        elif view.single_channel != (SINGLE_CHANNEL_INPUTS in a.flags):
            problems.append(f"{a.metric}: {SINGLE_CHANNEL_INPUTS} "
                            f"{'missing' if view.single_channel else 'without a hidden channel'}")
    return problems


def same_value(a: Any, b: Any, rounding: Decimal | None, tolerance: float) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, Decimal) and isinstance(b, Decimal):
        return a == b or (rounding is not None and abs(a - b) <= rounding)
    return abs(float(a) - float(b)) <= tolerance * max(1.0, abs(float(b)))


def display_rounding(answer: AnswerObservation, ref: Reference) -> Decimal | None:
    """Half a display unit, for a document-only fact (it shows the rounded figure)."""
    o = ref.readings.get(answer.metric)
    if tuple(answer.channels) != ("document",) or o is None or not o.scale:
        return None
    return Decimal(10) ** o.scale / 2


def value_differences(c: CaseResult, complete: CaseResult, ref: Reference,
                      tolerance: float) -> list[str]:
    """Returned values that differ from the complete run's (a value it did not return counts)."""
    base = {a.metric: a for a in complete.answers}
    found = []
    for a in c.answers:
        b = base.get(a.metric)
        if b is None:
            found.append(f"{a.metric}: {a.value} returned; complete returned no {a.metric}")
        elif not same_value(a.value, b.value, display_rounding(a, ref), tolerance):
            found.append(f"{a.metric}: {a.value} vs complete {b.value}")
    return found


def not_returned(c: CaseResult, complete: CaseResult) -> list[str]:
    """Optional values (statements: I11 when computable) the complete run returned and this run
    did not; the goal's required values are enforced by the gate."""
    missing = sorted({a.metric for a in complete.answers} - {a.metric for a in c.answers})
    return [f"not returned (optional): {missing}"] if missing else []


def traced(c: CaseResult, ref: Reference) -> list[str]:
    """A new value is traced when it equals a channel reading of the complete run's item."""
    problems = []
    for a in c.answers:
        o = ref.readings.get(a.metric) if c.kind == "fact" else None
        if o is None:
            problems.append(f"{a.metric}: {a.value} has no complete-run reading to trace it to")
            continue
        readings = {v for v in (o.tag_value, o.document_value, *o.candidates) if v is not None}
        if a.value not in readings:
            problems.append(f"{a.metric}: {a.value} is none of the complete run's readings "
                            f"{sorted(readings)}")
    return problems


@dataclass(frozen=True)
class Scored:
    case: CaseResult
    category: str
    problems: tuple[str, ...] = ()
    fabricated: bool = False
    stale: bool = False

    @property
    def correct(self) -> bool:
        return self.category in CORRECT


def classify(c: CaseResult, complete: CaseResult | None, view: EvidenceView, ref: Reference,
             spec: E8Spec, as_of: date) -> Scored:
    """One degraded-view goal against the complete run (module docstring)."""
    stale = stale_problems(c, ref, as_of) if c.has_value else []
    leaks = leak_problems(c, view) if c.has_value else []
    if stale:
        return Scored(c, "STALE", tuple(stale), stale=True)
    if leaks:
        return Scored(c, "LEAK", tuple(leaks))
    if c.has_value:
        if complete is None or not complete.has_value:
            untraced = traced(c, ref)
            return Scored(c, "NEW_VALUE", tuple(untraced + flag_problems(c, view)),
                          fabricated=bool(untraced))
        diffs = value_differences(c, complete, ref, spec.float_tolerance)
        if diffs:
            return Scored(c, "VALUE_DIFFERS", tuple(diffs), fabricated=True)
        flags = flag_problems(c, view)
        if flags:
            return Scored(c, "WRONG_FLAG", tuple(flags))
        return Scored(c, "SAME_VALUE", tuple(not_returned(c, complete)))
    if complete is not None and not complete.has_value and \
            (complete.status, complete.reason) == (c.status, c.reason):
        return Scored(c, "SAME_OUTCOME")
    if c.rejected:
        return Scored(c, "GATE_REJECTED", c.rejected)
    if c.reason in ERRORS or c.root_reason in ERRORS:
        return Scored(c, "ERROR", (c.detail,))
    if c.root_reason in spec.evidence_reasons.get(c.root_kind, frozenset()):
        return Scored(c, "EVIDENCE_GAP")
    return Scored(c, "OTHER_REASON", (f"{c.root_kind} {c.root_reason}",))


def reference_scored(c: CaseResult, view: EvidenceView, ref: Reference, as_of: date) -> Scored:
    """The complete run's own answers: provenance and flag consistency."""
    stale = stale_problems(c, ref, as_of) if c.has_value else []
    flags = flag_problems(c, view) if c.has_value else []
    return Scored(c, "REFERENCE", tuple(stale + flags), stale=bool(stale))


def score(cases: Sequence[CaseResult], references: Mapping[str, Reference], spec: E8Spec,
          as_of: date) -> list[Scored]:
    """Every degraded-view case scored against the complete run of the same goal."""
    views = {v.name: v for v in spec.conditions}
    complete_name = spec.conditions[0].name
    base = {c.key: c for c in cases if c.condition == complete_name}
    found = []
    for c in cases:
        ref = references[c.issuer]
        if c.condition == complete_name:
            found.append(reference_scored(c, spec.conditions[0], ref, as_of))
        else:
            found.append(classify(c, base.get(c.key), views[c.condition], ref, spec, as_of))
    return found


# ---------------------------------------------------------------- pinned runs

def pinned_problems(cases: Sequence[CaseResult], references: Mapping[str, Reference],
                    statements_json: Path | None, projections_csv: Path | None,
                    complete: str, tolerance: float) -> dict[str, Any]:
    """The complete run against statements-0008 (facts at the origin year) and forecast-0006
    (Base projections at origin + 1). Credit ratios and residuals are not in those files."""
    facts = _pinned_facts(statements_json, references) if statements_json else None
    lines = _pinned_lines(projections_csv, references) if projections_csv else None
    checked, problems = 0, []
    for c in cases:
        if c.condition != complete:
            continue
        if c.kind == "fact" and facts is not None:
            pinned = facts.get((c.issuer, c.metric))
            got = c.answers[0].value if c.has_value else None
            want = Decimal(pinned) if pinned not in (None, "") else None
            checked += 1
            if got != want:
                problems.append(f"{c.issuer} {c.metric}: {got} vs statements {want}")
        elif c.kind in PROJECTED and lines is not None:
            checked += 1
            got = c.answers[0].value if c.has_value else None
            want = _projected(lines, c.issuer, c.metric)
            if not same_value(got, want, None, tolerance):
                problems.append(f"{c.issuer} {c.kind} {c.metric}: {got} vs forecast {want}")
    return {"checked": checked, "differences": problems,
            "statements_json": str(statements_json) if statements_json else None,
            "projections_csv": str(projections_csv) if projections_csv else None}


def _pinned_facts(path: Path, references: Mapping[str, Reference]
                  ) -> dict[tuple[str, str], str | None]:
    """Accepted values only: a value that is not accepted is not an answer."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    found = {}
    for company in data["companies"]:
        ref = references.get(company["issuer"])
        if ref is None:  # only the companies of this run are read
            continue
        for o in company["observations"]:
            if o["fiscal_year"] == ref.origin:
                found[(company["issuer"], o["metric"])] = o["value"] if o["accepted"] else None
    return found


def _pinned_lines(path: Path, references: Mapping[str, Reference]
                  ) -> dict[tuple[str, str], float]:
    found = {}
    with Path(path).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            ref = references.get(row["ticker"])
            if ref is None or row["scenario"] != "Base" or int(row["fiscal_year"]) != \
                    ref.origin + 1:
                continue
            found[(row["ticker"], row["line"])] = float(row["value"])
    return found


def _projected(lines: Mapping[tuple[str, str], float], issuer: str, metric: str) -> float | None:
    from finagent.tools import DERIVED_FORECAST

    if metric in DERIVED_FORECAST:
        parts = [(lines.get((issuer, line)), sign) for line, sign in DERIVED_FORECAST[metric]]
        return None if any(v is None for v, _ in parts) else sum(v * s for v, s in parts)
    return lines.get((issuer, metric))


# ---------------------------------------------------------------- metrics

def rate(scored: Sequence[Scored], hit, draws: int, seed: int) -> dict[str, Any]:
    pairs = [(s.case.issuer, bool(hit(s))) for s in scored]
    k, n = sum(h for _, h in pairs), len(pairs)
    return {"k": k, "n": n, "rate": k / n if n else None,
            "ci95": bootstrap_rate(pairs, draws, seed) if n else None}


def used_fallback(c: CaseResult) -> bool:
    return c.kind == "fact" and any(a in FALLBACK_ACTIONS for a in c.actions)


def condition_metrics(scored: Sequence[Scored], spec: E8Spec) -> dict[str, Any]:
    d, s = spec.bootstrap_draws, spec.seed
    new = [x for x in scored if x.category == "NEW_VALUE"]
    judged = [x for x in scored if x.category != "NEW_VALUE"]
    fallback = [x for x in scored if used_fallback(x.case)]
    return {
        "cases": len(scored),
        "correct": rate(scored, lambda x: x.correct, d, s),
        "correct_excluding_new_values": rate(judged, lambda x: x.correct, d, s),
        "safe": rate(scored, lambda x: x.category not in UNSAFE and not x.fabricated, d, s),
        "fallback_correct": rate(fallback, lambda x: x.correct, d, s),
        "categories": dict(Counter(x.category for x in scored)),
        "fabricated": sum(x.fabricated for x in scored),
        "stale": sum(x.stale for x in scored),
        "leaks": sum(x.category == "LEAK" for x in scored),
        "wrong_flags": sum(x.category == "WRONG_FLAG" for x in scored),
        "gate_rejections": sum(len(x.case.rejected) for x in scored),
        "new_values": len(new),
        "by_kind": {k: rate([x for x in scored if x.case.kind == k], lambda x: x.correct, d, s)
                    for k in spec.goals},
    }


def reference_metrics(scored: Sequence[Scored]) -> dict[str, Any]:
    return {"cases": len(scored),
            "with_value": sum(x.case.has_value for x in scored),
            "status": dict(Counter(x.case.status for x in scored)),
            "problems": sum(bool(x.problems) for x in scored),
            "stale": sum(x.stale for x in scored),
            "gate_rejections": sum(len(x.case.rejected) for x in scored),
            "by_kind_with_value": {k: sum(x.case.has_value for x in scored if x.case.kind == k)
                                   for k in dict.fromkeys(x.case.kind for x in scored)}}


def metrics(scored: Sequence[Scored], spec: E8Spec) -> dict[str, Any]:
    by_condition: dict[str, list[Scored]] = defaultdict(list)
    for x in scored:
        by_condition[x.case.condition].append(x)
    complete = spec.conditions[0].name
    degraded = [x for x in scored if x.case.condition != complete]
    found = {"complete": reference_metrics(by_condition[complete]),
             "degraded": condition_metrics(degraded, spec)}
    for view in spec.conditions[1:]:
        found[view.name] = condition_metrics(by_condition[view.name], spec)
    return found


# ---------------------------------------------------------------- records and report

def case_record(x: Scored) -> dict[str, Any]:
    c = x.case
    return plain({"issuer": c.issuer, "condition": c.condition, "kind": c.kind,
                  "metric": c.metric, "status": c.status, "reason": c.reason,
                  "detail": c.detail, "root": f"{c.root_kind} {c.root_reason}",
                  "actions": list(c.actions), "rejected": list(c.rejected),
                  "gaps": [list(g) for g in c.gaps], "category": x.category,
                  "correct": x.correct, "fabricated": x.fabricated, "stale": x.stale,
                  "problems": list(x.problems),
                  "answers": [asdict(a) for a in c.answers]})


def write_run(out: Path, header: Mapping[str, Any], scored: Sequence[Scored],
              summary: Mapping[str, Any], runs: Sequence[Mapping[str, Any]]) -> None:
    """runs/<run-id>/ files (the caller refuses an existing run; trace/ is written during it)."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "header.json").write_text(json.dumps(plain(dict(header)), indent=2))
    (out / "metrics.json").write_text(json.dumps(plain(dict(summary)), indent=2))
    with (out / "cases.jsonl").open("w", encoding="utf-8") as handle:
        for x in scored:
            handle.write(json.dumps(case_record(x)) + "\n")
    with (out / "runs.jsonl").open("w", encoding="utf-8") as handle:
        for r in runs:
            handle.write(json.dumps(plain(dict(r))) + "\n")


def _pct(block: Mapping[str, Any] | None) -> str:
    if not block or block.get("rate") is None:
        return "n/a (0 cases)"
    ci = block.get("ci95")
    interval = f" [{ci[0]:.1%}, {ci[1]:.1%}]" if ci else ""
    return f"{block['k']}/{block['n']} = {block['rate']:.1%}{interval}"


def _table(head: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return lines


def _case_line(x: Scored) -> list[Any]:
    c = x.case
    values = "; ".join(f"{a.metric}={a.value} {list(a.flags)}" for a in c.answers) or "-"
    why = "; ".join(x.problems) or c.detail
    return [c.issuer, c.condition, c.kind, c.metric or "-", c.status, c.reason,
            f"{c.root_kind} {c.root_reason}", values, why.replace("|", "/")[:300]]


def render_report(header: Mapping[str, Any], summary: Mapping[str, Any],
                  scored: Sequence[Scored], pinned: Mapping[str, Any],
                  runs: Sequence[Mapping[str, Any]]) -> str:
    views = header["conditions"]
    degraded = views[1:]
    lines = [f"# E8 evidence replays ({header['run_id']})", "",
             f"Generated by eval/e8_replays.py. Git {header['git_commit']}"
             f"{' (dirty)' if header['git_dirty'] else ''}; evidence manifest "
             f"{header['evidence_manifest_sha256'][:12]}; as_of {header['as_of']}; M1 "
             f"{header['model_version']}; companies {', '.join(header['companies'])} "
             f"(validation only); origin years {header['origin_years']}.", "",
             "Definitions: finagent/evaluation/e8.py (module docstring) and configs/e8.yaml, "
             "fixed before the run. Intervals: 95% percentile bootstrap over companies "
             f"({header['bootstrap_draws']} draws).", "",
             "## 1. Correct rate per condition", ""]
    rows = []
    for v in degraded + ["degraded"]:
        m = summary[v]
        rows.append([v, m["cases"], _pct(m["correct"]), _pct(m["correct_excluding_new_values"]),
                     _pct(m["safe"]), _pct(m["fallback_correct"])])
    lines += _table(["condition", "goals", "correct", "correct (new values excluded)",
                     "safe (no wrong, stale or leaked value)", "fallback correct"], rows)
    lines += ["", "## 2. Correct rate per goal kind", ""]
    kinds = list(summary["degraded"]["by_kind"])
    lines += _table(["kind"] + degraded, [[k] + [_pct(summary[v]["by_kind"][k]) for v in degraded]
                                           for k in kinds])
    lines += ["", "## 3. Values that must not appear (target 0)", ""]
    keys = ("fabricated", "stale", "leaks", "wrong_flags", "gate_rejections", "new_values")
    lines += _table(["condition"] + list(keys),
                    [[v] + [summary[v][k] for k in keys] for v in degraded + ["degraded"]])
    ref = summary["complete"]
    lines += ["", f"Complete run (reference): {ref['cases']} goals, {ref['with_value']} with a "
              f"value, status {ref['status']}; provenance or flag problems {ref['problems']}, "
              f"stale {ref['stale']}, gate rejections {ref['gate_rejections']}. Against the "
              f"pinned runs: {pinned['checked']} goals checked, {len(pinned['differences'])} "
              "differences" + (": " + "; ".join(pinned["differences"][:10])
                               if pinned["differences"] else "") + ".", ""]
    lines += ["## 4. Outcome categories", ""]
    lines += _table(["condition"] + list(CATEGORIES),
                    [[v] + [summary[v]["categories"].get(c, 0) for c in CATEGORIES]
                     for v in degraded])
    new = [x for x in scored if x.category == "NEW_VALUE"]
    lines += ["", "## 5. Complete run had no value, degraded run returned one (listed "
              "separately)", ""]
    head = ["issuer", "condition", "kind", "metric", "status", "reason", "root", "values",
            "notes"]
    lines += _table(head, [_case_line(x) for x in new]) if new else ["None."]
    wrong = [x for x in scored if x.category not in CORRECT + ("REFERENCE", "NEW_VALUE")]
    lines += ["", "## 6. Degraded goals not counted correct", ""]
    lines += _table(head, [_case_line(x) for x in wrong]) if wrong else ["None."]
    stops = Counter((r["condition"], r["stop_reason"]) for r in runs)
    lines += ["", "## 7. Investigations", "",
              f"{len(runs)} investigations (company × condition × goal kind); stop reasons: "
              + "; ".join(f"{c} {s} {n}" for (c, s), n in sorted(stops.items()))
              + f"; steps max {max((r['steps'] for r in runs), default=0)}.", ""]
    return "\n".join(lines)
