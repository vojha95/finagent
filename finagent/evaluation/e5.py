"""E5 planner evaluation (blueprint 10.4; Phase 6, E5 stage 2).

    python eval/e5_planner.py run --run-id e5-0001 --compiler m0
    python eval/e5_planner.py run --run-id e5-0002 --compiler m1
    python eval/e5_planner.py report --runs e5-0001 e5-0002

Every question of the frozen gold set (gold/intents_test.jsonl, checked against its sha256 first)
goes end to end through finagent.planner.investigate(question, as_of) with the run's compiler (M0:
the regex grammar; M1: the trained compiler at the configured confidence threshold, the grammar
below it) and the cell's selected company. The compiler is never tuned, retrained or changed here.

Definitions (fixed before e5-0001):

- Normalised goal: (kind, issuer, metric, period, scenario, target, conditional). The projected
  year of a forecast, liquidity or debt goal is resolved to a fiscal year on both sides before
  comparing (Vineet, stage 2): blank = origin + 1, "+n" = origin + n, a year stays a year; the
  origin is the issuer's latest fiscal year filed by as_of. So a blank target and an explicit
  first projected year score as equal. Every other slot compares strictly; metric "" (the kind's
  default set) matches only "".
- Goal accuracy: over the GOALS questions, the compiled goals (as a set) equal the gold goals.
  Coverage: the share of GOALS questions compiled into goals (not a clarification); precision:
  goal accuracy over the compiled ones (blueprint 10.4: M1 should raise coverage without
  lowering precision).
- Compiled correctly (every question): goal accuracy, or for a CLARIFICATION question a
  clarification of the gold category.
- Slot F1, per slot: per question, the multiset of the slot's values over the gold goals and over
  the compiled goals; TP = their intersection, FP = the rest of the compiled, FN = the rest of
  the gold; summed over all questions (a clarification compiles no goals; a CLARIFICATION
  question has no gold goals), then precision, recall and F1.
- Correct clarification, per gold category: the share of CLARIFICATION questions answered by a
  clarification of that category ("clarified": of any category). False clarification: the share
  of GOALS questions answered by a clarification.
- Answer correctness (end_to_end questions): the requested goals' outcomes (goal, resolved year,
  status, reason, and every value with its metric, fiscal year and scenario) equal those of the
  reference run, which sends the gold goals through the same tools and gate (investigate with a
  precompiled Compilation). No answer (a clarification) is not correct.
- Gate and fabrication check (every value the run returned): the gate accepted it; it is
  validated or a flagged single-channel fact; it cites a 10-K of the issuer filed by as_of (its
  sources too); and it equals the pinned batch value for its issuer, fiscal year, metric and
  scenario (statements, forecast, credit and insights runs). A value failing any of these counts
  as unverified.
- Splits: author (Vineet's blind questions, the builder's canonical ones) and request kind (the
  grid group; traps sit in their kind's group).
- LLM fallback (pass^k, k = 3): not run. There is no external AI API (CLAUDE.md rule 9) and no
  local LLM in Phase 6 (#121): a limitation, not a result.

Validation companies only: a held-out issuer is never read (the tools refuse it).
"""

import csv
import json
import math
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from finagent.planner.gate import NOT_MEANINGFUL, PROJECTED_KINDS, SINGLE_CHANNEL
from finagent.planner.loop import plain
from finagent.records import AnswerObservation, Compilation, Goal
from finagent.tools import DERIVED_FORECAST, resolve_target

GOALS, CLARIFICATION, VERIFIED = "GOALS", "CLARIFICATION", "VERIFIED"
VALIDATION = "validation"
SLOTS = ("kind", "issuer", "metric", "period", "scenario", "target", "conditional")
CATEGORIES = ("advice", "two_periods", "wrong_company", "unsupported")
AUTHORS = ("vineet", "canonical")
FLOAT_TOLERANCE = 1e-9  # relative, against the pinned csv (full float repr)
SIGNAL_TOLERANCE = 1e-5  # relative, the pinned signals.csv rounds to 6 significant digits
DEBT_COLUMNS = {"cash_available_for_debt_service": "cash_available"}
NormGoal = tuple[Any, ...]


# ---------------------------------------------------------------- goals and slots

def goal_from(raw: Mapping[str, Any]) -> Goal:
    return Goal(**raw)


def origin_years(tools: Any, issuers: Iterable[str], as_of: date,
                 universe: Mapping[str, str]) -> dict[str, int | None]:
    """Latest fiscal year filed by as_of, for validation issuers only (others: None)."""
    return {t: tools.resolve_year(t, "latest", as_of) if universe.get(t) == VALIDATION else None
            for t in sorted(set(issuers))}


