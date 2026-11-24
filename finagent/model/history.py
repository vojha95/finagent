"""Point-in-time access to the validated statement set for the forecast (blueprint 8.10, 6).

Only accepted values are used. A value from a filing filed after as_of raises (rule 6): the
statement set is built as of a date, and the forecast refuses a set or a value later than its
own as_of. Every other outcome is a typed lookup result: the value, NOT_REPORTED (the filer never
reports the item; the driver it feeds does not apply), or the gap reason (DISAGREE,
IDENTITY_FAILED, INPUT_GAP, NEITHER, ...), never 0.
"""

from dataclasses import dataclass
from datetime import date

from finagent.records import InputRef, Observation
from finagent.statements.build import StatementSet
from finagent.statements.derive import usable
from finagent.statements.verify_set import NOT_REPORTED

MISSING = "NEITHER"  # no observation at all for the metric and year


class PointInTimeError(ValueError):
    """A value or statement set dated after the forecast's as_of."""


@dataclass(frozen=True)
class Lookup:
    metric: str
    fiscal_year: int
    ref: InputRef | None  # the accepted value, else None
    reason: str | None  # None when accepted; NOT_REPORTED or a gap reason otherwise

    @property
    def found(self) -> bool:
        return self.ref is not None

    @property
    def not_reported(self) -> bool:
        return self.reason == NOT_REPORTED

    @property
    def value(self) -> float:
        """The accepted value as a float (model calculations only, rule 5)."""
        if self.ref is None:
            raise ValueError(f"{self.metric} FY{self.fiscal_year}: no accepted value "
                             f"({self.reason})")
        return float(self.ref.value)


def check_point_in_time(statement_set: StatementSet, as_of: date) -> None:
    """Raise unless the set and every filing it rests on are dated on or before as_of."""
    if statement_set.as_of > as_of:
        raise PointInTimeError(f"{statement_set.issuer}: statement set as of "
                               f"{statement_set.as_of} is after {as_of}")
    late = [f.accession for f in statement_set.filings if f.filed > as_of]
    if late:
        raise PointInTimeError(f"{statement_set.issuer}: filings after {as_of}: {late}")


def to_ref(observation: Observation, as_of: date) -> InputRef:
    if observation.filed > as_of:
        raise PointInTimeError(f"{observation.issuer} FY{observation.fiscal_year} "
                               f"{observation.metric}: filed {observation.filed} after {as_of}")
    assert observation.value is not None
    return InputRef(observation.metric, observation.fiscal_year, observation.value,
                    observation.status, observation.reason, observation.accession,
                    observation.filed, observation.flags)


def lookup(statement_set: StatementSet, fiscal_year: int, metric: str, as_of: date) -> Lookup:
    """The accepted value of metric for the year, or why there is none."""
    observation = statement_set.get(fiscal_year, metric)
    if observation is None:
        return Lookup(metric, fiscal_year, None, MISSING)
    if usable(observation, provisional=False):
        return Lookup(metric, fiscal_year, to_ref(observation, as_of), None)
    reason = "DISAGREE" if observation.status == "DISAGREE" else observation.reason
    return Lookup(metric, fiscal_year, None, reason)


def lookup_basis(statement_set: StatementSet, fiscal_year: int, basis: str,
                 as_of: date) -> Lookup:
    """A basis (e.g. ebit_basis) resolved to the metric chosen for the year."""
    chosen = statement_set.bases.get(fiscal_year, {}).get(basis)
    if chosen is None:
        return Lookup(basis, fiscal_year, None, "NO_BASIS")
    return lookup(statement_set, fiscal_year, chosen, as_of)
