"""E2: Correlation Agent ablation (blueprint 10.2; Phase 5c).

Evaluation mode: the agent sees each validation company-year's rows, M1 predictions and the
filing only (finagent.verify.workspace.FilingState.evaluation has no tag argument). Two
conditions per company-year:

- clean: the real M1 v3 readings;
- corrupted: one injected error through the in-memory edit hook (finagent.verify.edits), the
  type rotating over the sorted company-years (wrong_row, rescale_1000, flip_sign,
  second_candidate; configs/correlation_agent.yaml e2). The injected item is a scored face item
  the document channel read correctly, and the injection makes it wrong.

The answer key is E1's: the tag channel's VERIFIED value for face items (BC8), with BC13 twins
and BC14 rule keys. It is read by the scorer and by the harness that picks injections, never by
the agent. Reported: detection (the agent flags the injected value: CORRECTED or REVIEW),
correction to the true value, correct values wrongly changed, steps, field accuracy before /
after with McNemar, identity pass rate; split by injection type and detectability; bootstrap
CIs over companies.

Three levels of detectability for an injected error: *visible* (a check fails that does not fail
the same way on the clean readings: the identity library sees the error), *located* (the
injected item itself is an input of a failing check, so the agent can suspect it; a face-block
total's rows are not inputs) and neither (*invisible*: every check passes as on the clean
readings).
"""

import json
import random
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any

from finagent.config import AgentConfig, E2Config, check_not_held_out
from finagent.document.normalize import value_for_period
from finagent.evaluation.e1 import CORRECT, FieldResult, bootstrap_ci, field_accuracy, \
    mcnemar, score_filing
from finagent.evidence.store import FilingRecord
from finagent.records import AgentResult, DocumentEdit, Outcome, Prediction, RowInstance
from finagent.statements.build import StatementConfigs
from finagent.verify import within_precision
from finagent.verify.agent_tools import candidates
from finagent.verify.correlation_agent import ACTION_LIMIT, run_agent
from finagent.verify.workspace import FilingState

CLEAN, CORRUPTED = "clean", "corrupted"


# ---------------------------------------------------------------- inputs

@dataclass(frozen=True)
class Case:
    """One company-year as the document channel reads it: no tags."""

    filing: FilingRecord
    rows: tuple[RowInstance, ...]
    predictions: tuple[Prediction, ...]

    @property
    def key(self) -> tuple[str, int]:
        return self.filing.issuer, self.filing.fiscal_year


@dataclass(frozen=True)
class AnswerKey:
    """The scorer's held-back tags for one company-year (never given to the agent)."""

    tags: Mapping[str, Outcome]
    on_face: frozenset[str]
    twins: Mapping[str, str]


@dataclass(frozen=True)
class Injection:
    issuer: str
    fiscal_year: int
    kind: str
    assigned: str  # the type the rotation assigned; kind differs when that was not possible
    item: str
    edit: DocumentEdit
    true_value: Decimal
    clean_value: Decimal
    injected_value: Decimal


@dataclass(frozen=True)
class CaseResult:
    issuer: str
    fiscal_year: int
    accession: str
    condition: str
    prior: float
    injection: Injection | None
    detectable: bool | None  # corrupted: the injected item enters a failing check
    detected: bool | None  # corrupted: the agent flagged it (CORRECTED or REVIEW)
    corrected: bool | None  # corrupted: its final value is the true value
    actions_used: int
    stop_reason: str
    values: tuple[tuple[str, str], ...]  # (item, CORRECTED / REVIEW)
    before: tuple[FieldResult, ...]
    after: tuple[FieldResult, ...]
    checks_before: tuple[int, int]  # (PASS, computed)
    checks_after: tuple[int, int]
    wrongly_changed: tuple[str, ...]  # scored fields right before the agent, wrong after
    fixed: tuple[str, ...]  # scored fields wrong before the agent, right after
    unscored_changes: tuple[str, ...]  # items changed that have no answer key
    checked: tuple[str, ...] = ()  # scored items entering a computed check at the start
    visible: bool | None = None  # corrupted: the injection makes a check fail (any input)
    flagged: bool | None = None  # corrupted: a value in a newly failing check is flagged


# ---------------------------------------------------------------- scoring (answer key side)

def score(case: Case, key: AnswerKey, documents: Mapping) -> list[FieldResult]:
    """E1 field results for one set of document readings."""
    return score_filing(case.filing.issuer, case.filing.fiscal_year, dict(documents),
                        dict(key.tags), {r.row_id: r for r in case.rows}, set(key.on_face),
                        key.twins)


def _right(results: Iterable[FieldResult]) -> dict[str, bool]:
    return {r.item: r.outcome == CORRECT for r in results}


# ---------------------------------------------------------------- injections

def _state(case: Case, configs: StatementConfigs, agent: AgentConfig,
           injections: Sequence[DocumentEdit] = ()) -> FilingState:
    return FilingState.evaluation(case.filing, case.rows, case.predictions, configs, agent,
                                  injections)


def _wrong(value: Decimal | None, scale: int | None, truth: Decimal) -> bool:
    return value is not None and not within_precision(value, scale, truth)


