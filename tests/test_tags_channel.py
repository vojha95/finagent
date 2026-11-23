import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from finagent.config import LineItemSpec, core_items, load_concept_map
from finagent.evidence import EvidenceStore
from finagent.evidence.store import FilingRecord
from finagent.ixbrl.tagstore import TagFact
from finagent.records import Outcome
from finagent.tags import (
    AMOUNT_MISMATCH,
    CORROBORATED,
    INLINE_ONLY,
    MULTIPLE_VALUES_INLINE,
    MULTIPLE_VALUES_PRIMARY,
    NIL,
    NOT_FOUND,
    NOT_FOUND_INLINE,
    SIGN_VIOLATION,
    START_UNKNOWN,
    TagCandidate,
    consistent_at_precision,
    fact_matches,
    has_nil_fact,
    inline_candidates,
    observation_matches,
    precision,
    resolve_duplicates,
    valued_facts,
    primary_candidates,
    read_item,
    read_items,
    read_tag_channel,
)

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "evidence"
AS_OF = date(2026, 11, 23)
ACCN = "0000000001-26-000001"

FILING = FilingRecord(
    issuer="TEST",
    cik="0000000001",
    form="10-K",
    fiscal_year=2025,
    start=date(2025, 1, 1),
    start_classification="CALCULATION",
    end=date(2025, 12, 31),
    filed=date(2026, 2, 1),
    accession=ACCN,
    primary_document="test-20251231.htm",
    filing_url="https://example.invalid/test-20251231.htm",
)

REVENUE = LineItemSpec(
    item_id="revenue",
    label="Revenue",
    statement="IS",
    period_type="duration",
    sign="positive",
    core=True,
    concepts=("us-gaap:Revenues", "us-gaap:SalesRevenueNet"),
)
CASH = LineItemSpec(
    item_id="cash",
    label="Cash",
    statement="BS",
    period_type="instant",
    sign="positive",
    core=True,
    concepts=("us-gaap:Cash",),
)
INCOME = LineItemSpec(
    item_id="income",
    label="Income",
    statement="IS",
    period_type="duration",
    sign="either",
    core=True,
    concepts=("us-gaap:Income",),
)


def obs(val: Any, start: str | None = "2025-01-01", **over: Any) -> dict[str, Any]:
    o = {"end": "2025-12-31", "val": val, "accn": ACCN, "form": "10-K", "filed": "2026-02-01"}
    if start is not None:
        o["start"] = start
    o.update(over)
    return o


def cf(concepts: dict[str, list[dict[str, Any]]], unit: str = "USD") -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for concept, observations in concepts.items():
        taxonomy, _, name = concept.partition(":")
        facts.setdefault(taxonomy, {})[name] = {"units": {unit: observations}}
    return {"facts": facts}


def fact(
    concept: str,
    amount: Decimal | None,
    start: date | None = date(2025, 1, 1),
    dimensions: dict[str, str] | None = None,
    unit: str = "USD",
    accession: str = ACCN,
    decimals: str | None = "-6",
) -> TagFact:
    return TagFact(
        accession=accession,
        concept=concept,
        context_id="c",
        start=start,
        end=date(2025, 12, 31),
        dimensions=dimensions or {},
        unit=unit,
        scale=6,
        sign=1,
        amount=amount,
        cell_address=None,
        is_nil=amount is None,
        decimals=decimals,
    )


D = Decimal


# --- Company Facts primary lookup ---------------------------------------------------------


def test_observation_matches_requires_every_key() -> None:
    assert observation_matches(obs(1), FILING, "duration")
    assert not observation_matches(obs(1, form="10-K/A"), FILING, "duration")
    assert not observation_matches(obs(1, accn="other"), FILING, "duration")
    assert not observation_matches(obs(1, filed="2026-03-01"), FILING, "duration")
    assert not observation_matches(obs(1, end="2024-12-31"), FILING, "duration")
    assert not observation_matches(obs(1, start="2024-01-01"), FILING, "duration")


def test_observation_matches_instant_has_no_start() -> None:
    assert observation_matches(obs(1, start=None), FILING, "instant")
    assert not observation_matches(obs(1), FILING, "instant")


def test_primary_candidates_pool_concepts_and_dedupe() -> None:
    data = cf({"us-gaap:Revenues": [obs(100), obs(100)], "us-gaap:SalesRevenueNet": [obs(100)]})
    assert primary_candidates(data, FILING, REVENUE) == [
        TagCandidate("us-gaap:Revenues", D(100)),
        TagCandidate("us-gaap:SalesRevenueNet", D(100)),
    ]


