"""Dual-channel verification: the document reading against the tag reading (blueprint 8.7).

| document | tag                        | result                                                  |
| value    | same within precision      | VERIFIED (classification REPORTED_FACT)                 |
| value    | different                  | DISAGREE -> Correlation Agent (8.8)                     |
| value    | missing                    | SINGLE_CHANNEL (document); flagged                      |
| missing  | value                      | SINGLE_CHANNEL (tag); flagged; a document miss in E1    |
| missing  | missing                    | GAP                                                     |

A tag value is usable when the tag channel returned VERIFIED (both views of the tags agree) or
MISSING_PRIMARY with an inline-only value. Evaluation mode hides the tags (8.7 box), so every
document value is single-channel and only identity failures can trigger the agent.
"""

from dataclasses import replace
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from typing import Literal

from finagent.document.channel import DocumentReading
from finagent.evidence.store import FilingRecord
from finagent.records import Outcome, Reading, Status

Mode = Literal["product", "evaluation"]

AGREE = "AGREE"
VALUES_DIFFER = "VALUES_DIFFER"
DOCUMENT_ONLY = "DOCUMENT_ONLY"
TAG_ONLY = "TAG_ONLY"
NEITHER = "NEITHER"

_USABLE_TAG_STATUSES = ("VERIFIED", "MISSING_PRIMARY")


def tag_value(tag: Outcome | None) -> Decimal | None:
    if tag is None or tag.status not in _USABLE_TAG_STATUSES:
        return None
    return tag.value


def within_precision(document_value: Decimal, scale: int | None, tag: Decimal) -> bool:
    """Equal once the tag is rounded to the document's displayed precision (e.g. millions)."""
    if document_value == tag:
        return True
    if not scale:
        return False
    step = Decimal(1).scaleb(scale)
    return any(
        tag.quantize(step, rounding=rounding) == document_value.quantize(step)
        for rounding in (ROUND_HALF_UP, ROUND_HALF_EVEN)
    )


def _document_reading(
    filing: FilingRecord, document: DocumentReading, status: Status
) -> Reading:
    return Reading(
        issuer=filing.issuer, fiscal_year=filing.fiscal_year, item=document.item,
        value=document.value, channel="document", status=status, row_id=document.row_id,
        unit="USD", start=filing.start, end=filing.end, accession=filing.accession,
        single_source=status == "SINGLE_CHANNEL",
    )


def _retag(readings: tuple[Reading, ...], status: Status) -> tuple[Reading, ...]:
    return tuple(replace(r, status=status) for r in readings)


def verify_item(
    filing: FilingRecord,
    document: DocumentReading,
    tag: Outcome | None,
    mode: Mode = "product",
) -> Outcome:
    """Combine one item's document and tag readings into a dual-channel Outcome."""
    tag_amount = None if mode == "evaluation" else tag_value(tag)
    tag_readings = () if mode == "evaluation" or tag is None else tag.observations
    has_document = document.value is not None

    if has_document and tag_amount is not None:
        if within_precision(document.value, document.scale, tag_amount):
            status, reason = "VERIFIED", AGREE
            detail = "Document and tag agree within displayed precision; REPORTED_FACT"
        else:
            status, reason = "DISAGREE", VALUES_DIFFER
            detail = f"Document {document.value} vs tag {tag_amount}"
    elif has_document:
        status, reason = "SINGLE_CHANNEL", DOCUMENT_ONLY
        detail = "Document value only; accepted only if every identity it enters passes"
    elif tag_amount is not None:
        status, reason = "SINGLE_CHANNEL", TAG_ONLY
        detail = f"Tag value only; document channel: {document.reason}"
    else:
        status, reason = "GAP", NEITHER
        detail = f"Document: {document.reason}; tag: {tag.reason if tag else 'not read'}"

    observations: tuple[Reading, ...] = ()
    if has_document:
        observations += (_document_reading(filing, document, status),)
    if tag_amount is not None:
        observations += _retag(tag_readings, status)
    return Outcome(status=status, reason=reason, observations=observations, detail=detail)


def verify_filing(
    filing: FilingRecord,
    documents: dict[str, DocumentReading],
    tags: dict[str, Outcome],
    mode: Mode = "product",
) -> dict[str, Outcome]:
    """verify_item for every item the document channel read."""
    return {item: verify_item(filing, doc, tags.get(item), mode) for item, doc in documents.items()}