def injection_rows(state: FilingState, item: str, kind: str, truth: Decimal, rng: random.Random,
                   as_of: date) -> list[tuple[str, Decimal, bool]]:
    """(row id, factor, negate) choices for one injection type, best first (may be empty)."""
    reading = state.reading(item)
    spec = state.configs.concept_map[item]
    if reading.row_id is None or reading.value is None:
        return []
    if kind == "rescale_1000":
        return [(reading.row_id, Decimal(1000), False)]
    if kind == "flip_sign":
        return [(reading.row_id, Decimal(1), True)] if reading.value != 0 else []
    if kind == "second_candidate":
        return [(c.row_id, Decimal(1), False) for c in candidates(state, item, as_of)
                if _wrong(c.value, c.scale, truth)][:1]
    # wrong_row: another row of the same statement, not a candidate of the item
    near = {c.row_id for c in candidates(state, item, as_of)}
    rows = []
    for row in state.rows:
        if row.statement != reading.statement or row.row_id in near | {reading.row_id}:
            continue
        value = value_for_period(row, state.filing.end, spec.sign)
        if value is not None and _wrong(value.value, value.scale, truth):
            rows.append(row.row_id)
    return [(rng.choice(sorted(rows)), Decimal(1), False)] if rows else []


def choose_injection(case: Case, key: AnswerKey, configs: StatementConfigs, agent: AgentConfig,
                     assigned: str, kinds: Sequence[str], rng: random.Random, as_of: date
                     ) -> Injection | None:
    """One injected error for the case: the assigned type if possible, else the next types in
    rotation; the item drawn at random among scored face items read correctly."""
    state = _state(case, configs, agent)
    clean = score(case, key, state.documents)
    eligible = sorted(r.item for r in clean if r.strict == CORRECT)
    rng.shuffle(eligible)
    start = kinds.index(assigned)
    for kind in [kinds[(start + i) % len(kinds)] for i in range(len(kinds))]:
        for item in eligible:
            truth = next(r.tag_value for r in clean if r.item == item)
            for row_id, factor, negate in injection_rows(state, item, kind, truth, rng, as_of):
                edit = DocumentEdit(case.filing.accession, case.filing.end, item, row_id, factor,
                                    negate, source="injection", actions=(kind,))
                injected = _state(case, configs, agent, [edit])
                after = {r.item: r for r in score(case, key, injected.documents)}
                if item in after and after[item].outcome != CORRECT:
                    return Injection(case.filing.issuer, case.filing.fiscal_year, kind, assigned,
                                     item, edit, truth, state.reading(item).value,
                                     injected.reading(item).value)
    return None


def plan_injections(cases: Sequence[Case], keys: Mapping[tuple[str, int], AnswerKey],
                    configs: StatementConfigs, agent: AgentConfig, e2: E2Config, as_of: date
                    ) -> dict[tuple[str, int], Injection | None]:
    """One injection per company-year, reproducible from the seed."""
    rng = random.Random(e2.seed)
    kinds = list(e2.injection_types)
    ordered = sorted(cases, key=lambda c: c.key)
    return {c.key: choose_injection(c, keys[c.key], configs, agent, kinds[i % len(kinds)],
                                    kinds, rng, as_of)
            for i, c in enumerate(ordered)}


# ---------------------------------------------------------------- one run

def _pass_counts(state: FilingState) -> tuple[int, int]:
    checks = state.view.checks
    return sum(c.status == "PASS" for c in checks), len(checks)


def evaluate_case(case: Case, key: AnswerKey, configs: StatementConfigs, agent: AgentConfig,
                  as_of: date, injection: Injection | None, condition: str) -> CaseResult:
    """Run the agent on one company-year (tags hidden) and score it afterwards."""
    start = _state(case, configs, agent, [injection.edit] if injection else [])
    result: AgentResult = run_agent(start, as_of)
    final = replace(start, edits={e.item: e for e in result.edits})
    # Scoring only after the agent has finished.
    before, after = score(case, key, start.documents), score(case, key, final.documents)
    right_before, right_after = _right(before), _right(after)
    scored = set(right_before)
    changed = {e.item for e in result.edits}
    checked = {i for c in start.view.checks for i in c.inputs} & scored
    detectable = detected = corrected = visible = flagged = None
    if injection is not None:
        item = injection.item
        detectable = any(c.status == "FAIL" and item in c.inputs for c in start.view.checks)
        detected = item in {v.item for v in result.values}
        corrected = right_after.get(item, False)
        new = newly_failing(_state(case, configs, agent), start)
        visible = bool(new)
        flagged = bool({v.item for v in result.values} & {i for c in new for i in c.inputs})
    return CaseResult(
        case.filing.issuer, case.filing.fiscal_year, case.filing.accession, condition,
        agent.rescale_prior, injection, detectable, detected, corrected, result.actions_used,
        result.stop_reason, tuple((v.item, v.status) for v in result.values), tuple(before),
        tuple(after), _pass_counts(start), _pass_counts(final),
        tuple(sorted(i for i in scored if right_before[i] and not right_after.get(i, False))),
        tuple(sorted(i for i in scored if not right_before[i] and right_after.get(i, False))),
        tuple(sorted(changed - scored)), tuple(sorted(checked)), visible, flagged)


def newly_failing(clean: FilingState, injected: FilingState) -> list:
    """Checks failing on the injected readings that do not fail the same way (same check, same
    residual) on the clean readings."""
    before = {(c.check_id, c.residual) for c in clean.view.checks if c.status == "FAIL"}
    return [c for c in injected.view.checks
            if c.status == "FAIL" and (c.check_id, c.residual) not in before]