def normalise_goal(goal: Goal, origins: Mapping[str, int | None]) -> NormGoal:
    """The goal's slots, with a projected year resolved to a fiscal year (module docstring)."""
    target = goal.target
    origin = origins.get(goal.issuer)
    if goal.kind in PROJECTED_KINDS and origin is not None:
        target = resolve_target(origin, target)
    return (goal.kind, goal.issuer, goal.metric, goal.period, goal.scenario, target,
            goal.conditional)


def slot_counts(gold: Sequence[NormGoal], predicted: Sequence[NormGoal]
                ) -> dict[str, tuple[int, int, int]]:
    """slot -> (TP, FP, FN) over the multisets of the slot's values."""
    found = {}
    for i, slot in enumerate(SLOTS):
        g = Counter(repr(x[i]) for x in gold)
        p = Counter(repr(x[i]) for x in predicted)
        tp = sum((g & p).values())
        found[slot] = (tp, sum(p.values()) - tp, sum(g.values()) - tp)
    return found


def describe_goal(g: NormGoal) -> str:
    kind, issuer, metric, period, scenario, target, conditional = g
    parts = [issuer, kind, metric or "(defaults)", f"p={period}"]
    if scenario:
        parts.append(scenario)
    if kind in PROJECTED_KINDS:
        parts.append(f"t={target}")
    if conditional:
        parts.append("conditional")
    return " ".join(str(p) for p in parts)


def slot_differences(gold: Sequence[NormGoal], predicted: Sequence[NormGoal]) -> list[str]:
    """Plain-words slot differences of a goal mismatch (one line each)."""
    diffs = []
    for i, slot in enumerate(SLOTS):
        g, p = Counter(x[i] for x in gold), Counter(x[i] for x in predicted)
        if g != p:
            missing, extra = sorted(map(str, (g - p).elements())), \
                sorted(map(str, (p - g).elements()))
            diffs.append(f"{slot}: gold {missing or '-'} vs compiled {extra or '-'}")
    if not diffs and len(gold) != len(predicted):
        diffs.append(f"{len(gold)} gold goals vs {len(predicted)} compiled")
    return diffs


# ---------------------------------------------------------------- answers

def _value(v: Any) -> str:
    if v is None:
        return "None"
    if isinstance(v, Decimal):
        return str(v.normalize()) if v.is_finite() else str(v)
    return repr(float(v))


def resolved_year(goal: Goal, origins: Mapping[str, int | None]) -> Any:
    origin = origins.get(goal.issuer)
    if origin is None:
        return goal.target if goal.kind in PROJECTED_KINDS else goal.period
    if goal.kind in PROJECTED_KINDS:
        return resolve_target(origin, goal.target)
    return origin if goal.period == "latest" else int(goal.period)


def answer_key(inv: Any, origins: Mapping[str, int | None]) -> Counter:
    """The requested goals' outcomes, comparable across a compiled and a reference run."""
    keys = []
    for g in inv.requested:
        values = tuple(sorted((o.metric, o.fiscal_year, o.scenario, _value(o.value))
                              for o in g.observations)) if g.status == VERIFIED else ()
        keys.append((g.goal.kind, g.goal.issuer, g.goal.metric, g.goal.scenario,
                     g.goal.conditional, resolved_year(g.goal, origins), g.status, g.reason,
                     values))
    return Counter(keys)


def answer_differences(got: Counter, want: Counter) -> list[str]:
    def short(k: tuple) -> str:
        kind, issuer, metric, scenario, _, year, status, reason, values = k
        shown = ", ".join(f"{m}={v}" for m, _, _, v in values[:3])
        return f"{issuer} {kind} {metric or '-'} {scenario} FY{year} {status} {reason}" + \
            (f" [{shown}{'...' if len(values) > 3 else ''}]" if values else "")
    return [f"missing: {short(k)}" for k in sorted((want - got).elements(), key=str)] + \
        [f"extra: {short(k)}" for k in sorted((got - want).elements(), key=str)]


# ---------------------------------------------------------------- pinned values

def _dec(text: Any) -> Decimal | None:
    if text in (None, ""):
        return None
    try:
        return Decimal(str(text))
    except InvalidOperation:
        return None


def _float(text: Any) -> float | None:
    return None if text in (None, "") else float(text)


