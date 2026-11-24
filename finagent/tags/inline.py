"""Inline XBRL lookups: corroboration and fallback views of the tags (blueprint 8.6 steps 2-3)."""

from collections.abc import Iterable, Mapping
from decimal import Decimal

from finagent.config import LineItemSpec
from finagent.evidence.store import FilingRecord
from finagent.ixbrl.tagstore import TagFact
from finagent.precision import rounds_to
from finagent.tags.companyfacts import USD, Period, TagCandidate, period_bounds


Dimensions = Mapping[str, str]  # axis -> member, e.g. StatementEquityComponentsAxis


def fact_matches(
    fact: TagFact, concept: str, filing: FilingRecord, period_type: str,
    period: Period | None = None, dimensions: Dimensions | None = None,
) -> bool:
    """Same concept, same filing, USD, the context's dimensions, and the period.

    Defaults: the filing's own period and a non-dimensional context. With dimensions, the
    context must carry exactly those axis -> member pairs.
    """
    if fact.concept != concept or fact.accession != filing.accession:
        return False
    if fact.unit != USD:
        return False
    if dict(fact.dimensions) != dict(dimensions or {}):
        return False
    start, end = period_bounds(filing, period_type, period)
    if fact.end != end:
        return False
    if period_type == "instant":
        return fact.start is None
    return start is not None and fact.start == start


def matching_facts(
    facts: Iterable[TagFact], concept: str, filing: FilingRecord, period_type: str,
    period: Period | None = None, dimensions: Dimensions | None = None,
) -> list[TagFact]:
    return [f for f in facts if fact_matches(f, concept, filing, period_type, period, dimensions)]


def valued_facts(
    facts: Iterable[TagFact], concept: str, filing: FilingRecord, period_type: str,
    period: Period | None = None, dimensions: Dimensions | None = None,
) -> list[TagFact]:
    """Matching facts that carry an amount (xsi:nil facts excluded)."""
    return [f for f in matching_facts(facts, concept, filing, period_type, period, dimensions)
            if f.amount is not None]


def precision(fact: TagFact) -> int | None:
    """The fact's decimals as an integer; None means exact (INF or not stated)."""
    if fact.decimals is None or fact.decimals.upper() == "INF":
        return None
    return int(fact.decimals)


def consistent_at_precision(amount: Decimal, fact: TagFact) -> bool:
    """XBRL consistent-duplicates test: amount, rounded to the fact's own decimals, equals it.

    Ties may round half up or half even (finagent.precision). An equal fact is always
    consistent; an exact fact (decimals INF) must be equal.
    """
    if amount == fact.amount:
        return True
    decimals = precision(fact)
    if decimals is None:
        return False
    return rounds_to(amount, fact.amount, -decimals)


def _rank(fact: TagFact) -> float:
    decimals = precision(fact)
    return float("inf") if decimals is None else float(decimals)


def resolve_duplicates(duplicates: list[TagFact]) -> set[Decimal]:
    """Amounts for one concept and context after applying the consistent-duplicates rule.

    The most precise facts give the value; less precise facts must agree with it at their own
    decimals. Returns one amount when consistent, otherwise every distinct amount (a conflict).
    """
    if not duplicates:
        return set()
    best = max(_rank(f) for f in duplicates)
    top = {f.amount for f in duplicates if _rank(f) == best and f.amount is not None}
    if len(top) == 1:
        (value,) = top
        if all(consistent_at_precision(value, f) for f in duplicates):
            return {value}
    return {f.amount for f in duplicates if f.amount is not None}


def inline_candidates(
    facts: list[TagFact], filing: FilingRecord, spec: LineItemSpec,
    period: Period | None = None, dimensions: Dimensions | None = None,
) -> list[TagCandidate]:
    """Every (concept, amount) pair among the item's candidates found inline (nil facts excluded).

    Rounded duplicates of one concept collapse to the most precise amount when consistent.
    """
    found: list[TagCandidate] = []
    for concept in spec.concepts:
        duplicates = valued_facts(facts, concept, filing, spec.period_type, period, dimensions)
        for amount in sorted(resolve_duplicates(duplicates)):
            found.append(TagCandidate(concept, amount))
    return found


def has_nil_fact(
    facts: list[TagFact], filing: FilingRecord, spec: LineItemSpec,
    period: Period | None = None, dimensions: Dimensions | None = None,
) -> bool:
    """True if a candidate concept is tagged for the period only as xsi:nil."""
    return any(
        f.is_nil
        for concept in spec.concepts
        for f in matching_facts(facts, concept, filing, spec.period_type, period, dimensions)
    )
