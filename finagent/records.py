"""Shared records and the outcome vocabulary (blueprint sections 6.1 and 13)."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

# Blueprint 6.1 outcome statuses.
Status = Literal[
    "VERIFIED",
    "CORRECTED",
    "REVIEW",
    "MISSING_PRIMARY",
    "CONFLICT",
    "INVALID",
    "GAP",
    "EVIDENCE_GAP",
    "SKIPPED",
    "CLARIFICATION",
    "COMPLETE",
    "PARTIAL",
]

Channel = Literal["document", "tag"]

# Where a tag-channel reading came from. Both views are the filer's own tags (8.6 naming note).
TagSource = Literal["companyfacts", "inline", "inline_only"]


@dataclass(frozen=True)
class Reading:
    """One reading of a line item for one filing (blueprint 13, plus provenance fields)."""

    issuer: str
    fiscal_year: int
    item: str
    value: Decimal | None  # None means absent; never 0 for a missing value
    channel: Channel
    status: Status
    concept: str | None = None
    row_id: str | None = None
    unit: str | None = None
    start: date | None = None
    end: date | None = None
    accession: str | None = None
    source: TagSource | None = None
    single_source: bool = False


@dataclass(frozen=True)
class Outcome:
    """Result of a tool step (blueprint 13). reason is a stable code; detail is free text."""

    status: Status
    reason: str
    observations: tuple[Reading, ...] = field(default_factory=tuple)
    detail: str = ""

    @property
    def value(self) -> Decimal | None:
        """The single value carried by the outcome, or None when there is not exactly one."""
        values = {r.value for r in self.observations if r.value is not None}
        return values.pop() if len(values) == 1 else None