@dataclass
class Pinned:
    """The pinned batch runs' values for the validation companies (read-only)."""

    facts: dict[tuple[str, int, str], set[Decimal]] = field(default_factory=dict)
    accepted: dict[tuple[str, int, str], Decimal] = field(default_factory=dict)
    lines: dict[tuple[str, str, int, str], float] = field(default_factory=dict)
    ratios: dict[tuple[str, int, str], float | None] = field(default_factory=dict)
    debt: dict[tuple[str, str, int], dict[str, float | None]] = field(default_factory=dict)
    identities: dict[tuple[str, int, str], Decimal | None] = field(default_factory=dict)
    signals: dict[tuple[str, int], dict[str, tuple[str, dict[str, float]]]] = \
        field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, runs_dir: Path, tickers: Iterable[str], statements: str, forecast: str,
             credit: str, insights: str) -> "Pinned":
        keep = set(tickers)
        p = cls(sources={"statements": statements, "forecast": forecast, "credit": credit,
                         "insights": insights})
        data = json.loads((runs_dir / statements / "statements.json").read_text("utf-8"))
        for company in data["companies"]:
            if company["issuer"] not in keep:
                continue
            for o in company["observations"]:
                key = (company["issuer"], int(o["fiscal_year"]), o["metric"])
                readings = {_dec(x) for x in (o["value"], o.get("tag_value"),
                                              o.get("document_value"), o.get("original_value"),
                                              *(o.get("candidates") or []))}
                p.facts[key] = {x for x in readings if x is not None}
                if o["accepted"] and _dec(o["value"]) is not None:
                    p.accepted[key] = _dec(o["value"])  # type: ignore[assignment]
        for row in _rows(runs_dir / statements / "identities.csv", keep, "issuer"):
            p.identities[(row["issuer"], int(row["fiscal_year"]), row["identity_id"])] = \
                _dec(row["residual"])
        for row in _rows(runs_dir / forecast / "projections.csv", keep):
            p.lines[(row["ticker"], row["scenario"], int(row["fiscal_year"]), row["line"])] = \
                float(row["value"])
        for row in _rows(runs_dir / credit / "ratios.csv", keep):
            p.ratios[(row["ticker"], int(row["fiscal_year"]), row["ratio_id"])] = \
                _float(row["value"])
        for row in _rows(runs_dir / credit / "debt_service.csv", keep):
            p.debt[(row["ticker"], row["scenario"], int(row["fiscal_year"]))] = \
                {k: _float(v) if k not in ("status", "detail") else None
                 for k, v in row.items() if k not in ("ticker", "split", "issuer", "scenario")}
        for row in _rows(runs_dir / insights / "signals.csv", keep):
            measures = {}
            for part in filter(None, (s.strip() for s in row["values"].split(";"))):
                name, _, number = part.partition("=")
                measures[name.strip()] = float(number)
            year = int(row["fiscal_year"].removeprefix("FY"))
            p.signals.setdefault((row["ticker"], year), {})[row["signal"]] = \
                (row["state"], measures)
        return p

    def check(self, kind: str, o: AnswerObservation) -> str | None:
        """None when the value equals the pinned value; else why not."""
        key = (o.issuer, o.fiscal_year)
        if kind in ("fact", "explain") and not o.metric.endswith("_change"):
            readings = self.facts.get((*key, o.metric))
            return _decimal_in(o.value, readings, "statements")
        if kind == "explain":
            base = o.metric.removesuffix("_change")
            now, before = self.accepted.get((*key, base)), \
                self.accepted.get((o.issuer, o.fiscal_year - 1, base))
            want = None if now is None or before is None else now - before
            return _same(o.value, want, 0.0, "statements (change)")
        if kind == "statements":
            return _same(o.value, self.identities.get((*key, o.metric)), 0.0, "identities")
        if kind in ("forecast", "liquidity"):
            return _same(o.value, self._line(o.issuer, o.scenario, o.fiscal_year, o.metric),
                         FLOAT_TOLERANCE, "projections")
        if kind == "credit":
            if (*key, o.metric) not in self.ratios:
                return f"no pinned ratio {o.metric}"
            return _same(o.value, self.ratios[(*key, o.metric)], FLOAT_TOLERANCE, "ratios")
        if kind == "debt":
            row = self.debt.get((o.issuer, o.scenario, o.fiscal_year))
            column = DEBT_COLUMNS.get(o.metric, o.metric)
            if row is None or column not in row:
                return f"no pinned debt service {o.metric}"
            return _same(o.value, row[column], FLOAT_TOLERANCE, "debt_service")
        if kind == "risk":
            states = self.signals.get(key)
            if states is None:
                return "no pinned signals for the year"
            if o.metric == "signals_fired":
                fired = sum(1 for s, _ in states.values() if s == "FIRED")
                return _same(o.value, fired, 0.0, "signals")
            signal, _, measure = o.metric.partition(".")
            state, measures = states.get(signal, ("", {}))
            if state != "FIRED" or measure not in measures:
                return f"{o.metric}: pinned state {state or 'none'}"
            return _same(o.value, measures[measure], SIGNAL_TOLERANCE, "signals")
        return f"no pinned source for kind {kind}"

    def _line(self, issuer: str, scenario: str, year: int, metric: str) -> float | None:
        if metric in DERIVED_FORECAST:
            parts = [(self.lines.get((issuer, scenario, year, line)), sign)
                     for line, sign in DERIVED_FORECAST[metric]]
            if any(v is None for v, _ in parts):
                return None
            return sum(v * s for v, s in parts)  # type: ignore[operator]
        return self.lines.get((issuer, scenario, year, metric))