def test_primary_candidates_ignore_non_usd() -> None:
    assert primary_candidates(cf({"us-gaap:Revenues": [obs(100)]}, unit="EUR"), FILING, REVENUE) == []


# --- inline lookups -----------------------------------------------------------------------


def test_fact_matches_filters_dimension_unit_period_and_accession() -> None:
    concept = "us-gaap:Revenues"
    assert fact_matches(fact(concept, D(1)), concept, FILING, "duration")
    assert not fact_matches(fact(concept, D(1), dimensions={"a:Axis": "a:M"}), concept, FILING, "duration")
    assert not fact_matches(fact(concept, D(1), unit="EUR"), concept, FILING, "duration")
    assert not fact_matches(fact(concept, D(1), start=date(2024, 1, 1)), concept, FILING, "duration")
    assert not fact_matches(fact(concept, D(1), accession="other"), concept, FILING, "duration")
    assert not fact_matches(fact(concept, D(1)), "us-gaap:Other", FILING, "duration")


def test_valued_facts_skips_nil() -> None:
    facts = [fact("us-gaap:Revenues", D(5)), fact("us-gaap:Revenues", None)]
    assert [f.amount for f in valued_facts(facts, "us-gaap:Revenues", FILING, "duration")] == [D(5)]


def test_precision_reads_decimals() -> None:
    assert precision(fact("us-gaap:X", D(1), decimals="-8")) == -8
    assert precision(fact("us-gaap:X", D(1), decimals="INF")) is None
    assert precision(fact("us-gaap:X", D(1), decimals=None)) is None


def test_consistent_at_precision() -> None:
    rounded = fact("us-gaap:X", D("4.8E+9"), decimals="-8")
    assert consistent_at_precision(D(4_791_000_000), rounded)
    assert not consistent_at_precision(D(4_741_000_000), rounded)
    exact = fact("us-gaap:X", D(4_791_000_000), decimals="INF")
    assert consistent_at_precision(D(4_791_000_000), exact)
    assert not consistent_at_precision(D(4_791_000_001), exact)


def test_consistent_at_precision_ties_round_half_even() -> None:
    assert consistent_at_precision(D(4_750_000_000), fact("us-gaap:X", D("4.8E+9"), decimals="-8"))
    assert consistent_at_precision(D(4_850_000_000), fact("us-gaap:X", D("4.8E+9"), decimals="-8"))


def test_resolve_duplicates_keeps_most_precise_when_consistent() -> None:
    dupes = [fact("us-gaap:X", D(4_791_000_000)), fact("us-gaap:X", D("4.8E+9"), decimals="-8")]
    assert resolve_duplicates(dupes) == {D(4_791_000_000)}


def test_resolve_duplicates_reports_all_when_inconsistent() -> None:
    dupes = [fact("us-gaap:X", D(4_791_000_000)), fact("us-gaap:X", D("4.7E+9"), decimals="-8")]
    assert resolve_duplicates(dupes) == {D(4_791_000_000), D("4.7E+9")}
    assert resolve_duplicates([fact("us-gaap:X", D(5)), fact("us-gaap:X", D(6))]) == {D(5), D(6)}
    assert resolve_duplicates([]) == set()


def test_inline_candidates_and_nil_detection() -> None:
    facts = [fact("us-gaap:SalesRevenueNet", D(7)), fact("us-gaap:Revenues", None)]
    assert inline_candidates(facts, FILING, REVENUE) == [TagCandidate("us-gaap:SalesRevenueNet", D(7))]
    assert has_nil_fact(facts, FILING, REVENUE)
    assert not has_nil_fact(facts[:1], FILING, REVENUE)


# --- read_item: one test per outcome path -------------------------------------------------


def test_verified_when_primary_corroborated() -> None:
    out = read_item(FILING, REVENUE, cf({"us-gaap:Revenues": [obs(100)]}), [fact("us-gaap:Revenues", D(100))])
    assert (out.status, out.reason, out.value) == ("VERIFIED", CORROBORATED, D(100))
    reading = out.observations[0]
    assert reading.channel == "tag" and reading.source == "companyfacts"
    assert reading.concept == "us-gaap:Revenues" and not reading.single_source
    assert reading.start == FILING.start and reading.accession == ACCN


def test_conflict_when_primary_synonyms_disagree() -> None:
    data = cf({"us-gaap:Revenues": [obs(100)], "us-gaap:SalesRevenueNet": [obs(90)]})
    out = read_item(FILING, REVENUE, data, [])
    assert (out.status, out.reason, out.value) == ("CONFLICT", MULTIPLE_VALUES_PRIMARY, None)
    assert {r.value for r in out.observations} == {D(100), D(90)}


