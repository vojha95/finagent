"""Tag channel: the second reading of each line item, from the filer's tags (blueprint 8.6).

Company Facts is primary; inline XBRL corroborates it, or alone supplies a single-source
value when Company Facts lacks one. Both are views of the same filer tags, so agreement
here is a consistency check, not independent verification (8.6 naming note).
"""

from datetime import date
from typing import Any

from finagent.config import LineItemSpec, core_items, load_concept_map
from finagent.evidence.integrity import read_snapshot, read_snapshot_json
from finagent.evidence.store import EvidenceStore, FilingRecord
from finagent.ixbrl.tagstore import TagFact, build_tag_store
from finagent.records import Outcome, Reading, Status, TagSource
from finagent.tags.companyfacts import Period, TagCandidate, period_bounds, primary_candidates
from finagent.tags.inline import (
    Dimensions,
    consistent_at_precision,
    has_nil_fact,
    inline_candidates,
    valued_facts,
)

# Reason codes, stable so evaluation can count them separately.
CORROBORATED = "CORROBORATED"
MULTIPLE_VALUES_PRIMARY = "MULTIPLE_VALUES_PRIMARY"
MULTIPLE_VALUES_INLINE = "MULTIPLE_VALUES_INLINE"
AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
NOT_FOUND_INLINE = "NOT_FOUND_INLINE"
INLINE_ONLY = "INLINE_ONLY"
SIGN_VIOLATION = "SIGN_VIOLATION"
NIL = "NIL"
NOT_FOUND = "NOT_FOUND"
START_UNKNOWN = "START_UNKNOWN"


def _readings(
    filing: FilingRecord,
    spec: LineItemSpec,
    candidates: list[TagCandidate],
    status: Status,
    source: TagSource,
    period: Period | None = None,
) -> tuple[Reading, ...]:
    start, end = period_bounds(filing, spec.period_type, period)
    return tuple(
        Reading(
            issuer=filing.issuer,
            fiscal_year=filing.fiscal_year,
            item=spec.item_id,
            value=c.amount,
            channel="tag",
            status=status,
            concept=c.concept,
            unit="USD",
            start=start,
            end=end,
            accession=filing.accession,
            source=source,
            single_source=source == "inline_only",
        )
        for c in candidates
    )


def _sign_ok(spec: LineItemSpec, candidate: TagCandidate) -> bool:
    return spec.sign == "either" or candidate.amount >= 0


def _sign_violation(
    filing: FilingRecord, spec: LineItemSpec, candidates: list[TagCandidate], source: TagSource,
    period: Period | None = None,
) -> Outcome:
    return Outcome(
        status="INVALID",
        reason=SIGN_VIOLATION,
        observations=_readings(filing, spec, candidates, "INVALID", source, period),
        detail=f"{spec.item_id} must be non-negative; tagged {candidates[0].amount}",
    )


def corroborate(
    filing: FilingRecord, spec: LineItemSpec, primary: list[TagCandidate], facts: list[TagFact],
    period: Period | None = None,
) -> Outcome:
    """Every concept behind the single primary value must carry that amount inline.

    Consistent-duplicates rule: at least one inline fact has the exact amount, and every other
    fact for the concept and period agrees once the amount is rounded to that fact's decimals.
    A fact that disagrees even at its own precision is AMOUNT_MISMATCH; inline facts that are
    all consistent but none exact give NOT_FOUND_INLINE.
    """
    amount = primary[0].amount
    missing: list[str] = []
    mismatched: list[TagCandidate] = []
    for candidate in primary:
        duplicates = valued_facts(facts, candidate.concept, filing, spec.period_type, period)
        disagreeing = sorted(
            {f.amount for f in duplicates if not consistent_at_precision(amount, f)}
        )
        mismatched.extend(TagCandidate(candidate.concept, a) for a in disagreeing)
        if not any(f.amount == amount for f in duplicates):
            missing.append(candidate.concept)
    if mismatched:
        return Outcome(
            status="CONFLICT",
            reason=AMOUNT_MISMATCH,
            observations=_readings(filing, spec, primary, "CONFLICT", "companyfacts", period)
            + _readings(filing, spec, mismatched, "CONFLICT", "inline", period),
            detail=f"Company Facts {amount}; inline {[str(m.amount) for m in mismatched]}",
        )
    if missing:
        return Outcome(
            status="CONFLICT",
            reason=NOT_FOUND_INLINE,
            observations=_readings(filing, spec, primary, "CONFLICT", "companyfacts", period),
            detail=f"No exact non-dimensional inline fact for {missing}",
        )
    return Outcome(
        status="VERIFIED",
        reason=CORROBORATED,
        observations=_readings(filing, spec, primary, "VERIFIED", "companyfacts", period),
        detail="Company Facts and inline XBRL agree (consistency check of the filer's tags)",
    )


