"""BC7 history for the E6 backtest: earlier fiscal years from Company Facts, point in time.

At an origin d(t) fewer pinned 10-Ks may be visible than the drivers read: revenue growth reads
`growth_years` revenues, the dividend and repurchase medians `median_years`, and the effective
interest rate the year before the origin (finagent.model.drivers). BC7: those earlier fiscal
years come from Company Facts (the tag channel), filtered by filing date at or before the
origin. Pinned 10-Ks stay at up to five years, and the forecast code is unchanged: E6 hands
build_forecast the shipped statement set plus these years.

For each fiscal year before the earliest pinned filing of the set, back to the first year the
drivers read:
- the year's period comes from the 10-Ks listed in the pinned submissions as filed on or before
  as_of, consecutive with the origin's filing (EvidenceStore.annual_filings). An unpinned 10-K's
  document is never opened; only its Company Facts are read.
- Each canonical item and each non-dimensional A.2 input is read from Company Facts for that
  period, from the latest listed 10-K filed by as_of that tags it with one value. That is the
  statement set's rule for a year shown by later filings (approved answer 5). The value is
  SINGLE_CHANNEL (tag), accepted, flagged TAG_ONLY and BC7_HISTORY, and carries the source
  filing's accession and filing date.
  - Several values in a source (e.g. total revenues and net sales, both revenue candidates):
    in a pinned year the document channel picks one (BC15). With no document, the value of the
    concept that carries the set's own accepted value in its pinned years is kept, when that
    leaves one value, flagged CONCEPT_FROM_PINNED_YEARS (#340).
  - Several values in every source: CONFLICT. A negative value where the item must be
    non-negative: INVALID. Neither is accepted.
  - No value: GAP NEITHER, or NOT_REPORTED when the statement set marks the item NOT_REPORTED in
    every pinned year (answer 6). Missing is never zero.
- Substitutes (answer 7) and the derivations (derivations.yaml) then run on those values, as for
  a pinned year in final mode.

No history value is identity-checked (no document is read), so none is VERIFIED. Nothing is
read from a filing filed after as_of: the chain stops at as_of, a Company Facts fact must carry
its source filing's accession and date, and with_history refuses any observation filed later.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

from finagent.config import ForecastConfig, LineItemSpec
from finagent.evidence.store import FilingRecord, build_filing_url, period_length_ok
from finagent.model.history import PointInTimeError
from finagent.records import Observation
from finagent.statements.build import StatementConfigs, StatementSet, substitute
from finagent.statements.derive import derive_all
from finagent.statements.model_inputs import input_period
from finagent.statements.verify_set import NOT_REPORTED, TAG_ONLY_FLAG, base_observation
from finagent.tags.channel import START_UNKNOWN, SIGN_VIOLATION
from finagent.tags.companyfacts import Period, primary_candidates

BC7_HISTORY = "BC7_HISTORY"
CONCEPT_FROM_PINNED_YEARS = "CONCEPT_FROM_PINNED_YEARS"  # several values; the set's concept kept
DIMENSIONAL_INLINE_ONLY = "DIMENSIONAL_INLINE_ONLY"  # Company Facts holds no dimensional facts
NO_LISTED_FILING = "NO_LISTED_FILING"  # the year has no consecutive 10-K listed by as_of
MULTIPLE_VALUES = "MULTIPLE_VALUES_PRIMARY"


def history_depth(config: ForecastConfig) -> int:
    """The fiscal years ending at the origin that the drivers read: revenue growth, the medians,
    and the year before the origin (effective interest rate)."""
    return max(config.growth_years, config.median_years, 2)


def annual_chain(store, origin: FilingRecord, as_of: date, years: int) -> list[FilingRecord]:
    """The consecutive 10-Ks listed as filed by as_of, ending at the origin's filing, oldest
    first: at most `years` + 1, the extra one giving the oldest year its start. Fiscal years
    count back from the origin's; a start is the preceding period end + 1 day (CALCULATION).
    Empty when the origin's filing is not the latest listed 10-K for its period end."""
    listed = [f for f in store.annual_filings(origin.cik, as_of) if f.period_end <= origin.end]
    if not listed or listed[-1].accession != origin.accession:
        return []
    chain = [listed[-1]]
    index = len(listed) - 1
    while index > 0 and len(chain) < years + 1 \
            and period_length_ok(listed[index - 1].period_end, chain[0].period_end):
        chain.insert(0, listed[index - 1])
        index -= 1
    records: list[FilingRecord] = []
    for offset, filing in enumerate(chain):
        start = chain[offset - 1].period_end + timedelta(days=1) if offset else None
        records.append(FilingRecord(
            issuer=origin.issuer, cik=origin.cik, form=filing.form,
            fiscal_year=origin.fiscal_year - (len(chain) - 1 - offset), start=start,
            start_classification="CALCULATION" if start is not None else "EVIDENCE_GAP",
            end=filing.period_end, filed=filing.filed, accession=filing.accession,
            primary_document=filing.primary_document,
            filing_url=build_filing_url(origin.cik, filing.accession, filing.primary_document)))
    return records