def run_condition(cases: Sequence[Case], keys: Mapping[tuple[str, int], AnswerKey],
                  configs: StatementConfigs, agent: AgentConfig, as_of: date,
                  injections: Mapping[tuple[str, int], Injection | None] | None = None
                  ) -> list[CaseResult]:
    """Every case once: clean (injections None) or corrupted."""
    condition = CLEAN if injections is None else CORRUPTED
    found = []
    for case in sorted(cases, key=lambda c: c.key):
        injection = injections.get(case.key) if injections is not None else None
        if injections is not None and injection is None:
            continue  # no injectable item (reported in the run)
        found.append(evaluate_case(case, keys[case.key], configs, agent, as_of, injection,
                                   condition))
    return found


# ---------------------------------------------------------------- metrics

def bootstrap_rate(cases: Sequence[tuple[str, bool]], draws: int = 2000, seed: int = 0,
                   level: float = 0.95) -> tuple[float, float] | None:
    """Percentile interval of a rate over cases, resampling companies."""
    by_issuer: dict[str, list[bool]] = defaultdict(list)
    for issuer, hit in cases:
        by_issuer[issuer].append(hit)
    issuers = sorted(by_issuer)
    if not issuers:
        return None
    rng = random.Random(seed)
    estimates = []
    for _ in range(draws):
        sample = [h for i in rng.choices(issuers, k=len(issuers)) for h in by_issuer[i]]
        estimates.append(sum(sample) / len(sample))
    estimates.sort()
    return (estimates[int((1 - level) / 2 * draws)],
            estimates[min(draws - 1, int((1 + level) / 2 * draws))])


def rate(results: Sequence[CaseResult], hit: Callable[[CaseResult], bool], draws: int
         ) -> dict[str, Any]:
    pairs = [(r.issuer, bool(hit(r))) for r in results]
    n = len(pairs)
    k = sum(h for _, h in pairs)
    return {"k": k, "n": n, "rate": k / n if n else None,
            "ci95": bootstrap_rate(pairs, draws) if n else None}


def _fields(results: Sequence[CaseResult], attr: str) -> list[FieldResult]:
    return [f for r in results for f in getattr(r, attr)]


def condition_metrics(results: Sequence[CaseResult], draws: int) -> dict[str, Any]:
    """Every E2 metric for one condition (and one prior)."""
    before, after = _fields(results, "before"), _fields(results, "after")
    only_before, only_after, p = mcnemar(before, after)
    correct_before = sum(f.outcome == CORRECT for f in before)
    wrong = sum(len(r.wrongly_changed) for r in results)
    checks = {k: [sum(getattr(r, k)[i] for r in results) for i in (0, 1)]
              for k in ("checks_before", "checks_after")}
    metrics: dict[str, Any] = {
        "cases": len(results),
        "field_accuracy_before": field_accuracy(before),
        "field_accuracy_after": field_accuracy(after),
        "ci95_before": bootstrap_ci(before, draws), "ci95_after": bootstrap_ci(after, draws),
        "fields": len(before),
        "fields_in_a_check": _share(sum(len(r.checked) for r in results), len(before)),
        "mcnemar": {"before_only_correct": only_before, "after_only_correct": only_after,
                    "p_value": p},
        "wrongly_changed": wrong,
        "wrongly_changed_rate": wrong / correct_before if correct_before else None,
        "wrongly_changed_cases": rate(results, lambda r: bool(r.wrongly_changed), draws),
        "fixed": sum(len(r.fixed) for r in results),
        "unscored_changes": sum(len(r.unscored_changes) for r in results),
        "values": dict(_count(s for r in results for _, s in r.values)),
        "value_outcomes": value_outcomes(results),
        "actions_mean": sum(r.actions_used for r in results) / len(results) if results else None,
        "actions_max": max((r.actions_used for r in results), default=0),
        "action_limit_share": rate(results, lambda r: r.stop_reason == ACTION_LIMIT, draws),
        "stop_reasons": dict(_count(r.stop_reason for r in results)),
        "identity_pass_rate_before": _share(*checks["checks_before"]),
        "identity_pass_rate_after": _share(*checks["checks_after"]),
        "all_checks_pass_before": rate(results, lambda r: r.checks_before[0] ==
                                       r.checks_before[1], draws),
        "all_checks_pass_after": rate(results, lambda r: r.checks_after[0] == r.checks_after[1],
                                      draws),
    }
    injected = [r for r in results if r.injection is not None]
    if injected:
        metrics.update(injection_metrics(injected, draws))
    return metrics


def injection_metrics(results: Sequence[CaseResult], draws: int) -> dict[str, Any]:
    """Detection and correction, overall and split by type and by detectability."""
    def block(rs: Sequence[CaseResult]) -> dict[str, Any]:
        return {"cases": len(rs), "visible": rate(rs, lambda r: r.visible, draws),
                "detectable": rate(rs, lambda r: r.detectable, draws),
                "detected": rate(rs, lambda r: r.detected, draws),
                "flagged": rate(rs, lambda r: r.flagged, draws),
                "corrected": rate(rs, lambda r: r.corrected, draws),
                "wrongly_changed_cases": rate(rs, lambda r: bool(r.wrongly_changed), draws)}

    kinds = sorted({r.injection.kind for r in results if r.injection})
    return {
        "injected": block(results),
        "by_type": {k: block([r for r in results if r.injection and r.injection.kind == k])
                    for k in kinds},
        "by_detectability": {
            "located": block([r for r in results if r.detectable]),
            "visible_not_located": block([r for r in results
                                          if r.visible and not r.detectable]),
            "invisible": block([r for r in results if not r.visible and not r.detectable])},
    }