def test_conflict_amount_mismatch_inline() -> None:
    out = read_item(FILING, REVENUE, cf({"us-gaap:Revenues": [obs(100)]}), [fact("us-gaap:Revenues", D(99))])
    assert (out.status, out.reason) == ("CONFLICT", AMOUNT_MISMATCH)
    assert {(r.source, r.value) for r in out.observations} == {("companyfacts", D(100)), ("inline", D(99))}


def test_conflict_not_found_inline() -> None:
    dimensional = fact("us-gaap:Revenues", D(100), dimensions={"a:Axis": "a:M"})
    out = read_item(FILING, REVENUE, cf({"us-gaap:Revenues": [obs(100)]}), [dimensional])
    assert (out.status, out.reason) == ("CONFLICT", NOT_FOUND_INLINE)


def test_inline_corroboration_requires_same_concept() -> None:
    out = read_item(
        FILING, REVENUE, cf({"us-gaap:Revenues": [obs(100)]}), [fact("us-gaap:SalesRevenueNet", D(100))]
    )
    assert out.reason == NOT_FOUND_INLINE


def test_verified_with_consistent_rounded_duplicate() -> None:
    facts = [fact("us-gaap:Revenues", D(4_791_000_000)), fact("us-gaap:Revenues", D("4.8E+9"), decimals="-8")]
    out = read_item(FILING, REVENUE, cf({"us-gaap:Revenues": [obs(4_791_000_000)]}), facts)
    assert (out.status, out.reason, out.value) == ("VERIFIED", CORROBORATED, D(4_791_000_000))


def test_mismatch_when_duplicate_disagrees_at_its_own_precision() -> None:
    facts = [fact("us-gaap:Revenues", D(4_791_000_000)), fact("us-gaap:Revenues", D("4.7E+9"), decimals="-8")]
    out = read_item(FILING, REVENUE, cf({"us-gaap:Revenues": [obs(4_791_000_000)]}), facts)
    assert (out.status, out.reason) == ("CONFLICT", AMOUNT_MISMATCH)
    assert {r.value for r in out.observations if r.source == "inline"} == {D("4.7E+9")}


def test_not_found_inline_when_only_rounded_duplicates() -> None:
    facts = [fact("us-gaap:Revenues", D("4.8E+9"), decimals="-8")]
    out = read_item(FILING, REVENUE, cf({"us-gaap:Revenues": [obs(4_791_000_000)]}), facts)
    assert (out.status, out.reason) == ("CONFLICT", NOT_FOUND_INLINE)


def test_fallback_collapses_consistent_rounded_duplicates() -> None:
    facts = [fact("us-gaap:Revenues", D(4_791_000_000)), fact("us-gaap:Revenues", D("4.8E+9"), decimals="-8")]
    out = read_item(FILING, REVENUE, cf({}), facts)
    assert (out.status, out.reason, out.value) == ("MISSING_PRIMARY", INLINE_ONLY, D(4_791_000_000))


def test_missing_primary_falls_back_to_inline_only() -> None:
    out = read_item(FILING, REVENUE, cf({}), [fact("us-gaap:SalesRevenueNet", D(50))])
    assert (out.status, out.reason, out.value) == ("MISSING_PRIMARY", INLINE_ONLY, D(50))
    reading = out.observations[0]
    assert reading.source == "inline_only" and reading.single_source
    assert reading.status == "MISSING_PRIMARY"


def test_inline_only_never_verified() -> None:
    out = read_item(FILING, REVENUE, cf({}), [fact("us-gaap:Revenues", D(50))])
    assert out.status != "VERIFIED"


def test_fallback_conflict_when_inline_disagrees() -> None:
    facts = [fact("us-gaap:Revenues", D(50)), fact("us-gaap:SalesRevenueNet", D(51))]
    out = read_item(FILING, REVENUE, cf({}), facts)
    assert (out.status, out.reason) == ("CONFLICT", MULTIPLE_VALUES_INLINE)


def test_gap_when_nothing_found() -> None:
    out = read_item(FILING, REVENUE, cf({}), [])
    assert (out.status, out.reason, out.value, out.observations) == ("GAP", NOT_FOUND, None, ())


def test_gap_nil_inline_fact_is_not_zero() -> None:
    out = read_item(FILING, REVENUE, cf({}), [fact("us-gaap:Revenues", None)])
    assert (out.status, out.reason, out.value) == ("GAP", NIL, None)


def test_invalid_sign_on_primary() -> None:
    out = read_item(FILING, REVENUE, cf({"us-gaap:Revenues": [obs(-5)]}), [fact("us-gaap:Revenues", D(-5))])
    assert (out.status, out.reason) == ("INVALID", SIGN_VIOLATION)


