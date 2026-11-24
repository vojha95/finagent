"""Coverage of the forecast's inputs by the validated statement set (configs/forecast_inputs.yaml).

For each need and each fiscal year it needs (counting back from the latest), one slot: PRESENT
when the statement set holds an accepted value, otherwise the reason. DISAGREE and NOT_REPORTED
are counted separately from the other gaps: a DISAGREE value has no accepted value until the
Correlation Agent (Phase 5); NOT_REPORTED is an item the filer never reports (never 0). A year
the evidence store does not return is INSUFFICIENT_HISTORY.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from finagent.config import ForecastNeed
from finagent.statements.build import StatementSet
from finagent.statements.derive import usable
from finagent.statements.verify_set import NOT_REPORTED

PRESENT = "PRESENT"
DISAGREE = "DISAGREE"
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


@dataclass(frozen=True)
class Slot:
    issuer: str
    need_id: str
    tier: str
    fiscal_year: int
    metric: str  # the metric behind a basis, else the need id
    outcome: str  # PRESENT, DISAGREE, NOT_REPORTED, INSUFFICIENT_HISTORY or another gap reason
    status: str | None
    flags: tuple[str, ...] = ()


def slots(statement_set: StatementSet, needs: Sequence[ForecastNeed]) -> list[Slot]:
    years = statement_set.years()
    latest = max(years)
    found = []
    for need in needs:
        for offset in range(need.years):
            year = latest - offset
            if year not in years:
                found.append(Slot(statement_set.issuer, need.need_id, need.tier, year,
                                  need.need_id, INSUFFICIENT_HISTORY, None))
                continue
            metric = statement_set.bases.get(year, {}).get(need.need_id) or need.need_id
            observation = statement_set.get(year, metric)
            if observation is None:
                outcome, status, flags = "NEITHER", None, ()
            else:
                status, flags = observation.status, observation.flags
                if usable(observation, provisional=False):
                    outcome = PRESENT
                elif observation.status == DISAGREE:
                    outcome = DISAGREE
                else:
                    outcome = observation.reason
            found.append(Slot(statement_set.issuer, need.need_id, need.tier, year,
                              metric, outcome, status, flags))
    return found


@dataclass(frozen=True)
class Summary:
    issuer: str
    tier: str
    slots: int
    present: int
    disagree: int
    not_reported: int
    other_gaps: dict[str, int]

    @property
    def share_present(self) -> float:
        return self.present / self.slots if self.slots else 0.0

    @property
    def share_present_where_reported(self) -> float:
        """Present over slots the filer reports at all (NOT_REPORTED left out)."""
        denominator = self.slots - self.not_reported
        return self.present / denominator if denominator else 0.0


def summarise(found: Iterable[Slot], tier: str) -> Summary:
    chosen = [s for s in found if s.tier == tier]
    counts = Counter(s.outcome for s in chosen)
    other = {k: v for k, v in sorted(counts.items())
             if k not in (PRESENT, DISAGREE, NOT_REPORTED)}
    issuers = {s.issuer for s in chosen}
    return Summary(",".join(sorted(issuers)), tier, len(chosen), counts[PRESENT],
                   counts[DISAGREE], counts[NOT_REPORTED], other)