def value_outcomes(results: Sequence[CaseResult]) -> dict[str, dict[str, int]]:
    """Each value the agent flagged (CORRECTED / REVIEW), by whether its starting reading was
    right, wrong or unscored: a REVIEW on a right reading is a false alarm (value kept)."""
    found: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in results:
        right = _right(r.before)
        for item, status in r.values:
            bucket = "unscored" if item not in right else \
                "right_before" if right[item] else "wrong_before"
            found[status][bucket] += 1
    return {s: dict(sorted(b.items())) for s, b in sorted(found.items())}


def _count(values: Iterable[str]) -> dict[str, int]:
    found: dict[str, int] = defaultdict(int)
    for v in values:
        found[v] += 1
    return dict(sorted(found.items()))


def _share(k: int, n: int) -> float | None:
    return k / n if n else None


# ---------------------------------------------------------------- reading the filings

def read_case(store, filing: FilingRecord, classifier, as_of: date) -> tuple[Case, list]:
    """The document channel's inputs for one filing, plus its tag facts for the scorer only."""
    from finagent.evidence.integrity import read_snapshot
    from finagent.filing_rows import read_filing_rows

    if filing.filed > as_of:
        raise ValueError(f"{filing.accession} was filed {filing.filed}, after {as_of}")
    document = read_snapshot(store.evidence_dir, filing.primary_document, store.manifest)
    located = read_filing_rows(document, filing.accession, store.fiscal_periods(filing))
    rows = tuple(r for rs in located.rows.values() for r in rs)
    predictions = tuple(classifier.predict(list(rows))) if rows else ()
    return Case(filing, rows, predictions), list(located.facts)


def answer_key(store, case: Case, facts: Sequence, configs: StatementConfigs, as_of: date,
               equivalences: Sequence) -> AnswerKey:
    """E1's answer key for one filing (verified tags, face items, BC13 twins, BC14 keys)."""
    from finagent.dataset.labels import concept_owner, label_rows
    from finagent.evaluation.e1 import equivalent_items, rule_answer_keys
    from finagent.evidence.integrity import read_snapshot_json
    from finagent.tags.channel import read_items

    filing = case.filing
    company_facts = read_snapshot_json(store.evidence_dir, f"companyfacts_{filing.cik}.json",
                                       store.manifest)
    tags = read_items(filing, list(configs.concept_map.values()), company_facts, list(facts),
                      as_of)
    labels = [label for _, label in label_rows(list(case.rows), list(facts),
                                               concept_owner(configs.concept_map))]
    twins, _ = equivalent_items(facts, filing, equivalences)
    return AnswerKey(rule_answer_keys(tags, labels), frozenset(l.label for l in labels), twins)


def load_cases(store, tickers: Sequence[str], classifier, configs: StatementConfigs, as_of: date,
               equivalences: Sequence, universe: Mapping[str, str]
               ) -> tuple[list[Case], dict[tuple[str, int], AnswerKey]]:
    """Every pinned filing of `tickers` with its answer key. E2 never opens a held-out company
    (there is no flag to allow it)."""
    check_not_held_out(tickers, universe, False)
    cases, keys = [], {}
    for ticker in tickers:
        for filing in store.load_filings(ticker, as_of):
            case, facts = read_case(store, filing, classifier, as_of)
            cases.append(case)
            keys[case.key] = answer_key(store, case, facts, configs, as_of, equivalences)
    return cases, keys


# ---------------------------------------------------------------- the full run

@dataclass(frozen=True)
class E2Run:
    priors: tuple[float, ...]  # the configured prior first, then the sensitivity priors
    injections: Mapping[tuple[str, int], Injection | None]
    results: Mapping[tuple[float, str], tuple[CaseResult, ...]]
    metrics: Mapping[tuple[float, str], dict[str, Any]]


def run_e2(cases: Sequence[Case], keys: Mapping[tuple[str, int], AnswerKey],
           configs: StatementConfigs, agent: AgentConfig, e2: E2Config, as_of: date) -> E2Run:
    """Clean and corrupted at the configured prior and at each sensitivity prior (rescale and
    flip priors set together). The injections are planned once and shared by every prior."""
    injections = plan_injections(cases, keys, configs, agent, e2, as_of)
    priors = (agent.rescale_prior,
              *[p for p in e2.prior_sensitivity if p != agent.rescale_prior])
    results, metrics = {}, {}
    for prior in priors:
        run = agent if prior == agent.rescale_prior else \
            replace(agent, rescale_prior=prior, flip_prior=prior)
        for condition, planned in ((CLEAN, None), (CORRUPTED, injections)):
            found = tuple(run_condition(cases, keys, configs, run, as_of, planned))
            results[prior, condition] = found
            metrics[prior, condition] = condition_metrics(found, e2.bootstrap_draws)
    return E2Run(priors, injections, results, metrics)


# ---------------------------------------------------------------- records

def _text(value: Any) -> Any:
    return None if value is None else str(value)


