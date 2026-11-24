"""Tag lookups with a period override (opening instants) and dimensions (Phase 4a refactor).

Defaults are unchanged: the filing's own period and a non-dimensional context.
"""

from datetime import date
from decimal import Decimal

from statements_support import END, FILING, START, fact, spec

from finagent.tags.channel import INLINE_ONLY, START_UNKNOWN, read_item
from finagent.tags.companyfacts import Period, observation_matches, period_bounds, primary_candidates
from finagent.tags.inline import fact_matches, has_nil_fact, inline_candidates, valued_facts

OPENING = Period(None, date(2024, 12, 31))
RE_MEMBER = {"us-gaap:StatementEquityComponentsAxis": "us-gaap:RetainedEarningsMember"}


def _observation(end, start=None, val=5):
    raw = {"form": "10-K", "accn": FILING.accession, "filed": FILING.filed.isoformat(),
           "end": end.isoformat(), "val": val}
    if start:
        raw["start"] = start.isoformat()
    return raw


def test_period_bounds_default_and_override():
    assert period_bounds(FILING, "duration") == (START, END)
    assert period_bounds(FILING, "instant") == (None, END)
    assert period_bounds(FILING, "instant", OPENING) == (None, date(2024, 12, 31))


def test_observation_matches_opening_instant_only_with_the_period():
    opening = _observation(date(2024, 12, 31))
    assert not observation_matches(opening, FILING, "instant")
    assert observation_matches(opening, FILING, "instant", OPENING)
    assert observation_matches(_observation(END), FILING, "instant")


def test_primary_candidates_with_period():
    companyfacts = {"facts": {"us-gaap": {"RE": {"units": {"USD": [
        _observation(END, val=10), _observation(date(2024, 12, 31), val=7)]}}}}}
    item = spec("re", "BS", "instant", concepts=("us-gaap:RE",))
    assert [c.amount for c in primary_candidates(companyfacts, FILING, item)] == [10]
    assert [c.amount for c in primary_candidates(companyfacts, FILING, item, OPENING)] == [7]


def test_fact_matches_default_unchanged_and_dimensions_exact():
    plain = fact("us-gaap:X", 1, start=START)
    dimensional = fact("us-gaap:X", 2, start=START, dims=RE_MEMBER)
    assert fact_matches(plain, "us-gaap:X", FILING, "duration")
    assert not fact_matches(dimensional, "us-gaap:X", FILING, "duration")
    assert fact_matches(dimensional, "us-gaap:X", FILING, "duration", dimensions=RE_MEMBER)
    assert not fact_matches(plain, "us-gaap:X", FILING, "duration", dimensions=RE_MEMBER)
    other = {"us-gaap:StatementEquityComponentsAxis": "us-gaap:CommonStockMember"}
    assert not fact_matches(dimensional, "us-gaap:X", FILING, "duration", dimensions=other)


def test_fact_matches_opening_instant():
    opening = fact("us-gaap:RE", 7, end=date(2024, 12, 31))
    assert not fact_matches(opening, "us-gaap:RE", FILING, "instant")
    assert fact_matches(opening, "us-gaap:RE", FILING, "instant", OPENING)
    assert valued_facts([opening], "us-gaap:RE", FILING, "instant", OPENING) == [opening]


def test_inline_candidates_and_nil_with_dimensions():
    item = spec("dividends", concepts=("us-gaap:D",))
    facts = [fact("us-gaap:D", 3, start=START, dims=RE_MEMBER),
             fact("us-gaap:D", 9, start=START)]
    found = inline_candidates(facts, FILING, item, dimensions=RE_MEMBER)
    assert [c.amount for c in found] == [3]
    nil = [fact("us-gaap:D", 0, start=START, dims=RE_MEMBER, nil=True)]
    assert has_nil_fact(nil, FILING, item, dimensions=RE_MEMBER)
    assert not has_nil_fact(nil, FILING, item)


def test_read_item_dimensional_is_inline_only():
    item = spec("dividends", concepts=("us-gaap:D",))
    companyfacts = {"facts": {"us-gaap": {"D": {"units": {"USD": [
        _observation(END, START, val=9)]}}}}}
    facts = [fact("us-gaap:D", 3, start=START, dims=RE_MEMBER)]
    outcome = read_item(FILING, item, companyfacts, facts, dimensions=RE_MEMBER)
    assert (outcome.status, outcome.reason, outcome.value) == ("MISSING_PRIMARY", INLINE_ONLY,
                                                               Decimal(3))
    assert outcome.observations[0].single_source


def test_read_item_period_needs_start_for_duration():
    item = spec("x")
    outcome = read_item(FILING, item, {}, [], period=Period(None, END))
    assert outcome.reason == START_UNKNOWN