def _rows(path: Path, keep: set[str], column: str = "ticker") -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [r for r in csv.DictReader(handle) if r[column] in keep]


def _decimal_in(value: Any, readings: set[Decimal] | None, source: str) -> str | None:
    if readings is None:
        return f"no pinned {source} reading"
    if isinstance(value, Decimal) and value in readings:
        return None
    return f"{value} is none of the pinned {source} readings {sorted(readings)}"


def _same(value: Any, want: Any, tolerance: float, source: str) -> str | None:
    if value is None or want is None:
        return None if value is None and want is None else f"{value} vs pinned {source} {want}"
    if isinstance(value, Decimal) and isinstance(want, Decimal):
        return None if value == want else f"{value} vs pinned {source} {want}"
    a, b = float(value), float(want)
    if math.isfinite(a) and abs(a - b) <= tolerance * max(1.0, abs(b)):
        return None
    return f"{value} vs pinned {source} {want}"


def audit(inv: Any, tools: Any, as_of: date, pinned: Pinned) -> tuple[int, list[str]]:
    """(values checked, problems) for every value the run returned (module docstring)."""
    checked, problems = 0, []
    filings: dict[str, dict[str, Any]] = {}
    for g in inv.goals:
        for o in g.observations:
            checked += 1
            tag = f"{g.goal.issuer} {g.goal.kind} {o.metric} FY{o.fiscal_year}"
            if o.issuer not in filings:
                filings[o.issuer] = {f.accession: f for f in tools.filings(o.issuer, as_of)}
            known = filings[o.issuer]
            if o.issuer != g.goal.issuer:
                problems.append(f"{tag}: issuer {o.issuer}")
            if not any(d.metric == o.metric and d.accepted for d in g.decisions):
                problems.append(f"{tag}: no accepted gate decision")
            single = g.goal.kind in ("fact", "explain") and SINGLE_CHANNEL in o.flags \
                and len(o.channels) == 1
            if not (o.validated or single):
                problems.append(f"{tag}: not validated and not a flagged single-channel fact")
            if o.value is None and NOT_MEANINGFUL not in o.flags:
                problems.append(f"{tag}: no value and not flagged {NOT_MEANINGFUL}")
            cited = known.get(o.accession)
            if cited is None or cited.filed != o.filed or o.filed > as_of:
                problems.append(f"{tag}: cites {o.accession} filed {o.filed}, not a 10-K of "
                                "the issuer filed by as_of")
            for source in o.sources:
                if source not in known:
                    problems.append(f"{tag}: source {source} is not a 10-K filed by as_of")
            why = pinned.check(g.goal.kind, o)
            if why:
                problems.append(f"{tag}: untraced ({why})")
    return checked, problems


# ---------------------------------------------------------------- one question

@dataclass(frozen=True)
class QuestionScore:
    id: str
    cell: str
    author: str
    group: str
    variant: str
    question: str
    selected_issuer: str
    expected: str
    gold_category: str
    end_to_end: bool
    gold: tuple[NormGoal, ...]
    predicted: tuple[NormGoal, ...]
    outcome: str  # GOALS or CLARIFICATION (what the compiler produced)
    predicted_category: str
    reason: str
    clause: str
    clauses: tuple[str, ...]  # how each clause was read (route, intent, confidence)
    goals_match: bool
    compiled_correct: bool
    status: str
    stop_reason: str
    answer: str
    reference_status: str
    answer_correct: bool | None
    answer_differences: tuple[str, ...]
    values: int
    problems: tuple[str, ...]
    seconds: float
    diagnostics: tuple[str, ...] = ()

    @property
    def slots(self) -> dict[str, tuple[int, int, int]]:
        return slot_counts(self.gold, self.predicted)


def score_compilation(record: Mapping[str, Any], compilation: Compilation,
                      origins: Mapping[str, int | None]) -> dict[str, Any]:
    """The compiler's part of one question's score (no tool is called)."""
    gold = tuple(normalise_goal(goal_from(g), origins) for g in record["goals"] or ())
    c = compilation.clarification
    outcome = CLARIFICATION if c is not None else GOALS
    predicted = () if c is not None else \
        tuple(normalise_goal(g, origins) for g in compilation.goals)
    goals_match = record["expected"] == GOALS and outcome == GOALS and \
        set(gold) == set(predicted)
    category = c.category if c is not None else ""
    correct = goals_match or (record["expected"] == CLARIFICATION and outcome == CLARIFICATION
                              and category == record["clarification_category"])
    clauses = tuple(f"{p.route}:{p.kind}"
                    + (f" {p.confidence:.3f}" if p.confidence is not None else "")
                    for p in compilation.clauses)
    return {"gold": gold, "predicted": predicted, "outcome": outcome,
            "predicted_category": category, "reason": c.reason if c is not None else "",
            "clause": c.clause if c is not None else "", "clauses": clauses,
            "goals_match": goals_match, "compiled_correct": correct}


