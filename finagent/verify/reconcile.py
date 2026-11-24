"""Reconcile a company's statements: Correlation Agent, rebuild, final check (8.8; Phase 5).

reconcile_company runs the agent (product mode) on every filing's own fiscal year, rebuilds the
statement set with the agent's edits as in-memory overrides (8.7 runs again on the changed
readings, so a corrected value that now agrees with the tag is VERIFIED, flagged CORRECTED,
with its original reading kept), and then runs the final check of each fiscal year.

A correction is also applied to the next filing's prior-year column of the same year when that
column is read from the same row (same statement, and the same position or label: #108) and
shows the same original value (#107, decided); it is flagged CORRECTED there too, and I8 then
compares it with the corrected year. A swap goes to the row of the next filing that is the same
row as the target row and shows the corrected value; with none or several nothing propagates.

final_check is independent of the agent: it recomputes I9, I10 and I11 from the identity
library on the final statement values of the year (the values the forecast uses). BC22: I9 and
I10 must pass (FAIL -> INVALID, NOT_COMPUTABLE -> GAP) at their tolerance, exact zero unless an
operand is summed from rounded presented components (BC24: half the reporting unit per
component); I11 counts only when it is computable, and a non-zero I11 is REVIEW with its
residual and does not block.

This is the interface the Phase 6 planner's "reconcile" action for a statements goal calls.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any

from finagent.config import AgentConfig, IdentityLibrary, LineItemSpec, load_agent_config
from finagent.document.normalize import value_for_period
from finagent.evidence.store import EvidenceStore
from finagent.records import (AgentResult, AgentValue, DocumentEdit, IdentityResult,
                              Observation, RowInstance, StatementsCheck)
from finagent.statements.build import (FilingAnalysis, StatementConfigs, StatementSet,
                                       analyse_company, build_statement_set)
from finagent.statements.identities import (NOT_COMPUTABLE, PASS, REVIEW, Context,
                                            evaluate_sum)
from finagent.statements.replay import FULL, EvidenceView
from finagent.verify.correlation_agent import run_agent
from finagent.verify.edits import probability
from finagent.verify.workspace import FilingState

VERIFIED, INVALID, GAP = "VERIFIED", "INVALID", "GAP"
FAIL = "FAIL"


@dataclass(frozen=True)
class Reconciliation:
    """One company after the Correlation Agent (product mode)."""

    issuer: str
    as_of: date
    statement_set: StatementSet  # rebuilt with the agent's edits
    before: StatementSet  # the statement set without the agent (Phase 4a)
    results: tuple[AgentResult, ...]  # one per filing, own fiscal year
    edits: tuple[DocumentEdit, ...]
    checks: dict[int, StatementsCheck]  # final check per fiscal year
    propagated: tuple[DocumentEdit, ...] = ()  # corrections carried to prior-year columns


def year_scale(pool: Mapping[str, Observation]) -> int:
    """The year's reporting unit (power of ten) from its document readings."""
    scales = [o.scale for o in pool.values() if o.scale and not o.correction]
    return max(scales) if scales else 0


def final_check(s: StatementSet, fiscal_year: int, library: IdentityLibrary,
                agent: AgentConfig) -> StatementsCheck:
    """The statements goal of one fiscal year (BC22, BC24), recomputed on its final values."""
    filing = next(f for f in s.filings if f.fiscal_year == fiscal_year)
    pool = {m: o for (y, m), o in s.observations.items() if y == fiscal_year}
    ctx = Context(filing, pool, s.bases.get(fiscal_year, {}), s.nci.get(fiscal_year, {}),
                  year_scale(pool))
    specs = {spec.identity_id: spec for spec in library.identities}
    results: list[IdentityResult] = []
    for identity_id in agent.must_pass + agent.review_when_computed:
        r = evaluate_sum(specs[identity_id], ctx, library)
        if r.status != NOT_COMPUTABLE:
            assert r.residual is not None and r.tolerance is not None
            failed = FAIL if identity_id in agent.must_pass else REVIEW
            r = replace(r, status=PASS if abs(r.residual) <= r.tolerance else failed)
        results.append(r)
    exact = [r for r in results if r.identity_id in agent.must_pass]
    failed = [r for r in exact if r.status == FAIL]
    missing = [r for r in exact if r.status == NOT_COMPUTABLE]
    review = tuple(f"{r.identity_id} residual {r.residual}" for r in results
                   if r.identity_id in agent.review_when_computed and r.status == REVIEW)
    if failed:
        status = INVALID
        reason = "; ".join(f"{r.identity_id} residual {r.residual} (tolerance {r.tolerance})"
                           for r in failed)
    elif missing:
        status = GAP
        reason = "; ".join(f"{r.identity_id} not computable: missing {list(r.missing)}"
                           for r in missing)
    else:
        status = VERIFIED
        rounded = [f"{r.identity_id} residual {r.residual} within {r.tolerance} (BC24)"
                   for r in exact if r.residual != 0]
        reason = " and ".join(r.identity_id for r in exact) + (
            " pass: " + "; ".join(rounded) if rounded else " exactly zero")
    return StatementsCheck(s.issuer, fiscal_year, status, reason, tuple(results), review)


