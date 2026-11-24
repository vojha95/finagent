"""Planner text helpers: normalisation and whole-word phrase matching."""

from finagent.planner.text import find_phrases, normalise, phrase_pattern, remove_spans


def test_normalise_lowers_straightens_and_drops_end_punctuation():
    assert normalise("  What was Amazon’s  Revenue — FY2025?! ") == \
        "what was amazon's revenue - fy2025"


def test_phrase_pattern_is_whole_word_and_spans_whitespace():
    p = phrase_pattern("net income")
    assert p.search("the net   income of") and not p.search("net incomes")
    assert not phrase_pattern("t").search("at&t")  # '&' counts as part of a word


def test_find_phrases_longest_first_without_overlap_in_text_order():
    found = find_phrases("severe stress and net income", ["stress", "severe stress", "income",
                                                          "net income"])
    assert [p for p, _, _ in found] == ["severe stress", "net income"]
    assert found[0][1] == 0


def test_find_phrases_empty():
    assert find_phrases("anything", []) == []


def test_remove_spans_single_spaces_the_rest():
    assert remove_spans("show net income for fy", [(5, 15)]) == "show for fy"
    assert remove_spans("a b c", [(2, 3)], fill="X") == "a X c"