def reference_compilation(record: Mapping[str, Any]) -> Compilation:
    goals = tuple(goal_from(g) for g in record["goals"] or ())
    issuers = tuple(dict.fromkeys(g.issuer for g in goals))
    return Compilation(record["question"], "e5-gold-goals", goals, issuers=issuers,
                       comparison=len(issuers) > 1)


def run_question(record: Mapping[str, Any], group: str, *, tools: Any, parser: Any,
                 resources: Any, as_of: date, universe: Mapping[str, str], pinned: Pinned,
                 run_id: str, trace_dir: Path | None = None,
                 diagnose: Callable[[str], tuple[str, ...]] | None = None,
                 investigate_fn: Callable[..., Any] | None = None) -> QuestionScore:
    """One gold question end to end, then its reference run when it is an end_to_end one."""
    if investigate_fn is None:
        from finagent.planner.investigate import investigate as investigate_fn
    started = time.perf_counter()
    selected = record["selected_issuer"] or None
    inv = investigate_fn(record["question"], as_of, tools=tools, parser=parser,
                         resources=resources, selected_issuer=selected, trace_dir=trace_dir,
                         run_id=f"{run_id}:{record['id']}")
    seconds = time.perf_counter() - started
    issuers = {g["issuer"] for g in record["goals"] or ()} | \
        {g.issuer for g in inv.compilation.goals}
    origins = origin_years(tools, issuers, as_of, universe)
    part = score_compilation(record, inv.compilation, origins)
    checked, problems = audit(inv, tools, as_of, pinned)
    reference_status, correct, diffs = "", None, []
    if record["end_to_end"]:
        ref = investigate_fn(record["question"], as_of, tools=tools, resources=resources,
                             trace_dir=trace_dir, run_id=f"{run_id}:{record['id']}:reference",
                             compilation=reference_compilation(record))
        reference_status = ref.status
        if inv.compilation.clarification is not None:
            correct, diffs = False, ["no answer: the compiler asked for a clarification"]
        else:
            diffs = answer_differences(answer_key(inv, origins), answer_key(ref, origins))
            correct = not diffs
    return QuestionScore(
        record["id"], record["cell"], record["author"], group, record["trap_type"],
        record["question"], record["selected_issuer"], record["expected"],
        record["clarification_category"], bool(record["end_to_end"]), part["gold"],
        part["predicted"], part["outcome"], part["predicted_category"], part["reason"],
        part["clause"], part["clauses"], part["goals_match"], part["compiled_correct"],
        inv.status, inv.stop_reason, inv.answer, reference_status, correct, tuple(diffs),
        checked, tuple(problems), round(seconds, 3),
        diagnose(record["question"]) if diagnose else ())


def m1_diagnostics(resources: Any, classifier: Any) -> Callable[[str], tuple[str, ...]]:
    """The trained classifier's top intent and confidence per clause (read only; for the error
    list's causes, never fed back to the compiler)."""
    from finagent.planner.compile import question_clauses

    def diagnose(question: str) -> tuple[str, ...]:
        clauses, _ = question_clauses(question, resources)
        if not clauses:
            return ()
        found = classifier.predict([c.body for c in clauses])
        return tuple(f"{c.body!r} -> {label} {conf:.3f}"
                     for c, (label, conf) in zip(clauses, found))

    return diagnose


# ---------------------------------------------------------------- metrics

def frac(k: int, n: int) -> dict[str, Any]:
    return {"k": k, "n": n, "rate": k / n if n else None, "ci95": wilson(k, n)}


def wilson(k: int, n: int, z: float = 1.959964) -> list[float] | None:
    if n == 0:
        return None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [max(0.0, centre - half), min(1.0, centre + half)]


def slot_f1(results: Sequence[QuestionScore]) -> dict[str, dict[str, Any]]:
    found = {}
    for slot in SLOTS:
        tp = fp = fn = 0
        for r in results:
            a, b, c = r.slots[slot]
            tp, fp, fn = tp + a, fp + b, fn + c
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = (2 * precision * recall / (precision + recall)
              if precision and recall else (0.0 if tp + fp + fn else None))
        found[slot] = {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall,
                       "f1": f1}
    return found