def injection_record(i: Injection) -> dict[str, Any]:
    return {"issuer": i.issuer, "fiscal_year": i.fiscal_year, "kind": i.kind,
            "assigned": i.assigned, "item": i.item, "row_id": i.edit.row_id,
            "factor": str(i.edit.factor), "negate": i.edit.negate,
            "true_value": str(i.true_value), "clean_value": _text(i.clean_value),
            "injected_value": _text(i.injected_value)}


def case_record(r: CaseResult) -> dict[str, Any]:
    """One CaseResult as JSON: every field's outcome and value before and after the agent."""
    after = {f.item: f for f in r.after}
    fields = [{"item": f.item, "tag_value": str(f.tag_value), "before": f.outcome,
               "before_value": _text(f.document_value),
               "after": after[f.item].outcome if f.item in after else None,
               "after_value": _text(after[f.item].document_value) if f.item in after else None}
              for f in r.before]
    return {"issuer": r.issuer, "fiscal_year": r.fiscal_year, "accession": r.accession,
            "condition": r.condition, "prior": r.prior,
            "injection": injection_record(r.injection) if r.injection else None,
            "visible": r.visible, "detectable": r.detectable, "detected": r.detected,
            "flagged": r.flagged, "corrected": r.corrected, "checked": list(r.checked),
            "actions_used": r.actions_used, "stop_reason": r.stop_reason,
            "values": [list(v) for v in r.values], "checks_before": list(r.checks_before),
            "checks_after": list(r.checks_after), "wrongly_changed": list(r.wrongly_changed),
            "fixed": list(r.fixed), "unscored_changes": list(r.unscored_changes),
            "fields": fields}


def metrics_record(run: E2Run) -> dict[str, Any]:
    """{prior: {condition: metrics}} as JSON-ready text keys."""
    return {f"{p:g}": {c: run.metrics[p, c] for c in (CLEAN, CORRUPTED)} for p in run.priors}


def write_run(out, run: E2Run, header: Mapping[str, Any]) -> None:
    """runs/<id>/: header.json, injections.csv, cases.jsonl (every prior and condition),
    metrics.json."""
    from pathlib import Path

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "header.json").write_text(json.dumps(dict(header), indent=2, default=str) + "\n")
    with (out / "injections.csv").open("w", newline="") as handle:
        handle.write(injections_text(run))
    with (out / "cases.jsonl").open("w") as handle:
        for prior in run.priors:
            for condition in (CLEAN, CORRUPTED):
                for r in run.results[prior, condition]:
                    handle.write(json.dumps(case_record(r), default=str) + "\n")
    (out / "metrics.json").write_text(json.dumps(metrics_record(run), indent=2, default=str)
                                      + "\n")


# ---------------------------------------------------------------- report (docs/e2_agent.md)

def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def _ci(ci: Sequence[float] | None) -> str:
    return "" if not ci else f" [{_pct(ci[0])}, {_pct(ci[1])}]"


def _rate(block: Mapping[str, Any]) -> str:
    return f"{block['k']}/{block['n']} = {_pct(block['rate'])}{_ci(block['ci95'])}"