def _label(row: RowInstance) -> str:
    return " ".join(row.label.lower().split())


def same_row(a: RowInstance, b: RowInstance) -> bool:
    """The same face row in two filings: same statement, and the same position or the same
    label (filers rename lines, e.g. "Provision for" -> "Benefit (provision) for income taxes";
    the same original value is required as well, #107/#108)."""
    return a.statement == b.statement and (a.position == b.position or _label(a) == _label(b))


def propagate(edit: DocumentEdit, value: AgentValue, earlier: FilingAnalysis,
              later: FilingAnalysis, spec: LineItemSpec) -> DocumentEdit | None:
    """The correction of `edit` (filing t-1, own year) for filing t's prior-year column of that
    year, or None unless that column is read from the same row with the same original value."""
    if earlier.inputs is None or later.inputs is None or value.original_row_id is None:
        return None
    comparative = later.comparatives.get(earlier.filing.fiscal_year, {}).get(edit.item)
    if comparative is None or comparative.row_id is None or comparative.document_value is None \
            or comparative.document_value != value.original_value:
        return None
    before = {r.row_id: r for r in earlier.inputs.rows}
    after = {r.row_id: r for r in later.inputs.rows}
    original, here = before.get(value.original_row_id), after.get(comparative.row_id)
    if original is None or here is None or not same_row(original, here):
        return None
    period = earlier.filing.end
    if edit.row_id == value.original_row_id:  # rescale / flip on the same row
        target = here.row_id
    else:  # swap: the counterpart of the target row, showing the same cell value
        source = before.get(edit.row_id)
        cell = value_for_period(source, period, spec.sign) if source else None
        if cell is None or cell.value is None:
            return None
        matches = [r.row_id for r in later.inputs.rows if same_row(r, source)
                   and (v := value_for_period(r, period, spec.sign)) is not None
                   and v.value is not None and v.value == cell.value]
        if len(matches) != 1:
            return None
        target = matches[0]
    return DocumentEdit(
        later.filing.accession, period, edit.item, target, edit.factor, edit.negate,
        probability(later.inputs.predictions, target, edit.item),
        edit.actions + ("propagated",), "agent",
        f"propagated from {earlier.filing.accession} FY{earlier.filing.fiscal_year}")


def propagate_all(results: Sequence[AgentResult], analyses: Sequence[FilingAnalysis],
                  configs: StatementConfigs) -> tuple[DocumentEdit, ...]:
    """Each CORRECTED own-year edit carried to the next filing's prior-year column (#107)."""
    by_accession = {a.filing.accession: i for i, a in enumerate(analyses)}
    found = []
    for r in results:
        i = by_accession.get(r.accession)
        if i is None or i + 1 >= len(analyses):
            continue
        values = {v.item: v for v in r.values if v.status == "CORRECTED"}
        for edit in r.edits:
            value = values.get(edit.item)
            if value is None:
                continue
            carried = propagate(edit, value, analyses[i], analyses[i + 1],
                                configs.concept_map[edit.item])
            if carried is not None:
                found.append(carried)
    return tuple(found)


def run_company_agent(analyses: Sequence, configs: StatementConfigs, agent: AgentConfig,
                      as_of: date) -> list[AgentResult]:
    """The agent on each filing's own fiscal year (product mode)."""
    return [run_agent(FilingState.product(a.inputs, configs, agent), as_of)
            for a in analyses if a.inputs is not None]


