"""Dual-channel values of the validated statement set, product mode (blueprint 8.7, 8.9).

One Observation per (filing, canonical item) from the document reading (M1 rows) and the tag
reading, combined by finagent.verify.verify_item:

| 8.7 result                | value used in sums            | accepted                                 |
| VERIFIED (AGREE, BC15)    | the tag amount (exact figure) | yes; document value kept alongside       |
| SINGLE_CHANNEL (document) | the document value            | pending: only if every identity it       |
|                           |                               | enters passes (finagent.statements       |
|                           |                               | .identities.apply_acceptance)            |
| SINGLE_CHANNEL (tag)      | the tag amount                | yes, flagged                             |
| DISAGREE                  | none (both readings kept)     | never; never falls back to the tag value |
| GAP / CONFLICT / INVALID  | none (never 0)                | no; reason says why                      |

GAP reasons: START_UNKNOWN (no period start), STATEMENT_NOT_LOCATED (the item's statement was
not found and no tag either), NEITHER; NOT_REPORTED is assigned later, per company, to an item
the filer never reports (finagent.statements.build).
"""

from collections.abc import Collection, Mapping
from dataclasses import replace
from decimal import Decimal

from finagent.config import LineItemSpec
from finagent.document.channel import DocumentReading, primary_statement
from finagent.evidence.store import FilingRecord
from finagent.records import Observation, Outcome, RowInstance, Status
from finagent.tags.channel import START_UNKNOWN
from finagent.verify import (
    AGREE_WITH_ONE_CANDIDATE,
    tag_candidates,
    tag_value,
    verify_item,
    within_precision,
)

# Final reason codes beyond the 8.7 ones (AGREE, AGREE_WITH_ONE_CANDIDATE, DOCUMENT_ONLY,
# TAG_ONLY, VALUES_DIFFER, NEITHER).
STATEMENT_NOT_LOCATED = "STATEMENT_NOT_LOCATED"
NOT_REPORTED = "NOT_REPORTED"
IDENTITY_FAILED = "IDENTITY_FAILED"
NO_IDENTITY_CHECK = "NO_IDENTITY_CHECK"
COMPARATIVE_DOCUMENT_ONLY = "COMPARATIVE_DOCUMENT_ONLY"  # never judged by identities

# Flags.
TAG_ONLY_FLAG = "TAG_ONLY"
INLINE_ONLY_FLAG = "INLINE_ONLY"
RESTATED = "RESTATED"
FROM_LATER_FILING = "FROM_LATER_FILING"
PENDING = "PENDING_IDENTITIES"  # a document-only value awaiting the identity check

UNIT = "USD"


def base_observation(
    filing: FilingRecord, metric: str, *, period_type: str = "duration", **fields
) -> Observation:
    """An Observation carrying the filing's provenance; fields fill in the rest."""
    defaults = dict(
        issuer=filing.issuer, cik=filing.cik, fiscal_year=filing.fiscal_year, metric=metric,
        unit=UNIT, value=None, classification="EVIDENCE_GAP", status="GAP", reason="NEITHER",
        accepted=False, accession=filing.accession, filing_url=filing.filing_url,
        filed=filing.filed,
        fiscal_period_start=filing.start if period_type == "duration" else None,
        fiscal_period_end=filing.end,
    )
    defaults.update(fields)
    return Observation(**defaults)


def _tag_concepts(tag: Outcome | None) -> tuple[str, ...]:
    if tag is None:
        return ()
    return tuple(sorted({r.concept for r in tag.observations if r.concept}))


def _tag_source_flags(tag: Outcome | None) -> tuple[str, ...]:
    if tag is not None and any(r.source == "inline_only" for r in tag.observations):
        return (INLINE_ONLY_FLAG,)
    return ()