def summarise(results: Sequence[QuestionScore]) -> dict[str, Any]:
    goals = [r for r in results if r.expected == GOALS]
    clar = [r for r in results if r.expected == CLARIFICATION]
    compiled = [r for r in goals if r.outcome == GOALS]
    e2e = [r for r in results if r.end_to_end]
    by_category = {}
    for category in CATEGORIES:
        mine = [r for r in clar if r.gold_category == category]
        if mine:
            by_category[category] = {
                "correct": frac(sum(r.predicted_category == category for r in mine), len(mine)),
                "clarified": frac(sum(r.outcome == CLARIFICATION for r in mine), len(mine))}
    return {
        "questions": len(results),
        "goal_accuracy": frac(sum(r.goals_match for r in goals), len(goals)),
        "coverage": frac(len(compiled), len(goals)),
        "precision": frac(sum(r.goals_match for r in compiled), len(compiled)),
        "compiled_correct": frac(sum(r.compiled_correct for r in results), len(results)),
        "false_clarification": frac(len(goals) - len(compiled), len(goals)),
        "clarification": by_category,
        "clarification_correct": frac(sum(r.compiled_correct for r in clar), len(clar)),
        "answered_instead_of_clarifying": frac(sum(r.outcome == GOALS for r in clar), len(clar)),
        "answer_correct": frac(sum(bool(r.answer_correct) for r in e2e), len(e2e)),
        "slots": slot_f1(results),
        "values": sum(r.values for r in results),
        "unverified_values": sum(len(r.problems) for r in results),
        "questions_with_unverified": sum(bool(r.problems) for r in results),
        "status": dict(Counter(r.status for r in results)),
    }


def metrics(results: Sequence[QuestionScore]) -> dict[str, Any]:
    """Overall, by author and by request kind (grid group)."""
    by_author = {a: summarise([r for r in results if r.author == a]) for a in AUTHORS}
    groups = list(dict.fromkeys(r.group for r in results))
    return {"overall": summarise(results), "by_author": by_author,
            "by_group": {g: summarise([r for r in results if r.group == g]) for g in groups}}


def mcnemar(a: Mapping[str, bool], b: Mapping[str, bool]) -> dict[str, Any]:
    """Exact McNemar test on the shared questions: (a only right, b only right, p)."""
    shared = sorted(a.keys() & b.keys())
    only_a = sum(a[k] and not b[k] for k in shared)
    only_b = sum(b[k] and not a[k] for k in shared)
    n = only_a + only_b
    p = 1.0 if n == 0 else min(1.0, 2 * sum(math.comb(n, k) for k in
                                            range(min(only_a, only_b) + 1)) / 2 ** n)
    return {"questions": len(shared), "a_only_correct": only_a, "b_only_correct": only_b,
            "p_value": p}


def is_error(r: QuestionScore) -> bool:
    return not r.compiled_correct or r.answer_correct is False


def cause(r: QuestionScore) -> str:
    """One line: what went wrong (mechanical; the report notes add the diagnosis)."""
    if r.expected == GOALS and r.outcome == CLARIFICATION:
        where = f" on {r.clause!r}" if r.clause else ""
        return f"clarified {r.reason} ({r.predicted_category}){where}"
    if r.expected == CLARIFICATION and r.outcome == GOALS:
        return f"answered with goals; expected a {r.gold_category} clarification"
    if r.expected == CLARIFICATION:
        return (f"clarified {r.reason} ({r.predicted_category}); expected {r.gold_category}")
    if not r.goals_match:
        return "; ".join(slot_differences(r.gold, r.predicted))
    return "answer differs: " + "; ".join(r.answer_differences[:2])


# ---------------------------------------------------------------- files

def score_record(r: QuestionScore) -> dict[str, Any]:
    return plain(asdict(r))


def load_scores(path: Path) -> list[QuestionScore]:
    found = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            raw = json.loads(line)
            for key in ("gold", "predicted"):
                raw[key] = tuple(tuple(g) for g in raw[key])
            for key in ("clauses", "answer_differences", "problems", "diagnostics"):
                raw[key] = tuple(raw.get(key) or ())
            found.append(QuestionScore(**raw))
    return found


