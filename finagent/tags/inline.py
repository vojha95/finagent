"""Inline XBRL lookups: corroboration and fallback views of the tags (blueprint 8.6 steps 2-3)."""

from collections.abc import Iterable
from decimal import Decimal

from finagent.config import LineItemSpec
from finagent.evidence.store import FilingRecord
from finagent.ixbrl.tagstore import TagFact
from finagent.tags.companyfacts import USD, TagCandidate


def fact_matches(fact: TagFact, concept: str, filing: FilingRecord, period_type: str) -> bool:
    """Same concept, same filing, USD, non-dimensional context, and the filing's period."""
    if fact.concept != concept or fact.accession != filing.accession:
        return False
    if fact.unit != USD or fact.dimensions:
        return False
    if fact.end != filing.end:
        return False
    if period_type == "instant":
        return fact.start is None
    return filing.start is not None and fact.start == filing.start


def matching_facts(
    facts: Iterable[TagFact], concept: str, filing: FilingRecord, period_type: str
) -> list[TagFact]:
    return [f for f in facts if fact_matches(f, concept, filing, period_type)]


def inline_amounts(
    facts: Iterable[TagFact], concept: str, filing: FilingRecord, period_type: str
) -> set[Decimal]:
    """Distinct non-nil amounts for one concept; a fact repeated in the document counts once."""
    return {
        f.amount
        for f in matching_facts(facts, concept, filing, period_type)
        if f.amount is not None
    }


def inline_candidates(
    facts: list[TagFact], filing: FilingRecord, spec: LineItemSpec
) -> list[TagCandidate]:
    """Every (concept, amount) pair among the item's candidates found inline (nil facts excluded)."""
    found: list[TagCandidate] = []
    for concept in spec.concepts:
        for amount in sorted(inline_amounts(facts, concept, filing, spec.period_type)):
            found.append(TagCandidate(concept, amount))
    return found


def has_nil_fact(facts: list[TagFact], filing: FilingRecord, spec: LineItemSpec) -> bool:
    """True if a candidate concept is tagged for the period only as xsi:nil."""
    return any(
        f.is_nil
        for concept in spec.concepts
        for f in matching_facts(facts, concept, filing, spec.period_type)
    )