def observe_item(
    filing: FilingRecord,
    spec: LineItemSpec,
    document: DocumentReading,
    tag: Outcome | None,
    rows_by_id: Mapping[str, RowInstance],
    located: Collection[str],
) -> Observation:
    """Combine one canonical item's two readings into an Observation (product mode)."""
    outcome = verify_item(filing, document, tag)
    row = rows_by_id.get(document.row_id) if document.row_id else None
    common = dict(
        period_type=spec.period_type, verification=outcome.reason,
        statement=row.statement if row else None, row_id=document.row_id,
        row_label=row.label if row else None, concepts=_tag_concepts(tag),
        document_value=document.value, scale=document.scale, detail=outcome.detail,
    )
    status: Status = outcome.status
    if status == "VERIFIED":
        amount = _verified_amount(outcome, tag)
        others = tuple(a for a in tag_candidates(tag) if a != amount)
        return base_observation(
            filing, spec.item_id, value=amount, tag_value=amount, classification="REPORTED_FACT",
            status=status, reason=outcome.reason, accepted=True, channels=("document", "tag"),
            candidates=others, flags=_tag_source_flags(tag), **common)
    if status == "DISAGREE":
        return base_observation(
            filing, spec.item_id, tag_value=tag_value(tag), status=status, reason=outcome.reason,
            channels=("document", "tag"), **common)
    if status == "SINGLE_CHANNEL" and document.value is not None:
        return base_observation(
            filing, spec.item_id, value=document.value, classification="REPORTED_FACT",
            status=status, reason=outcome.reason, channels=("document",), flags=(PENDING,),
            candidates=tuple(tag_candidates(tag)), **common)
    if status == "SINGLE_CHANNEL":
        amount = tag_value(tag)
        return base_observation(
            filing, spec.item_id, value=amount, tag_value=amount, classification="REPORTED_FACT",
            status=status, reason=outcome.reason, accepted=True, channels=("tag",),
            flags=(TAG_ONLY_FLAG,) + _tag_source_flags(tag), **common)
    return _gap(filing, spec, tag, located, common)


def _verified_amount(outcome: Outcome, tag: Outcome | None) -> Decimal:
    if outcome.reason == AGREE_WITH_ONE_CANDIDATE:
        amounts = {r.value for r in outcome.observations if r.channel == "tag"}
        return amounts.pop()
    amount = tag_value(tag)
    assert amount is not None  # VERIFIED needs a usable tag value
    return amount


def _gap(
    filing: FilingRecord, spec: LineItemSpec, tag: Outcome | None, located: Collection[str],
    common: dict,
) -> Observation:
    """No usable reading in either channel: why."""
    if tag is not None and tag.status in ("CONFLICT", "INVALID"):
        values = tuple(sorted({r.value for r in tag.observations if r.value is not None}))
        return base_observation(filing, spec.item_id, status=tag.status, reason=tag.reason,
                                candidates=values, **common)
    if tag is not None and tag.reason == START_UNKNOWN:
        reason = START_UNKNOWN
    elif primary_statement(spec) not in located:
        reason = STATEMENT_NOT_LOCATED
    else:
        reason = "NEITHER"
    return base_observation(filing, spec.item_id, status="GAP", reason=reason, **common)


def observe_comparative(observation: Observation) -> Observation:
    """A prior-year column reading in a later filing: VERIFIED and tag-only values stay
    accepted; a document-only one is never judged by identities, so it is not accepted."""
    if observation.reason == "DOCUMENT_ONLY":
        return replace(observation, accepted=False, reason=COMPARATIVE_DOCUMENT_ONLY,
                       flags=tuple(f for f in observation.flags if f != PENDING))
    return observation


def is_document_only(observation: Observation) -> bool:
    return observation.channels == ("document",)


def channel_rank(observation: Observation) -> int:
    """VERIFIED 3 > SINGLE_CHANNEL tag 2 > SINGLE_CHANNEL document 1 > anything else 0."""
    if observation.status == "VERIFIED":
        return 3
    if observation.status == "SINGLE_CHANNEL":
        return 1 if is_document_only(observation) else 2
    return 0


def same_amount(a: Observation, b: Observation) -> bool:
    """Equal values; a document-only value is compared within its displayed precision."""
    if a.value is None or b.value is None:
        return False
    if a.value == b.value:
        return True
    for doc, other in ((a, b), (b, a)):
        if is_document_only(doc) and doc.scale:
            return within_precision(doc.value, doc.scale, other.value)
    return False

