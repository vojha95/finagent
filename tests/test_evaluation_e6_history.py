"""BC7 history for E6 (finagent.evaluation.e6_history): earlier years from Company Facts, point
in time; and EvidenceStore.annual_filings."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from statements_support import FILING, spec

from finagent.config import load_forecast_config
from finagent.evaluation.e6_history import (BC7_HISTORY, History, NO_LISTED_FILING,
                                            annual_chain, history_depth, never_reported,
                                            read_history, read_history_year, tag_history_value,
                                            with_history)
from finagent.evidence.store import EvidenceStore, SubmissionFiling
from finagent.model.history import PointInTimeError
from finagent.statements.build import StatementConfigs, StatementSet
from finagent.statements.verify_set import NOT_REPORTED, base_observation

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = StatementConfigs.load()


def listed(year: int, filed: date | None = None) -> SubmissionFiling:
    return SubmissionFiling("10-K", date(year, 12, 31), filed or date(year + 1, 2, 1),
                            f"A-{year}", f"s{year}.htm")


class FakeStore:
    def __init__(self, filings):
        self.filings = filings

    def annual_filings(self, cik, as_of):
        return [f for f in self.filings if f.filed <= as_of]


def record(year: int):
    return replace(FILING, fiscal_year=year, start=date(year, 1, 1), end=date(year, 12, 31),
                   filed=date(year + 1, 2, 1), accession=f"A-{year}")


def fact(concept_value, year, accession, filed, start=True):
    entry = {"form": "10-K", "accn": accession, "filed": filed.isoformat(),
             "end": date(year, 12, 31).isoformat(), "val": concept_value}
    if start:
        entry["start"] = date(year, 1, 1).isoformat()
    return entry


def facts(entries: dict[str, list[dict]]) -> dict:
    out: dict = {"facts": {}}
    for concept, rows in entries.items():
        taxonomy, name = concept.split(":")
        out["facts"].setdefault(taxonomy, {})[name] = {"units": {"USD": rows}}
    return out


def test_history_depth_follows_the_forecast_config():
    config = load_forecast_config()
    assert history_depth(config) == max(config.growth_years, config.median_years, 2)
    assert history_depth(replace(config, growth_years=1, median_years=1)) == 2


def test_annual_chain_counts_back_consecutive_listed_10ks():
    store = FakeStore([listed(y) for y in range(2017, 2025)])
    chain = annual_chain(store, record(2024), date(2025, 2, 1), 3)
    # years + 1 filings: the extra (oldest) one only gives the next year its start
    assert [f.fiscal_year for f in chain] == [2021, 2022, 2023, 2024]
    assert chain[0].start is None and chain[0].start_classification == "EVIDENCE_GAP"
    assert chain[1].start == date(2021, 12, 31) + timedelta(days=1)
    assert all(f.filed <= date(2025, 2, 1) for f in chain)
    gap = FakeStore([listed(2018), listed(2021), listed(2022), listed(2023), listed(2024)])
    assert [f.fiscal_year for f in annual_chain(gap, record(2024), date(2025, 2, 1), 4)] == [
        2021, 2022, 2023, 2024]  # a year missing from the listing stops the chain
    assert annual_chain(FakeStore([listed(2023)]), record(2024), date(2025, 2, 1), 3) == []


def test_tag_history_value_takes_the_latest_valid_source_filed_by_as_of():
    item = spec("revenue", sign="positive")
    sources = [record(2022), record(2023), record(2024)]
    cf = facts({"us-gaap:X": [fact(100, 2022, "A-2022", date(2023, 2, 1)),
                              fact(101, 2022, "A-2023", date(2024, 2, 1)),
                              fact(102, 2022, "A-2024", date(2025, 2, 1))]})
    found = tag_history_value(record(2022), sources, item, None, cf, date(2024, 2, 1))
    assert found.value == Decimal(101) and found.accession == "A-2023"  # A-2024 is later
    assert found.status == "SINGLE_CHANNEL" and found.accepted and BC7_HISTORY in found.flags
    assert found.filed <= date(2024, 2, 1)
    assert found.fiscal_year == 2022 and found.fiscal_period_start == date(2022, 1, 1)


def test_tag_history_value_problems_and_gaps():
    item = spec("revenue", sign="positive")
    sources = [record(2022), record(2023)]
    two = facts({"us-gaap:X": [fact(100, 2022, "A-2022", date(2023, 2, 1)),
                               fact(5, 2022, "A-2023", date(2024, 2, 1)),
                               fact(6, 2022, "A-2023", date(2024, 2, 1))]})
    assert tag_history_value(record(2022), sources, item, None, two,
                             date(2024, 2, 1)).value == Decimal(100)  # latest valid source
    conflict = facts({"us-gaap:X": [fact(5, 2022, "A-2023", date(2024, 2, 1)),
                                    fact(6, 2022, "A-2023", date(2024, 2, 1))]})
    found = tag_history_value(record(2022), sources, item, None, conflict, date(2024, 2, 1))
    assert found.status == "CONFLICT" and not found.accepted
    negative = facts({"us-gaap:X": [fact(-5, 2022, "A-2022", date(2023, 2, 1))]})
    found = tag_history_value(record(2022), sources, item, None, negative, date(2024, 2, 1))
    assert found.status == "INVALID" and not found.accepted
    found = tag_history_value(record(2022), sources, item, None, facts({}), date(2024, 2, 1))
    assert (found.status, found.reason, found.value) == ("GAP", "NEITHER", None)
    no_start = replace(record(2022), start=None)
    assert tag_history_value(no_start, sources, item, None, two,
                             date(2024, 2, 1)).reason == "START_UNKNOWN"


def obs_gap(year, metric, reason):
    return base_observation(record(year), metric, reason=reason)


def pinned_set(years, as_of, observations=None) -> StatementSet:
    return StatementSet("SYNT", FILING.cik, as_of, tuple(record(y) for y in years),
                        observations or {}, [], {y: {} for y in years}, "test")


def test_never_reported_needs_every_pinned_year():
    s = pinned_set((2023, 2024), date(2025, 2, 1),
                   {(2023, "inventory"): obs_gap(2023, "inventory", NOT_REPORTED),
                    (2024, "inventory"): obs_gap(2024, "inventory", NOT_REPORTED),
                    (2023, "cash"): obs_gap(2023, "cash", NOT_REPORTED),
                    (2024, "cash"): obs_gap(2024, "cash", "NEITHER")})
    assert never_reported(s, "inventory") and not never_reported(s, "cash")


def concept(item: str) -> str:
    return CONFIGS.concept_map[item].concepts[0]


def test_read_history_year_reads_items_and_derives_flagged():
    debt = concept("long_term_debt")
    cf = facts({concept("revenue"): [fact(500, 2022, "A-2022", date(2023, 2, 1))],
                debt: [fact(70, 2022, "A-2022", date(2023, 2, 1), start=False)]})
    values, bases = read_history_year(record(2022), [record(2022)], cf, CONFIGS,
                                      date(2023, 2, 1), frozenset({"inventory"}))
    assert values["revenue"].value == Decimal(500) and values["revenue"].accepted
    assert values["funded_debt"].value == Decimal(70)
    assert values["funded_debt"].classification == "CALCULATION"
    assert all(BC7_HISTORY in o.flags for o in values.values())
    assert values["inventory"].reason == NOT_REPORTED
    assert values["accounts_receivable"].reason == "NEITHER"  # missing is never zero
    assert "ebit_basis" in bases


def test_read_history_adds_only_needed_years_before_the_pinned_ones():
    store = FakeStore([listed(y) for y in range(2018, 2025)])
    as_of = date(2025, 2, 1)
    rows = [fact(100 + y, y, f"A-{y}", date(y + 1, 2, 1)) for y in range(2019, 2024)]
    cf = facts({concept("revenue"): rows})
    s = pinned_set((2024,), as_of)
    found = read_history(s, store, cf, CONFIGS, load_forecast_config())
    assert found.needed == (2021, 2022, 2023) and found.years == found.needed
    assert found.observations[(2021, "revenue")].value == Decimal(2121)
    merged = with_history(s, found)
    assert merged.years() == [2024]  # the origin is unchanged
    assert merged.get(2022, "revenue").value == Decimal(2122)
    assert any("BC7 history" in n for n in merged.notes)
    nothing = read_history(pinned_set((2021, 2022, 2023, 2024), as_of), store, cf, CONFIGS,
                           load_forecast_config())
    assert nothing.needed == () and not nothing.observations
    sparse = read_history(s, FakeStore([listed(2022), listed(2023), listed(2024)]), cf,
                          CONFIGS, load_forecast_config())
    assert sparse.missing == {2021: NO_LISTED_FILING}


def test_with_history_refuses_a_value_filed_after_as_of_and_keeps_pinned_years():
    s = pinned_set((2024,), date(2025, 2, 1),
                   {(2024, "revenue"): obs_gap(2024, "revenue", "NEITHER")})
    late = replace(obs_gap(2023, "revenue", "NEITHER"), filed=date(2025, 3, 1))
    with pytest.raises(PointInTimeError):
        with_history(s, History(2024, (2023,), (2023,), {(2023, "revenue"): late}, {}, {}))
    shadow = obs_gap(2024, "revenue", "SHADOW")
    merged = with_history(s, History(2024, (), (), {(2024, "revenue"): shadow}, {}, {}))
    assert merged.get(2024, "revenue").reason == "NEITHER"


def test_annual_filings_lists_10ks_filed_by_as_of_on_real_evidence():
    store = EvidenceStore(ROOT / "evidence")
    cik = store.resolve_cik("AAPL")  # a train company
    found = store.annual_filings(cik, date(2022, 10, 28))
    assert found and all(f.form == "10-K" and f.filed <= date(2022, 10, 28) for f in found)
    assert [f.period_end for f in found] == sorted(f.period_end for f in found)


def test_read_history_on_real_company_facts_is_point_in_time():
    """A train company's first pinned origin: the history years come from Company Facts filed
    by d(t) only."""
    from finagent.evidence.integrity import read_snapshot_json

    store = EvidenceStore(ROOT / "evidence")
    first = store.load_filings("AAPL", date(2026, 11, 24))[0]
    origin = store.load_filings("AAPL", first.filed)[-1]
    cf = read_snapshot_json(store.evidence_dir, f"companyfacts_{origin.cik}.json",
                            store.manifest)
    s = StatementSet(origin.issuer, origin.cik, origin.filed, (origin,), {}, [],
                     {origin.fiscal_year: {}}, "test")
    found = read_history(s, store, cf, CONFIGS, load_forecast_config())
    assert found.years == tuple(range(origin.fiscal_year - 3, origin.fiscal_year))
    assert all(o.filed <= origin.filed for o in found.observations.values())
    assert all(found.observations[(y, "revenue")].accepted for y in found.years)
    with_history(s, found)  # raises if anything is later than d(t)


def test_several_values_are_narrowed_to_the_concept_of_the_pinned_years():
    from finagent.evaluation.e6_history import CONCEPT_FROM_PINNED_YEARS, accepted_concepts

    item = spec("revenue", sign="positive", concepts=("us-gaap:A", "us-gaap:B"))
    cf = facts({"us-gaap:A": [fact(105, 2022, "A-2022", date(2023, 2, 1)),
                              fact(205, 2024, "A-2024", date(2025, 2, 1))],
                "us-gaap:B": [fact(100, 2022, "A-2022", date(2023, 2, 1)),
                              fact(200, 2024, "A-2024", date(2025, 2, 1))]})
    accepted = replace(obs_gap(2024, "revenue", "AGREE"), value=Decimal(200), accepted=True,
                       status="VERIFIED", classification="REPORTED_FACT")
    s = pinned_set((2024,), date(2025, 2, 1), {(2024, "revenue"): accepted})
    preferred = accepted_concepts(s, cf, item)
    assert preferred == frozenset({"us-gaap:B"})
    found = tag_history_value(record(2022), [record(2022)], item, None, cf, date(2025, 2, 1),
                              preferred)
    assert found.value == Decimal(100) and found.accepted
    assert CONCEPT_FROM_PINNED_YEARS in found.flags and found.candidates == (Decimal(105),)
    plain = tag_history_value(record(2022), [record(2022)], item, None, cf, date(2025, 2, 1))
    assert plain.status == "CONFLICT"  # without the pinned concept: no choice is made
    restated = replace(accepted, accession="A-2025")
    assert accepted_concepts(pinned_set((2024,), date(2025, 2, 1),
                                        {(2024, "revenue"): restated}), cf, item) == frozenset()
