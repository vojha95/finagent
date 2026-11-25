"""Issuer resolution (blueprint 8.15 step 1): names, tickers, CIKs, outside-universe companies
and the second-issuer rules."""

import pytest
from planner_support import held_out_name, resources

from finagent.config import IssuerNames
from finagent.planner.issuers import (IssuerIndex, cik_mentions, find_mentions, has_comparison,
                                      mask_issuers, name_matches, normalise_title,
                                      possessive_mentions,
                                      registrant_mentions, resolve, stop_words,
                                      universe_mentions)

R = resources()


def _resolve(question, selected=None):
    return resolve(question, R.index, R.vocabulary, R.planner.max_issuers, selected)


def _small_index():
    names = IssuerNames({"AAA": ("Alpha Widgets", "Alpha"), "BBB": ("Target",),
                         "LOW": ("Lowes",), "QQQ": ("Quuxco",)}, frozenset())
    sec = [{"cik_str": 11, "ticker": "AAA", "title": "ALPHA WIDGETS INC"},
           {"cik_str": 22, "ticker": "BBB", "title": "TARGET CORP"},
           {"cik_str": 33, "ticker": "ZZZ", "title": "Zeta Motors, Inc."}]
    return IssuerIndex.build({"AAA": "train", "BBB": "validation", "LOW": "train",
                              "QQQ": "train"}, names, sec, R.vocabulary,
                             common_words={"target", "low", "alpha"})


def _tickers(question, index=R.index):
    return [m.ticker for m in universe_mentions(question, index)]


def test_build_maps_titles_and_ciks():
    index = _small_index()
    assert index.registrants["zeta motors"] == "ZZZ" and index.ciks[11] == "AAA"


def test_load_reads_the_pinned_ticker_map_and_the_bundled_word_list():
    assert R.index.registrants and set(R.index.universe) >= {"AMZN", "MSFT"}
    assert {"target", "chevron", "low", "cost"} <= R.index.common_words
    assert not R.index.is_common_word("Microsoft")


def test_names_any_case_with_or_without_an_apostrophe():
    """#231 (1): names in any case; a possessive with or without an apostrophe."""
    assert _tickers("Amazon and MSFT revenue") == ["AMZN", "MSFT"]
    assert _tickers("amazon revenue") == ["AMZN"]
    assert _tickers("whats abbotts net income") == ["ABT"]
    assert _tickers("Starbucks' revenue") == ["SBUX"]
    assert [m.ticker for m in universe_mentions("AT&T's debt", R.index)] == ["T"]


def test_common_word_names_need_a_capital_or_a_possessive():
    """#231 (2): a one-word name that is a common English word."""
    index = _small_index()
    assert _tickers("Target revenue", index) == ["BBB"]
    assert _tickers("TARGET revenue", index) == ["BBB"]
    assert _tickers("target's revenue", index) == ["BBB"]
    assert _tickers("the target year", index) == []
    assert _tickers("what were chevrons accounts receivable") == ["CVX"]
    assert _tickers("chevron revenue") == []
    assert _tickers("quuxco revenue", index) == ["QQQ"]  # not a common word: any case


def test_lower_case_tickers_only_when_not_a_common_word():
    """#231 (3): "msft" yes; "low" and a ticker after an apostrophe no."""
    index = _small_index()
    assert _tickers("msft revenue") == ["MSFT"]
    assert _tickers("low margins", index) == []
    assert _tickers("LOW revenue", index) == ["LOW"]
    assert _tickers("cost of revenue at Nike") == ["NKE"]
    assert _tickers("I can't say") == []
    assert _tickers("qqq revenue", index) == ["QQQ"]
    assert _tickers("o'qqq revenue", index) == []


def test_name_matches_and_is_common_word():
    index = _small_index()
    assert index.is_common_word("Target") and not index.is_common_word("Quuxco")
    assert [m.group(0) for m in name_matches("Target", index, "target's and Target, target")] \
        == ["target's", "Target"]
    assert [m.group(0) for m in name_matches("Quuxco", index, "quuxcos QUUXCO")] == \
        ["quuxcos", "QUUXCO"]
    assert [m.group(0) for m in name_matches("Alpha Widgets", index, "alpha widgets' debt")] \
        == ["alpha widgets'"]  # several words: any case


def test_cik_mentions():
    index = _small_index()
    assert [m.ticker for m in cik_mentions("revenue for CIK 0000000011", index)] == ["AAA"]
    assert [m.ticker for m in cik_mentions("cik: 33", index)] == [None]  # outside the universe


def test_registrant_and_possessive_mentions_are_outside_the_universe():
    index = _small_index()
    assert [m.ticker for m in registrant_mentions("Zeta Motors revenue", index, [])] == [None]
    assert [m.text for m in possessive_mentions("Acme's revenue", index, [])] == ["Acme's"]
    assert possessive_mentions("What was the Company's revenue", R.index, []) == []


def test_find_and_mask():
    q = "Compare Amazon and Microsoft revenue"
    mentions = find_mentions(q, R.index)
    assert [m.ticker for m in mentions] == ["AMZN", "MSFT"]
    assert mask_issuers(q, mentions) == "Compare <issuer> and <issuer> revenue"


def test_has_comparison():
    assert has_comparison("Amazon vs Microsoft", R.vocabulary)
    assert not has_comparison("Amazon and Microsoft", R.vocabulary)


def test_normalise_title_and_stop_words():
    assert normalise_title("AMAZON COM INC /DE/", ("inc",)) == "amazon com"
    assert {"revenue", "what"} <= stop_words(R.vocabulary)


def test_resolve_rules():
    assert _resolve("Amazon revenue").issuers == ("AMZN",)
    assert _resolve("revenue in 2024").clarification == "NO_ISSUER"
    assert _resolve("revenue in 2024", "MSFT").issuers == ("MSFT",)
    assert _resolve("Amazon revenue", "MSFT").clarification == "ISSUER_MISMATCH"
    assert _resolve("Amazon and Microsoft revenue").clarification == "SECOND_ISSUER"
    both = _resolve("Compare Amazon and Microsoft revenue")
    assert both.issuers == ("AMZN", "MSFT") and both.comparison
    assert _resolve("Compare Amazon, Microsoft and Chevron").clarification == "TOO_MANY_ISSUERS"
    assert _resolve("Compare Amazon and Microsoft", "CVX").clarification == "ISSUER_MISMATCH"
    assert _resolve("Tesla revenue in 2024").clarification == "UNSUPPORTED_ISSUER"
    assert _resolve("Acme's revenue").clarification == "UNSUPPORTED_ISSUER"


def test_selected_issuer_must_be_in_the_universe():
    with pytest.raises(ValueError):
        _resolve("revenue", "NOPE")


def test_held_out_name_resolves_and_is_flagged_without_reading_data():
    ticker, name = held_out_name()
    found = _resolve(f"{name} revenue in 2024")
    assert found.issuers == (ticker,) and found.held_out == (ticker,)