def _as_source(source: FilingRecord, record: FilingRecord) -> FilingRecord:
    """The source filing read for the record's fiscal year (its accession and date; the year's
    period)."""
    return replace(source, fiscal_year=record.fiscal_year, start=record.start, end=record.end,
                   start_classification=record.start_classification)


def tag_history_value(
    record: FilingRecord, sources: Sequence[FilingRecord], spec: LineItemSpec,
    period: Period | None, companyfacts: dict[str, Any], as_of: date,
    preferred: frozenset[str] = frozenset(),
) -> Observation:
    """One item for the record's year from Company Facts: the latest source (filed by as_of)
    tagging it with one valid value; else the latest problem (CONFLICT, INVALID); else GAP.
    preferred: the concepts carrying the set's accepted value in its pinned years; several
    values narrowed to these to one value are accepted, flagged CONCEPT_FROM_PINNED_YEARS."""
    start = period.start if period is not None else (
        record.start if spec.period_type == "duration" else None)
    end = period.end if period is not None else record.end
    common: dict = dict(period_type=spec.period_type, concepts=spec.concepts,
                        fiscal_period_start=start, fiscal_period_end=end)
    if spec.period_type == "duration" and start is None:
        return base_observation(record, spec.item_id, reason=START_UNKNOWN, **common)
    problem: Observation | None = None
    for source in sorted(sources, key=lambda f: f.filed, reverse=True):
        if source.filed > as_of:
            continue
        view = _as_source(source, record)
        found = primary_candidates(companyfacts, view, spec, period)
        amounts = sorted({c.amount for c in found})
        concepts = tuple(sorted({c.concept for c in found}))
        flags: tuple[str, ...] = (TAG_ONLY_FLAG, BC7_HISTORY)
        others: tuple = ()
        narrowed = sorted({c.amount for c in found if c.concept in preferred})
        if len(amounts) > 1 and len(narrowed) == 1:
            others = tuple(a for a in amounts if a != narrowed[0])
            amounts, flags = narrowed, flags + (CONCEPT_FROM_PINNED_YEARS,)
            concepts = tuple(sorted({c.concept for c in found if c.amount == narrowed[0]}))
        if len(amounts) > 1:
            problem = problem or base_observation(
                view, spec.item_id, status="CONFLICT", reason=MULTIPLE_VALUES,
                candidates=tuple(amounts), flags=(BC7_HISTORY,),
                **(common | {"concepts": concepts}))
        elif amounts and spec.sign != "either" and amounts[0] < 0:
            problem = problem or base_observation(
                view, spec.item_id, status="INVALID", reason=SIGN_VIOLATION,
                candidates=tuple(amounts), flags=(BC7_HISTORY,),
                **(common | {"concepts": concepts}))
        elif amounts:
            return base_observation(
                view, spec.item_id, value=amounts[0], tag_value=amounts[0],
                classification="REPORTED_FACT", status="SINGLE_CHANNEL", reason="TAG_ONLY",
                verification="TAG_ONLY", accepted=True, channels=("tag",), flags=flags,
                candidates=others,
                detail=f"Company Facts, {source.accession} filed {source.filed} (BC7 history)",
                **(common | {"concepts": concepts}))
    return problem or base_observation(record, spec.item_id, **common)


def accepted_concepts(statement_set: StatementSet, companyfacts: dict[str, Any],
                      spec: LineItemSpec) -> frozenset[str]:
    """The Company Facts concepts carrying the set's accepted value of a canonical item in its
    pinned years, from each year's own filing (restated years skipped); the concepts every such
    year agrees on; empty when there are none."""
    found: set[str] | None = None
    for filing in statement_set.filings:
        o = statement_set.get(filing.fiscal_year, spec.item_id)
        if o is None or not o.accepted or o.value is None or o.accession != filing.accession \
                or o.classification != "REPORTED_FACT":
            continue
        mine = {c.concept for c in primary_candidates(companyfacts, filing, spec)
                if c.amount == o.value}
        found = mine if found is None else found & mine
    return frozenset(found or ())


def never_reported(statement_set: StatementSet, metric: str) -> bool:
    """The set marks the metric NOT_REPORTED in every pinned year (answer 6)."""
    found = [statement_set.get(y, metric) for y in statement_set.years()]
    return bool(found) and all(o is not None and o.reason == NOT_REPORTED for o in found)


