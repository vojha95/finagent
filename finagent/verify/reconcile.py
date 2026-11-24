"""Reconcile a company's statements: Correlation Agent, rebuild, final check (8.8; Phase 5).

reconcile_company runs the agent (product mode) on every filing's own fiscal year, rebuilds the
statement set with the agent's edits as in-memory overrides (8.7 runs again on the changed
readings, so a corrected value that now agrees with the tag is VERIFIED, flagged CORRECTED,
with its original reading kept), and then runs the final check of each fiscal year.

final_check is independent of the agent: it recomputes I9, I10 and I11 from the identity
library on the final statement values of the year (the values the forecast uses). BC22: I9 and
I10 must be exactly zero (FAIL -> INVALID, NOT_COMPUTABLE -> GAP); I11 counts only when it is
computable, and a non-zero I11 is REVIEW with its residual and does not block.

This is the interface the Phase 6 planner's "reconcile" action for a statements goal calls.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any

from finagent.config import AgentConfig, IdentityLibrary, load_agent_config
from finagent.evidence.store import EvidenceStore
from finagent.records import AgentResult, DocumentEdit, IdentityResult, StatementsCheck
from finagent.statements.build import (StatementConfigs, StatementSet, analyse_company,
                                       build_statement_set)
from finagent.statements.identities import (NOT_COMPUTABLE, PASS, REVIEW, Context,
                                            evaluate_sum)
from finagent.verify.correlation_agent import run_agent
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


def final_check(s: StatementSet, fiscal_year: int, library: IdentityLibrary,
                agent: AgentConfig) -> StatementsCheck:
    """The statements goal of one fiscal year (BC22), recomputed on its final values."""
    filing = next(f for f in s.filings if f.fiscal_year == fiscal_year)
    pool = {m: o for (y, m), o in s.observations.items() if y == fiscal_year}
    ctx = Context(filing, pool, s.bases.get(fiscal_year, {}), s.nci.get(fiscal_year, {}), 0)
    specs = {spec.identity_id: spec for spec in library.identities}
    results: list[IdentityResult] = []
    for identity_id in agent.exact_zero + agent.review_when_computed:
        r = evaluate_sum(specs[identity_id], ctx, library)
        if r.status != NOT_COMPUTABLE:
            assert r.residual is not None
            failed = FAIL if identity_id in agent.exact_zero else REVIEW
            r = replace(r, status=PASS if r.residual == 0 else failed, tolerance=Decimal(0))
        results.append(r)
    exact = [r for r in results if r.identity_id in agent.exact_zero]
    failed = [r for r in exact if r.status == FAIL]
    missing = [r for r in exact if r.status == NOT_COMPUTABLE]
    review = tuple(f"{r.identity_id} residual {r.residual}" for r in results
                   if r.identity_id in agent.review_when_computed and r.status == REVIEW)
    if failed:
        status = INVALID
        reason = "; ".join(f"{r.identity_id} residual {r.residual} (must be exactly zero)"
                           for r in failed)
    elif missing:
        status = GAP
        reason = "; ".join(f"{r.identity_id} not computable: missing {list(r.missing)}"
                           for r in missing)
    else:
        status = VERIFIED
        reason = " and ".join(r.identity_id for r in exact) + " exactly zero"
    return StatementsCheck(s.issuer, fiscal_year, status, reason, tuple(results), review)


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
) -> Reconciliation:
    """Statement set -> agent per filing -> rebuild with the edits -> final check per year."""
    configs = configs or StatementConfigs.load()
    agent = agent or load_agent_config()
    before, analyses = analyse_company(store, ticker, as_of, classifier, universe, configs)
    results = run_company_agent(analyses, configs, agent, as_of)
    edits = tuple(e for r in results for e in r.edits)
    after = build_statement_set(store, ticker, as_of, classifier, universe, configs, edits) \
        if edits else before
    checks = {y: final_check(after, y, configs.identities, agent) for y in after.years()}
    return Reconciliation(after.issuer, as_of, after, before, tuple(results), edits, checks)


def company_statements(store: EvidenceStore, ticker: str, as_of: date, classifier,
                       universe: Mapping[str, str] | None, configs: StatementConfigs,
                       agent: AgentConfig | None) -> tuple[StatementSet, Reconciliation | None]:
    """The statement set the forecast uses: after the agent (agent given), or the Phase 4a
    build (agent None, e.g. --no-agent to reproduce Phase 4)."""
    if agent is None:
        return build_statement_set(store, ticker, as_of, classifier, universe, configs), None
    rec = reconcile_company(store, ticker, as_of, classifier, universe, configs, agent)
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