def reconcile_company(
    store: EvidenceStore,
    ticker: str,
    as_of: date,
    classifier,
    universe: Mapping[str, str] | None = None,
    configs: StatementConfigs | None = None,
    agent: AgentConfig | None = None,
    view: EvidenceView = FULL,
) -> Reconciliation:
    """Statement set -> agent per filing -> rebuild with the edits -> final check per year.
    view: the evidence the build may read (E8 replays, finagent.statements.replay)."""
    configs = configs or StatementConfigs.load()
    agent = agent or load_agent_config()
    before, analyses = analyse_company(store, ticker, as_of, classifier, universe, configs,
                                       view=view)
    results = run_company_agent(analyses, configs, agent, as_of)
    edits = tuple(e for r in results for e in r.edits)
    propagated = propagate_all(results, analyses, configs)
    after = build_statement_set(store, ticker, as_of, classifier, universe, configs,
                                edits + propagated, view) if edits else before
    checks = {y: final_check(after, y, configs.identities, agent) for y in after.years()}
    return Reconciliation(after.issuer, as_of, after, before, tuple(results), edits, checks,
                          propagated)


def company_statements(store: EvidenceStore, ticker: str, as_of: date, classifier,
                       universe: Mapping[str, str] | None, configs: StatementConfigs,
                       agent: AgentConfig | None, view: EvidenceView = FULL
                       ) -> tuple[StatementSet, Reconciliation | None]:
    """The statement set the forecast uses: after the agent (agent given), or the Phase 4a
    build (agent None, e.g. --no-agent to reproduce Phase 4)."""
    if agent is None:
        return build_statement_set(store, ticker, as_of, classifier, universe, configs,
                                   view=view), None
    rec = reconcile_company(store, ticker, as_of, classifier, universe, configs, agent, view)
    return rec.statement_set, rec


# ---------------------------------------------------------------- run outputs

def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def failing(checks: Sequence) -> list[str]:
    return [f"{c.check_id} {c.residual}" for c in checks if c.status == "FAIL"]


def result_record(r: AgentResult) -> dict[str, Any]:
    """One filing's agent run for agent.json (trace included)."""
    return {
        "issuer": r.issuer, "fiscal_year": r.fiscal_year, "accession": r.accession,
        "mode": r.mode, "as_of": r.as_of.isoformat(), "stop_reason": r.stop_reason,
        "actions_used": r.actions_used, "failing_before": failing(r.checks_before),
        "failing_after": failing(r.checks_after),
        "actions": [_plain(asdict(a)) for a in r.actions],
        "values": [_plain(asdict(v)) for v in r.values],
        "edits": [_plain(asdict(e)) for e in r.edits],
        "trace": [_plain(asdict(s)) for s in r.trace],
    }


def reconciliation_record(rec: Reconciliation, split: str) -> dict[str, Any]:
    return {"issuer": rec.issuer, "split": split, "as_of": rec.as_of.isoformat(),
            "filings": [result_record(r) for r in rec.results],
            "propagated": [_plain(asdict(e)) for e in rec.propagated],
            "final_check": [check_record(c) for _, c in sorted(rec.checks.items())]}


def check_record(c: StatementsCheck) -> dict[str, Any]:
    residuals = {r.identity_id: (r.status, None if r.residual is None else str(r.residual))
                 for r in c.identities}
    return {"issuer": c.issuer, "fiscal_year": c.fiscal_year, "status": c.status,
            "reason": c.reason, "review": list(c.review), "identities": residuals}


FINAL_CHECK_HEADER = ["ticker", "split", "fiscal_year", "origin", "status", "reason",
                      "I9", "I9_residual", "I10", "I10_residual", "I11", "I11_residual",
                      "review"]


def final_check_rows(rec: Reconciliation, split: str) -> list[list[Any]]:
    origin = max(rec.checks) if rec.checks else None
    rows = []
    for year, c in sorted(rec.checks.items()):
        ids = {r.identity_id: r for r in c.identities}
        cells: list[Any] = []
        for i in ("I9", "I10", "I11"):
            r = ids.get(i)
            cells += [r.status if r else "", "" if r is None or r.residual is None
                      else str(r.residual)]
        rows.append([rec.issuer, split, year, year == origin, c.status, c.reason, *cells,
                     "; ".join(c.review)])
    return rows
