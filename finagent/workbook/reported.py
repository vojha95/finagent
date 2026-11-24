"""Helpers shared by the statement sheets: GAP reasons, derivation terms and I9 / I10 access
(identity and equity_terms live in finagent.model.ratio_inputs, #123)."""

import re

from finagent.model.ratio_inputs import equity_terms, identity
from finagent.records import Observation

__all__ = ["DETAIL_PATTERN", "derivation_terms", "equity_terms", "gap_reason", "identity"]

DETAIL_PATTERN = re.compile(r"^[+-][a-z0-9_]+( [+-][a-z0-9_]+)*$")


def derivation_terms(detail: str) -> list[tuple[str, str]]:
    """The signed metrics of a formula derivation, from its detail ("+revenue -cost_...")."""
    return [(token[0], token[1:]) for token in detail.split()]


def gap_reason(o: Observation | None, year: int) -> str:
    """One short line: why the cell has no accepted value."""
    if o is None:
        return f"FY{year}: no observation"
    text = f"FY{year}: {o.status} ({o.reason})"
    if o.status in ("VERIFIED", "SINGLE_CHANNEL") and not o.accepted:
        text += ", not accepted"
    if o.status == "DISAGREE" and (o.document_value is not None or o.tag_value is not None):
        text += f"; document {o.document_value}, tag {o.tag_value}"
    return text + "; see Verification"
