"""Company Facts primary lookup (blueprint 8.6 step 1)."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from finagent.config import LineItemSpec
from finagent.evidence.store import FilingRecord

USD = "USD"


@dataclass(frozen=True)
class TagCandidate:
    """One concept's amount for the filing's period, from one view of the tags."""

    concept: str
    amount: Decimal


def _observations(companyfacts: dict[str, Any], concept: str) -> list[dict[str, Any]]:
    """USD observations for a prefix:Name concept; [] when the concept or unit is absent."""
    taxonomy, _, name = concept.partition(":")
    units = companyfacts.get("facts", {}).get(taxonomy, {}).get(name, {}).get("units", {})
    return list(units.get(USD, []))


def observation_matches(
    observation: dict[str, Any], filing: FilingRecord, period_type: str
) -> bool:
    """Form, accession, period start and end, and filing date must all match the filing."""
    if observation.get("form") != filing.form:
        return False
    if observation.get("accn") != filing.accession:
        return False
    if observation.get("filed") != filing.filed.isoformat():
        return False
    if observation.get("end") != filing.end.isoformat():
        return False
    start = observation.get("start")
    if period_type == "instant":
        return start is None
    return filing.start is not None and start == filing.start.isoformat()


def _amount(value: Any) -> Decimal:
    # str() first so a JSON float is read as written, not as its binary approximation.
    return Decimal(str(value))


def primary_candidates(
    companyfacts: dict[str, Any], filing: FilingRecord, spec: LineItemSpec
) -> list[TagCandidate]:
    """Every (concept, amount) pair among the item's candidates that matches the filing.

    Repeated observations of the same concept and amount collapse to one candidate.
    """
    found: list[TagCandidate] = []
    for concept in spec.concepts:
        amounts: list[Decimal] = []
        for observation in _observations(companyfacts, concept):
            if observation_matches(observation, filing, spec.period_type):
                amount = _amount(observation["val"])
                if amount not in amounts:
                    amounts.append(amount)
        found.extend(TagCandidate(concept, amount) for amount in amounts)
    return found