def test_invalid_sign_on_inline_only() -> None:
    out = read_item(FILING, REVENUE, cf({}), [fact("us-gaap:Revenues", D(-5))])
    assert (out.status, out.reason) == ("INVALID", SIGN_VIOLATION)
    assert out.observations[0].single_source


def test_negative_allowed_when_sign_either() -> None:
    out = read_item(FILING, INCOME, cf({"us-gaap:Income": [obs(-5)]}), [fact("us-gaap:Income", D(-5))])
    assert (out.status, out.value) == ("VERIFIED", D(-5))


def test_instant_item_verified() -> None:
    out = read_item(FILING, CASH, cf({"us-gaap:Cash": [obs(9, start=None)]}), [fact("us-gaap:Cash", D(9), start=None)])
    assert (out.status, out.value) == ("VERIFIED", D(9))
    assert out.observations[0].start is None


def test_duration_item_gap_when_start_unknown() -> None:
    filing = FilingRecord(**{**FILING.__dict__, "start": None, "start_classification": "EVIDENCE_GAP"})
    out = read_item(filing, REVENUE, cf({"us-gaap:Revenues": [obs(100)]}), [])
    assert (out.status, out.reason) == ("GAP", START_UNKNOWN)


def test_read_items_rejects_filing_after_as_of() -> None:
    with pytest.raises(ValueError, match="after"):
        read_items(FILING, [REVENUE], cf({}), [], as_of=date(2026, 1, 31))


def test_read_items_returns_one_outcome_per_item() -> None:
    out = read_items(FILING, [REVENUE, CASH], cf({}), [], as_of=AS_OF)
    assert set(out) == {"revenue", "cash"}
    assert all(isinstance(o, Outcome) for o in out.values())


# --- real evidence ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def store() -> EvidenceStore:
    return EvidenceStore(EVIDENCE_DIR)


@pytest.fixture(scope="module")
def amzn_fy2025(store: EvidenceStore) -> dict[str, Outcome]:
    return read_tag_channel(store, "AMZN", 2025, AS_OF)


def test_amazon_fy2025_revenue_verified(amzn_fy2025: dict[str, Outcome]) -> None:
    out = amzn_fy2025["revenue"]
    assert (out.status, out.value) == ("VERIFIED", D("716924000000"))
    assert out.observations[0].concept == "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"


def test_amazon_fy2025_capex_uses_productive_assets(amzn_fy2025: dict[str, Outcome]) -> None:
    out = amzn_fy2025["capital_expenditure"]
    assert (out.status, out.value) == ("VERIFIED", D("131819000000"))
    assert out.observations[0].concept == "us-gaap:PaymentsToAcquireProductiveAssets"


def test_amazon_income_tax_rounded_duplicate_verified(store: EvidenceStore) -> None:
    """FY2025 tags 19,087M exactly in tables and "$19.1 billion" (decimals -8) in text."""
    out = read_tag_channel(store, "AMZN", 2025, AS_OF, items=["income_tax_expense"])["income_tax_expense"]
    assert (out.status, out.reason, out.value) == ("VERIFIED", CORROBORATED, D("19087000000"))


@pytest.mark.parametrize("ticker", ["AMZN", "MSFT"])
def test_every_core_item_has_an_outcome_every_year(store: EvidenceStore, ticker: str) -> None:
    core = [s.item_id for s in core_items(load_concept_map())]
    for filing in store.load_filings(ticker, AS_OF):
        outcomes = read_tag_channel(store, ticker, filing.fiscal_year, AS_OF)
        assert list(outcomes) == core
        for item, out in outcomes.items():
            assert out.status in {"VERIFIED", "MISSING_PRIMARY", "CONFLICT", "INVALID", "GAP"}
            if out.status == "VERIFIED":
                assert out.value is not None and out.observations[0].accession == filing.accession
            if out.status == "GAP":
                assert out.value is None, (ticker, filing.fiscal_year, item)


def test_tag_channel_refuses_year_not_visible_as_of(store: EvidenceStore) -> None:
    with pytest.raises(LookupError):
        read_tag_channel(store, "AMZN", 2025, date(2026, 1, 1))


# --- guard: no company-specific code -------------------------------------------------------


def test_no_company_specific_code_in_tag_channel() -> None:
    pattern = re.compile(r"\b(ticker|issuer|cik)\s*(==|!=|in)\s*['\"(\[{]|AMZN|MSFT|1018724|789019")
    for path in (ROOT / "finagent" / "tags").glob("*.py"):
        assert not pattern.search(path.read_text(encoding="utf-8")), path