def write_run(out: Path, header: Mapping[str, Any], results: Sequence[QuestionScore],
              summary: Mapping[str, Any]) -> None:
    """runs/<run-id>/: header.json, questions.jsonl, metrics.json (trace/ written during it)."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "header.json").write_text(json.dumps(plain(dict(header)), indent=2))
    (out / "metrics.json").write_text(json.dumps(plain(dict(summary)), indent=2))
    with (out / "questions.jsonl").open("w", encoding="utf-8") as handle:
        for r in results:
            handle.write(json.dumps(score_record(r)) + "\n")


def tally(results: Sequence[QuestionScore], key: Callable[[QuestionScore], str]
          ) -> dict[str, list[QuestionScore]]:
    found: dict[str, list[QuestionScore]] = defaultdict(list)
    for r in results:
        found[key(r)].append(r)
    return dict(found)


# ---------------------------------------------------------------- report

def _pct(block: Mapping[str, Any] | None, interval: bool = False) -> str:
    if not block or block.get("rate") is None:
        return "n/a"
    ci = block.get("ci95") if interval else None
    span = f" [{ci[0]:.0%}, {ci[1]:.0%}]" if ci else ""
    return f"{block['k']}/{block['n']} = {block['rate']:.1%}{span}"


def _num(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.3f}"


def _table(head: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(str(c).replace("|", "/").replace("\n", " ") for c in row)
              + " |" for row in rows]
    return lines


HEADLINE = (("goal accuracy", "goal_accuracy"), ("coverage (compiled into goals)", "coverage"),
            ("precision (correct when compiled)", "precision"),
            ("false clarification", "false_clarification"),
            ("correct clarification (all categories)", "clarification_correct"),
            ("answered instead of clarifying", "answered_instead_of_clarifying"),
            ("compiled correctly (all questions)", "compiled_correct"),
            ("answer correct (end_to_end questions)", "answer_correct"))


def render_report(runs: Sequence[tuple[Mapping[str, Any], Sequence[QuestionScore]]],
                  point_out: Mapping[str, Sequence[str]]) -> str:
    """docs/e5_v1.md from the runs (in order; the last one is the trained compiler's)."""
    first = runs[0][0]
    ids = [h["run_id"] for h, _ in runs]
    summaries = {h["run_id"]: metrics(rs) for h, rs in runs}
    lines = [f"# E5 planner evaluation ({', '.join(ids)})", "",
             f"Generated by eval/e5_planner.py report. Gold set {first['gold_file']} (sha256 "
             f"{first['gold_sha256'][:12]}, frozen; grid sha256 {first['grid_sha256'][:12]}); "
             f"as_of {first['as_of']}; validation companies only; evidence manifest "
             f"{first['evidence_manifest_sha256'][:12]}.", ""]
    for h, _ in runs:
        lines.append(f"- {h['run_id']}: {h['compiler_name']} ({h['compiler']}"
                     + (f", threshold {h['threshold']}" if h.get("threshold") is not None
                        else "") + f"), git {h['git_commit']}{' (dirty)' if h['git_dirty'] else ''}"
                     f", {h['seconds']:.0f} s.")
    lines += ["", "Definitions: finagent/evaluation/e5.py (module docstring), fixed before "
              "e5-0001. **Projected year:** a blank target and an explicit year equal to the "
              "first projected year score as equal; both gold and compiled targets are resolved "
              "to a fiscal year (blank = latest fiscal year + 1, \"+n\" = latest + n) before "
              "comparing. Every other slot compares strictly. Intervals: 95% Wilson.", "",
              "**LLM fallback (blueprint 10.4 pass^k, k = 3): not run.** There is no external AI "
              "API (CLAUDE.md rule 9) and no local LLM in Phase 6 (#121). This is a limitation, "
              "not a result.", "",
              "**Limitation:** the builder's 50 canonical questions and all 120 gold labels were "
              "written after training; Vineet's 70 questions are the blind part (#207).", "",
              "## 1. Headline", ""]
    rows = [[name] + [_pct(summaries[i]["overall"][key], True) for i in ids]
            for name, key in HEADLINE]
    rows.append(["LLM fallback pass^3"] + ["not run (limitation)"] * len(ids))
    lines += _table(["measure"] + ids, rows)
    if len(runs) > 1:
        a, b = runs[0], runs[-1]
        test = mcnemar({r.id: r.compiled_correct for r in a[1]},
                       {r.id: r.compiled_correct for r in b[1]})
        lines += ["", f"Compiled correctly, paired by question ({a[0]['run_id']} vs "
                  f"{b[0]['run_id']}): {test['a_only_correct']} questions right only under "
                  f"{a[0]['run_id']}, {test['b_only_correct']} only under {b[0]['run_id']}; "
                  f"exact McNemar p = {test['p_value']:.2g}."]
    lines += ["", "## 2. By author (Vineet's blind 70, the builder's 50)", ""]
    for i in ids:
        s = summaries[i]["by_author"]
        lines += [f"**{i}**", ""]
        lines += _table(["measure"] + list(AUTHORS),
                        [[name] + [_pct(s[a][key]) for a in AUTHORS] for name, key in HEADLINE])
        lines.append("")
    lines += ["## 3. By request kind (grid group)", ""]
    for i in ids:
        s = summaries[i]["by_group"]
        lines += [f"**{i}**", ""]
        lines += _table(["group", "questions", "goal accuracy", "coverage", "precision",
                         "correct clarification", "answer correct"],
                        [[g, s[g]["questions"], _pct(s[g]["goal_accuracy"]),
                          _pct(s[g]["coverage"]), _pct(s[g]["precision"]),
                          _pct(s[g]["clarification_correct"]), _pct(s[g]["answer_correct"])]
                         for g in s])
        lines.append("")
    lines += ["## 4. Slot F1 per slot", "",
              "TP / FP / FN over the multisets of each slot's values per question (module "
              "docstring); P, R, F1.", ""]
    for i in ids:
        lines += [f"**{i}**", ""]
        rows = []
        for slot in SLOTS:
            o = summaries[i]["overall"]["slots"][slot]
            per = [_num(summaries[i]["by_author"][a]["slots"][slot]["f1"]) for a in AUTHORS]
            rows.append([slot, o["tp"], o["fp"], o["fn"], _num(o["precision"]),
                         _num(o["recall"]), _num(o["f1"])] + per)
        lines += _table(["slot", "TP", "FP", "FN", "P", "R", "F1", "F1 vineet",
                         "F1 canonical"], rows)
        lines += ["", "F1 by request kind:", ""]
        groups = summaries[i]["by_group"]
        lines += _table(["group"] + list(SLOTS),
                        [[g] + [_num(groups[g]["slots"][s]["f1"]) for s in SLOTS]
                         for g in groups])
        lines.append("")
    lines += ["## 5. Clarification per category", "",
              "correct: a clarification of the gold category; clarified: any clarification.", ""]
    rows = []
    for i in ids:
        s = summaries[i]
        for category in CATEGORIES:
            cells = [s["overall"]["clarification"].get(category)] + \
                [s["by_author"][a]["clarification"].get(category) for a in AUTHORS]
            rows.append([i, category] + [_pct(c["correct"]) if c else "n/a" for c in cells]
                        + [_pct(cells[0]["clarified"]) if cells[0] else "n/a"])
    lines += _table(["run", "category", "correct", "correct (vineet)", "correct (canonical)",
                     "clarified (any category)"], rows)
    by_variant = []
    for h, rs in runs:
        for variant, mine in sorted(tally([r for r in rs if r.expected == CLARIFICATION],
                                          lambda r: f"{r.gold_category}/{r.variant or '-'}"
                                          ).items()):
            by_variant.append([h["run_id"], variant, f"{sum(r.compiled_correct for r in mine)}"
                               f"/{len(mine)}", ", ".join(sorted(Counter(
                                   r.reason or "goals" for r in mine).elements()))])
    lines += ["", "By variant (what the compiler did):", ""]
    lines += _table(["run", "category/variant", "correct", "outcomes"], by_variant)
    lines += ["", "## 6. Gate and fabrication check", "",
              "Every value each run returned (requested goals and prerequisites): gate-accepted, "
              "validated or a flagged single-channel fact, cites a 10-K filed by as_of, and "
              "equals the pinned batch value (" + ", ".join(
                  f"{k} {v}" for k, v in first["pinned"].items()) + ").", ""]
    lines += _table(["run", "values checked", "unverified or untraced", "questions affected"],
                    [[i, summaries[i]["overall"]["values"],
                      summaries[i]["overall"]["unverified_values"],
                      summaries[i]["overall"]["questions_with_unverified"]] for i in ids])
    for h, rs in runs:
        for r in rs:
            for p in r.problems:
                lines.append(f"- {h['run_id']} {r.id}: {p}")
    watch = {q: name for name, qs in point_out.items() for q in qs}
    h, rs = runs[-1]
    errors = [r for r in rs if is_error(r)]
    lines += ["", f"## 7. Every {h['run_id']} error ({len(errors)})", "",
              "Compiled incorrectly, or compiled correctly with an answer that differs from the "
              "reference. " + "; ".join(f"{name}: {', '.join(qs)}" for name, qs in
                                        point_out.items()) + " (marked).", ""]
    rows = []
    for r in errors:
        gold = "; ".join(describe_goal(g) for g in r.gold) or f"CLARIFY {r.gold_category}"
        got = "; ".join(describe_goal(g) for g in r.predicted) or \
            f"CLARIFY {r.predicted_category} ({r.reason})"
        rows.append([r.id + (f" {watch[r.id]}" if r.id in watch else ""), r.author,
                     f"{r.group}{'/' + r.variant if r.variant else ''}", r.question, gold, got,
                     cause(r)])
    lines += _table(["id", "author", "kind", "question", "gold", "compiled", "cause"], rows)
    lines += ["", "Classifier reading of each clause (M1 top intent and confidence; the "
              "threshold sends lower ones to the grammar):", ""]
    lines += [f"- {r.id}: " + "; ".join(r.diagnostics) for r in errors if r.diagnostics]
    for h0, rs0 in runs[:-1]:
        errs = [r for r in rs0 if is_error(r)]
        lines += ["", f"## 8. {h0['run_id']} errors ({len(errs)}), by cause", "",
                  f"Per question: runs/{h0['run_id']}/questions.jsonl.", ""]
        lines += _table(["what happened", "questions"],
                        sorted(((k, len(v)) for k, v in tally(
                            errs, lambda r: (f"clarified {r.reason}" if r.outcome ==
                                             CLARIFICATION else "goals: " + ", ".join(
                                                 sorted({d.split(':')[0] for d in
                                                         slot_differences(r.gold, r.predicted)}
                                                        ) or ["answer differs"]))).items()),
                               key=lambda x: -x[1]))
    return "\n".join(lines) + "\n"
