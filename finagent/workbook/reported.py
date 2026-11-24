"""Helpers shared by the statement sheets: GAP reasons, derivation terms and I9 / I10 access."""

import re
from decimal import Decimal

from finagent.records import IdentityResult, Observation
from finagent.statements.build import StatementSet

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


def identity(s: StatementSet, year: int, identity_id: str) -> IdentityResult | None:
    found = [r for r in s.identities if r.fiscal_year == year and r.identity_id == identity_id
             and r.subject is None]
    return found[0] if found else None


def equity_terms(s: StatementSet, year: int) -> list[tuple[str, Decimal]]:
    """The equity operands of I9 (every operand except total assets and total liabilities)."""
    r = identity(s, year, "I9")
    if r is None:
        return []
    return [(t, v) for t, v in r.operands if t not in ("+total_assets", "-total_liabilities")]