def _table(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return lines


def _p(p: float) -> str:
    return f"{p:.3g}"


def _condition_rows(m: Mapping[str, Any]) -> list[list[Any]]:
    mc = m["mcnemar"]
    vo = m["value_outcomes"]

    def flagged(status: str) -> str:
        b = vo.get(status, {})
        return (f"{sum(b.values())} (right before {b.get('right_before', 0)}, wrong before "
                f"{b.get('wrong_before', 0)}, unscored {b.get('unscored', 0)})")

    return [
        ["company-years", m["cases"]],
        ["scored fields", m["fields"]],
        ["scored fields entering a computed check", _pct(m["fields_in_a_check"])],
        ["field accuracy, before agent", _pct(m["field_accuracy_before"]) + _ci(m["ci95_before"])],
        ["field accuracy, after agent", _pct(m["field_accuracy_after"]) + _ci(m["ci95_after"])],
        ["McNemar (before-only right / after-only right, p)",
         f"{mc['before_only_correct']} / {mc['after_only_correct']}, p = {_p(mc['p_value'])}"],
        ["fields fixed (wrong before, right after)", m["fixed"]],
        ["correct values wrongly changed", f"{m['wrongly_changed']} of the fields right before "
         f"({_pct(m['wrongly_changed_rate'])})"],
        ["company-years with a wrongly changed value", _rate(m["wrongly_changed_cases"])],
        ["values CORRECTED", flagged("CORRECTED")],
        ["values REVIEW", flagged("REVIEW")],
        ["changed values with no answer key", m["unscored_changes"]],
        ["actions per company-year (mean / max)",
         f"{m['actions_mean']:.2f} / {m['actions_max']}"],
        ["stopped at the action limit", _rate(m["action_limit_share"])],
        ["stop reasons", ", ".join(f"{k} {v}" for k, v in m["stop_reasons"].items())],
        ["identity checks passing, before → after",
         f"{_pct(m['identity_pass_rate_before'])} → {_pct(m['identity_pass_rate_after'])}"],
        ["company-years with every check passing, before",
         _rate(m["all_checks_pass_before"])],
        ["company-years with every check passing, after", _rate(m["all_checks_pass_after"])],
    ]


INJECTION_COLUMNS = ("visible", "detectable", "detected", "flagged", "corrected",
                     "wrongly_changed_cases")


def _injection_rows(m: Mapping[str, Any]) -> list[list[Any]]:
    blocks = [("all", m["injected"])]
    blocks += [(f"type: {k}", b) for k, b in m["by_type"].items()]
    blocks += [(k.replace("_", " "), b) for k, b in m["by_detectability"].items() if b["cases"]]
    return [[name, b["cases"], *(_rate(b[c]) for c in INJECTION_COLUMNS)] for name, b in blocks]


def _money(value: Any) -> str:
    if value is None:
        return "—"
    v = Decimal(str(value))
    return f"{v / Decimal(1_000_000):,.0f}M" if abs(v) >= 1_000_000 else f"{v:,f}"


def _case_rows(results: Sequence[CaseResult]) -> list[list[Any]]:
    rows = []
    for r in results:
        i = r.injection
        others = [f"{item} {status}" for item, status in r.values if item != i.item]
        level = "located" if r.detectable else "visible" if r.visible else "invisible"
        rows.append([f"{r.issuer} FY{r.fiscal_year}",
                     i.kind + ("" if i.kind == i.assigned else f" (assigned {i.assigned})"),
                     i.item, f"{_money(i.clean_value)} → {_money(i.injected_value)}",
                     level, dict(r.values).get(i.item, "—"),
                     "yes" if r.corrected else "no", r.actions_used, r.stop_reason,
                     "; ".join(others) or "—",
                     ", ".join(r.wrongly_changed) or "—"])
    return rows


def _clean_rows(results: Sequence[CaseResult]) -> list[list[Any]]:
    return [[f"{r.issuer} FY{r.fiscal_year}", r.actions_used, r.stop_reason,
             "; ".join(f"{item} {status}" for item, status in r.values),
             ", ".join(r.fixed) or "—", ", ".join(r.wrongly_changed) or "—"]
            for r in results if r.values or r.actions_used]


@dataclass(frozen=True)
class PreviousRun:
    """An earlier E2 run read back from runs/<id>/ for the side-by-side comparison."""

    header: Mapping[str, Any]
    metrics: Mapping[str, Mapping[str, Any]]  # {prior: {condition: metrics}}
    cases: tuple[Mapping[str, Any], ...]  # case records at the earlier run's first prior
    injections: str  # injections.csv text


def load_previous(run_dir) -> PreviousRun:
    from pathlib import Path

    run_dir = Path(run_dir)
    header = json.loads((run_dir / "header.json").read_text())
    base = header["priors"][0]
    cases = tuple(r for r in (json.loads(line) for line in
                              (run_dir / "cases.jsonl").read_text().splitlines() if line)
                  if r["prior"] == base)
    return PreviousRun(header, json.loads((run_dir / "metrics.json").read_text()), cases,
                       (run_dir / "injections.csv").read_text())


def injections_text(run: E2Run) -> str:
    """injections.csv as write_run writes it (to compare two runs' injections)."""
    import csv
    import io

    names = ["issuer", "fiscal_year", "kind", "assigned", "item", "row_id", "factor", "negate",
             "true_value", "clean_value", "injected_value"]
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(handle, names, restval="")
    writer.writeheader()
    for (issuer, year), i in sorted(run.injections.items()):
        writer.writerow(injection_record(i) if i else
                        {"issuer": issuer, "fiscal_year": year, "kind": "NONE"})
    return handle.getvalue()


def _compare_rows(a: Mapping[str, Any], b: Mapping[str, Any]) -> list[list[Any]]:
    """Key metrics of one condition in two runs (a earlier, b this run)."""
    def flagged(m: Mapping[str, Any], status: str, bucket: str) -> int:
        return m["value_outcomes"].get(status, {}).get(bucket, 0)

    def mc(m: Mapping[str, Any]) -> str:
        x = m["mcnemar"]
        return f"{x['before_only_correct']} / {x['after_only_correct']}, p = {_p(x['p_value'])}"

    rows = [
        ["field accuracy before → after", *(f"{_pct(m['field_accuracy_before'])} → "
                                            f"{_pct(m['field_accuracy_after'])}" for m in (a, b))],
        ["McNemar (before-only / after-only right, p)", mc(a), mc(b)],
        ["fields fixed", a["fixed"], b["fixed"]],
        ["correct values wrongly changed", a["wrongly_changed"], b["wrongly_changed"]],
        ["CORRECTED on a wrong reading / on a right one",
         *(f"{flagged(m, 'CORRECTED', 'wrong_before')} / {flagged(m, 'CORRECTED', 'right_before')}"
           for m in (a, b))],
        ["REVIEW on a right reading (false alarm, value kept)",
         flagged(a, "REVIEW", "right_before"), flagged(b, "REVIEW", "right_before")],
        ["values REVIEW in all", a["values"].get("REVIEW", 0), b["values"].get("REVIEW", 0)],
        ["actions per company-year (mean / max)",
         *(f"{m['actions_mean']:.2f} / {m['actions_max']}" for m in (a, b))],
        ["scored fields entering a computed check", _pct(a.get("fields_in_a_check")),
         _pct(b.get("fields_in_a_check"))],
        ["identity checks passing after", _pct(a["identity_pass_rate_after"]),
         _pct(b["identity_pass_rate_after"])],
        ["company-years with every check passing after", _rate(a["all_checks_pass_after"]),
         _rate(b["all_checks_pass_after"])],
    ]
    if "injected" in a and "injected" in b:
        for key, label in (("visible", "injected: visible"), ("detectable", "injected: located"),
                           ("detected", "injected: detected (item flagged)"),
                           ("flagged", "injected: year flagged"),
                           ("corrected", "injected: corrected")):
            rows.append([label, _rate(a["injected"][key]), _rate(b["injected"][key])])
        for kind in sorted(set(a["by_type"]) | set(b["by_type"])):
            rows.append([f"corrected, type {kind}",
                         *(_rate(m["by_type"][kind]["corrected"]) if kind in m["by_type"]
                           else "—" for m in (a, b))])
    return rows


def side_by_side(clean_a: Mapping[str, Any], clean_b: Mapping[str, Any],
                 corrupted_a: Mapping[str, Any], corrupted_b: Mapping[str, Any]
                 ) -> list[list[Any]]:
    """One row per metric: earlier and this run, clean then corrupted ("—" where a metric
    does not apply, e.g. injection rates on clean readings)."""
    clean = {r[0]: r[1:] for r in _compare_rows(clean_a, clean_b)}
    return [[label, *clean.get(label, ("—", "—")), *values]
            for label, *values in _compare_rows(corrupted_a, corrupted_b)]


def _outcome(record: Mapping[str, Any]) -> str:
    values = "; ".join(f"{i} {s}" for i, s in record["values"]) or "no flag"
    notes = []
    if record["injection"]:
        notes.append("injected item corrected" if record["corrected"] else
                     "injected item not corrected")
    if record["wrongly_changed"]:
        notes.append("wrongly changed: " + ", ".join(record["wrongly_changed"]))
    return f"{values} ({record['actions_used']} action(s)" + \
        ("; " + "; ".join(notes) if notes else "") + ")"


_OUTCOME_KEYS = ("values", "corrected", "wrongly_changed", "fixed", "actions_used")


def changed_cases(previous: Sequence[Mapping[str, Any]], results: Iterable[CaseResult]
                  ) -> list[list[Any]]:
    """Company-years whose agent outcome differs between the earlier run and this one."""
    before = {(r["condition"], r["issuer"], r["fiscal_year"]): r for r in previous}
    rows = []
    for r in results:
        now = case_record(r)
        old = before.get((r.condition, r.issuer, r.fiscal_year))
        if old is None or any(old[k] != now[k] for k in _OUTCOME_KEYS):
            rows.append([r.condition, f"{r.issuer} FY{r.fiscal_year}",
                         _outcome(old) if old else "—", _outcome(now)])
    return rows


def render_report(run: E2Run, header: Mapping[str, Any],
                  previous: PreviousRun | None = None) -> str:
    """docs/e2_agent.md: setup, the comparison with an earlier run (if given), both
    conditions, injections by type and detectability, every corrupted case, the clean
    condition's changes and the prior sensitivity."""
    base = run.priors[0]
    clean, corrupted = run.metrics[base, CLEAN], run.metrics[base, CORRUPTED]
    planned = [i for i in run.injections.values() if i]
    missing = sorted(k for k, i in run.injections.items() if i is None)
    switched = [i for i in planned if i.kind != i.assigned]
    number = iter(range(1, 20))
    provenance = [f"> **Provenance.** {header['provenance']}", ""] \
        if header.get("provenance") else []
    lines = [
        "# E2: Correlation Agent ablation (Phase 5c)",
        "",
        f"Blueprint 10.2. Run `{header['run_id']}`, generated by `python eval/e2_agent.py`. "
        f"Commit {header['git_commit']}{' with uncommitted changes' if header['git_dirty'] else ''}"
        f"; evidence manifest {header['evidence_manifest_sha256'][:12]}; document model "
        f"{header['model_version']} (dataset {header['dataset_version']}); as of "
        f"{header['as_of']}; split {header['split']} ({', '.join(header['companies'])}). "
        f"Files: `runs/{header['run_id']}/` (header.json, injections.csv, cases.jsonl, "
        f"metrics.json).",
        "",
        *provenance,
        f"## {next(number)}. Setup",
        "",
        "- **Evaluation mode.** The agent sees each company-year's rows, M1 top-3 predictions "
        "and the filing only: no tags, no tag checks (`D:<item>`), NCI by BC23 (no NCI only "
        "when no total-equity or net-income-including-NCI line is read and no IS or BS row "
        "label mentions a noncontrolling or minority interest, #110). Its loop, guardrails "
        "and settle rule are the product ones (`docs/correlation_agent_v1.md`): an action must "
        "bring a failing check within tolerance (#103); a swap needs M1 p >= "
        f"{header.get('swap_min_probability', '—')} and a row no other item reads (#109); a "
        "face-block total's component items are inputs of the checks that use it (#111).",
        "- **Answer key.** E1's: the tag channel's verified value for each face item (BC8), BC13 "
        "twins and BC14 keys. The scorer reads it after the agent has finished; the harness "
        "also reads it to choose injections. The agent never does.",
        "- **Clean.** The real M1 v3 readings of every company-year.",
        f"- **Corrupted.** One injected error per company-year through the in-memory edit hook: "
        f"types rotate over the sorted company-years ({', '.join(header['injection_types'])}); "
        f"the item is drawn at random (seed {header['seed']}) among the scored face items the "
        f"document channel read correctly (strict, no BC13), and the injection must make it "
        f"wrong. If the assigned type cannot be applied to any eligible item, the next type in "
        f"the rotation is used. {len(planned)} company-years injected"
        + (f"; {len(switched)} used another type than assigned" if switched else "")
        + (f"; no injection possible for {', '.join(f'{a} FY{b}' for a, b in missing)}"
           if missing else "") + ".",
        "  - `wrong_row`: the item read from another row of the same statement that is not one "
        "of its top-3 candidates and holds a different value (random).",
        "  - `rescale_1000`: its own row's value × 1,000.",
        "  - `flip_sign`: its own row's value with the sign reversed.",
        "  - `second_candidate`: the highest-ranked other candidate row (item in its top-3) "
        "whose value is wrong.",
        "- **Metrics.** *Visible*: the injection makes a check fail that does not fail the "
        "same way (same check, same residual) on the clean readings. *Located*: the injected "
        "item is itself an input of a failing check, so the agent can suspect it (a "
        "formula-derived value expands to its inputs, and a face-block total to the items read "
        "on its component rows, #111). "
        "*Detected*: the agent flags the injected item (CORRECTED or REVIEW). *Year flagged*: "
        "the agent flags some value of a newly failing check, so the year is not passed "
        "silently. *Corrected*: the injected item's final value is the true value. "
        "*Wrongly changed*: a scored field right before the "
        "agent and wrong after. Field accuracy is E1's (BC13 on), before = the agent's "
        "starting readings (with the injection), after = the final readings; McNemar is exact "
        "on the paired fields. Identity pass rate: PASS share of the evaluation-mode checks "
        "computed. Intervals are 95% percentile bootstraps over companies "
        f"({header['bootstrap_draws']} draws).",
        f"- **Priors.** Rescale and flip prior {base:g} [Guessing]; sensitivity at "
        f"{', '.join(f'{p:g}' for p in run.priors[1:])} (both priors set together).",
        "",
    ]
    if previous is not None:
        prev_id = previous.header["run_id"]
        prev = previous.metrics[f"{previous.header['priors'][0]:g}"]
        same = header.get("injections_identical_to_compare_run")
        lines += [
            f"## {next(number)}. {prev_id} and {header['run_id']} side by side",
            "",
            f"Same companies, filings, answer key, seed and injections"
            f"{'' if same else ' (NOTE: the injections differ)'}; prior {base:g}. "
            f"{prev_id} ran before the decisions recorded in the provenance note; "
            f"{header['run_id']} runs with them.",
            "",
            *_table(["metric", f"{prev_id} clean", f"{header['run_id']} clean",
                     f"{prev_id} corrupted", f"{header['run_id']} corrupted"],
                    side_by_side(prev[CLEAN], clean, prev[CORRUPTED], corrupted)),
            "",
            f"### Company-years whose outcome changed ({prev_id} → {header['run_id']})",
            "",
            *_table(["condition", "company-year", prev_id, header["run_id"]],
                    changed_cases(previous.cases, [*run.results[base, CLEAN],
                                                   *run.results[base, CORRUPTED]])),
            "",
        ]
    lines += [
        f"## {next(number)}. Results at prior {base:g}",
        "",
        *_table(["metric", "clean", "corrupted"],
                [[a[0], a[1], b[1]] for a, b in zip(_condition_rows(clean),
                                                    _condition_rows(corrupted))]),
        "",
        f"## {next(number)}. Injected errors: detection and correction",
        "",
        *_table(["subset", "cases", "visible", "located", "detected (item flagged)",
                 "year flagged", "corrected", "with a wrongly changed value"],
                _injection_rows(corrupted)),
        "",
        "### Every corrupted company-year",
        "",
        *_table(["company-year", "type", "item", "clean → injected", "detectability",
                 "agent's flag", "corrected", "actions", "stop", "other values flagged",
                 "wrongly changed"], _case_rows(run.results[base, CORRUPTED])),
        "",
        f"## {next(number)}. Clean condition: company-years where the agent acted or flagged",
        "",
        *_table(["company-year", "actions", "stop", "values", "fixed", "wrongly changed"],
                _clean_rows(run.results[base, CLEAN])),
        "",
        f"## {next(number)}. Prior sensitivity",
        "",
        *_table(["prior", "condition", "accuracy before → after", "McNemar p",
                 "wrongly changed", "fixed", "detected", "corrected", "actions mean"],
                [[f"{p:g}", c, f"{_pct(m['field_accuracy_before'])} → "
                  f"{_pct(m['field_accuracy_after'])}", _p(m["mcnemar"]["p_value"]),
                  m["wrongly_changed"], m["fixed"],
                  _rate(m["injected"]["detected"]) if "injected" in m else "—",
                  _rate(m["injected"]["corrected"]) if "injected" in m else "—",
                  f"{m['actions_mean']:.2f}"]
                 for p in run.priors for c in (CLEAN, CORRUPTED)
                 for m in [run.metrics[p, c]]]),
        "",
    ]
    differ = [p for p in run.priors[1:]
              if any(case_record(a) != {**case_record(b), "prior": a.prior}
                     for c in (CLEAN, CORRUPTED)
                     for a, b in zip(run.results[base, c], run.results[p, c]))]
    lines.append("Every case result is identical at every prior (apart from the prior itself)."
                 if not differ else "Case results differ from the configured prior at prior "
                 + ", ".join(f"{p:g}" for p in differ) + " (cases.jsonl).")
    return "\n".join(lines) + "\n"