def fallback(
    filing: FilingRecord, spec: LineItemSpec, facts: list[TagFact],
    period: Period | None = None, dimensions: Dimensions | None = None,
) -> Outcome:
    """Inline XBRL alone, when Company Facts has no value. Never VERIFIED: one source only."""
    candidates = inline_candidates(facts, filing, spec, period, dimensions)
    distinct = {c.amount for c in candidates}
    if len(distinct) > 1:
        return Outcome(
            status="CONFLICT",
            reason=MULTIPLE_VALUES_INLINE,
            observations=_readings(filing, spec, candidates, "CONFLICT", "inline_only", period),
            detail=f"Inline candidates disagree: {sorted(str(a) for a in distinct)}",
        )
    if candidates:
        if not _sign_ok(spec, candidates[0]):
            return _sign_violation(filing, spec, candidates, "inline_only", period)
        return Outcome(
            status="MISSING_PRIMARY",
            reason=INLINE_ONLY,
            observations=_readings(filing, spec, candidates, "MISSING_PRIMARY", "inline_only",
                                   period),
            detail="Company Facts has no value; single-source inline reading",
        )
    if has_nil_fact(facts, filing, spec, period, dimensions):
        return Outcome(status="GAP", reason=NIL, detail="Tagged only as xsi:nil")
    return Outcome(status="GAP", reason=NOT_FOUND, detail="No candidate concept in either view")


def read_item(
    filing: FilingRecord,
    spec: LineItemSpec,
    companyfacts: dict[str, Any],
    facts: list[TagFact],
    period: Period | None = None,
    dimensions: Dimensions | None = None,
) -> Outcome:
    """Primary lookup, then corroboration or fallback, for one line item.

    period overrides the filing's own period (default unchanged). A dimensional lookup
    (dimensions given) reads inline XBRL only, because Company Facts holds non-dimensional
    facts only: the result is at best a flagged single-source reading (MISSING_PRIMARY).
    """
    start, _ = period_bounds(filing, spec.period_type, period)
    if spec.period_type == "duration" and start is None:
        return Outcome(status="GAP", reason=START_UNKNOWN, detail="Period start is an evidence gap")
    if dimensions:
        return fallback(filing, spec, facts, period, dimensions)
    primary = primary_candidates(companyfacts, filing, spec, period)
    distinct = {c.amount for c in primary}
    if len(distinct) > 1:
        return Outcome(
            status="CONFLICT",
            reason=MULTIPLE_VALUES_PRIMARY,
            observations=_readings(filing, spec, primary, "CONFLICT", "companyfacts", period),
            detail=f"Company Facts candidates disagree: {sorted(str(a) for a in distinct)}",
        )
    if not primary:
        return fallback(filing, spec, facts, period)
    if not _sign_ok(spec, primary[0]):
        return _sign_violation(filing, spec, primary, "companyfacts", period)
    return corroborate(filing, spec, primary, facts, period)


def read_items(
    filing: FilingRecord,
    specs: list[LineItemSpec],
    companyfacts: dict[str, Any],
    facts: list[TagFact],
    as_of: date,
) -> dict[str, Outcome]:
    """One Outcome per item. Refuses a filing filed after as_of."""
    if filing.filed > as_of:
        raise ValueError(f"Filing {filing.accession} was filed {filing.filed}, after {as_of}")
    return {spec.item_id: read_item(filing, spec, companyfacts, facts) for spec in specs}


def read_tag_channel(
    store: EvidenceStore,
    ticker: str,
    fiscal_year: int,
    as_of: date,
    items: list[str] | None = None,
    concept_map: dict[str, LineItemSpec] | None = None,
) -> dict[str, Outcome]:
    """Tag-channel Outcomes for one issuer and fiscal year, using only evidence filed by as_of.

    items defaults to the core items of the concept map.
    """
    specs_by_id = concept_map if concept_map is not None else load_concept_map()
    specs = core_items(specs_by_id) if items is None else [specs_by_id[i] for i in items]
    filings = {f.fiscal_year: f for f in store.load_filings(ticker, as_of)}
    if fiscal_year not in filings:
        raise LookupError(f"{ticker}: no pinned 10-K for FY{fiscal_year} visible as of {as_of}")
    filing = filings[fiscal_year]
    companyfacts = read_snapshot_json(
        store.evidence_dir, f"companyfacts_{filing.cik}.json", store.manifest
    )
    document = read_snapshot(store.evidence_dir, filing.primary_document, store.manifest)
    facts = build_tag_store(document, filing.accession)
    return read_items(filing, specs, companyfacts, facts, as_of)