def read_history_year(
    record: FilingRecord, sources: Sequence[FilingRecord], companyfacts: dict[str, Any],
    configs: StatementConfigs, as_of: date, never: frozenset[str] = frozenset(),
    preferred: Mapping[str, frozenset[str]] | None = None,
) -> tuple[dict[str, Observation], dict[str, str | None]]:
    """Every canonical item and A.2 input for the record's year, then substitutes and the
    derivations. never: metrics the set marks NOT_REPORTED in every pinned year. preferred:
    per canonical item, the concepts carrying the set's accepted value (accepted_concepts)."""
    pool: dict[str, Observation] = {}
    for spec in configs.concept_map.values():
        pool[spec.item_id] = tag_history_value(record, sources, spec, None, companyfacts, as_of,
                                               (preferred or {}).get(spec.item_id, frozenset()))
    for spec in configs.model_inputs.inputs.values():
        if spec.is_item:
            continue
        if spec.dimensions:
            pool[spec.input_id] = base_observation(record, spec.input_id,
                                                   period_type=spec.period_type,
                                                   reason=DIMENSIONAL_INLINE_ONLY,
                                                   concepts=spec.concepts)
            continue
        period = input_period(record, spec)
        if period is None:
            pool[spec.input_id] = base_observation(record, spec.input_id,
                                                   period_type=spec.period_type,
                                                   reason=START_UNKNOWN, concepts=spec.concepts)
            continue
        pool[spec.input_id] = tag_history_value(record, sources, spec.line_item_spec(), period,
                                                companyfacts, as_of)
    for metric, observation in pool.items():
        if observation.status == "GAP" and observation.reason == "NEITHER" and metric in never:
            pool[metric] = replace(observation, reason=NOT_REPORTED)
    for spec in configs.model_inputs.inputs.values():
        if spec.substitute_item and spec.input_id in pool:
            pool[spec.input_id] = substitute(pool[spec.input_id], pool.get(spec.substitute_item),
                                             spec.substitute_basis or spec.substitute_item)
    final, bases = derive_all(record, configs.derivations, pool, provisional=False,
                              finance_lease_concepts=configs.model_inputs.finance_lease_concepts)
    flagged = {m: o if BC7_HISTORY in o.flags else replace(o, flags=o.flags + (BC7_HISTORY,))
               for m, o in final.items()}  # derived values too: every history value is marked
    return flagged, bases


@dataclass(frozen=True)
class History:
    """The BC7 years added for one origin, and why any needed year is missing."""

    origin: int
    needed: tuple[int, ...]  # years before the earliest pinned filing that the drivers read
    years: tuple[int, ...]  # the needed years with a listed 10-K
    observations: dict[tuple[int, str], Observation]
    bases: dict[int, dict[str, str | None]]
    missing: dict[int, str]  # needed year -> reason (e.g. NO_LISTED_FILING)


def read_history(statement_set: StatementSet, store, companyfacts: dict[str, Any],
                 configs: StatementConfigs, config: ForecastConfig) -> History:
    """The BC7 history for the set's origin (its latest fiscal year), as of the set's as_of."""
    as_of = statement_set.as_of
    origin = max(statement_set.years())
    earliest = min(statement_set.years())
    depth = history_depth(config)
    needed = tuple(range(origin - depth + 1, earliest))
    if not needed:
        return History(origin, (), (), {}, {}, {})
    record = next(f for f in statement_set.filings if f.fiscal_year == origin)
    chain = annual_chain(store, record, as_of, depth)
    by_year = {f.fiscal_year: f for f in chain}
    never = frozenset(m for (_, m) in statement_set.observations
                      if never_reported(statement_set, m))
    preferred = {spec.item_id: accepted_concepts(statement_set, companyfacts, spec)
                 for spec in configs.concept_map.values()}
    observations: dict[tuple[int, str], Observation] = {}
    bases: dict[int, dict[str, str | None]] = {}
    missing: dict[int, str] = {}
    for year in needed:
        own = by_year.get(year)
        if own is None:
            missing[year] = NO_LISTED_FILING
            continue
        sources = [f for f in chain if f.fiscal_year >= year]
        values, year_bases = read_history_year(own, sources, companyfacts, configs, as_of, never,
                                               preferred)
        observations.update({(year, m): o for m, o in values.items()})
        bases[year] = year_bases
    return History(origin, needed, tuple(y for y in needed if y not in missing), observations,
                   bases, missing)


def with_history(statement_set: StatementSet, history: History) -> StatementSet:
    """The set plus the BC7 years (never replacing a pinned year). Raises PointInTimeError for
    any added observation filed after the set's as_of."""
    late = [f"FY{o.fiscal_year} {o.metric} {o.accession} filed {o.filed}"
            for o in history.observations.values() if o.filed > statement_set.as_of]
    if late:
        raise PointInTimeError(f"{statement_set.issuer}: BC7 history after "
                               f"{statement_set.as_of}: {late[:5]}")
    pinned = set(statement_set.years())
    observations = {k: v for k, v in history.observations.items() if k[0] not in pinned}
    observations.update(statement_set.observations)
    bases = {y: b for y, b in history.bases.items() if y not in pinned}
    bases.update(statement_set.bases)
    note = (f"BC7 history: FY{', FY'.join(str(y) for y in history.years)} from Company Facts "
            f"(tag channel, filed by {statement_set.as_of})") if history.years else None
    return replace(statement_set, observations=observations, bases=bases,
                   notes=statement_set.notes + ([note] if note else []))


def history_refs(observations: Mapping[tuple[int, str], Observation]) -> int:
    """How many accepted BC7 history values there are."""
    return sum(1 for o in observations.values() if o.accepted and BC7_HISTORY in o.flags)
