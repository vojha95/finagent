from decimal import Decimal as D

from finagent.precision import rounds_to


def test_rounds_to_accepts_either_tie_rule():
    assert rounds_to(D(2_065_000_000), D("2.07E+9"), 7)  # half up
    assert rounds_to(D(2_065_000_000), D("2.06E+9"), 7)  # half even
    assert not rounds_to(D(2_065_000_000), D("2.08E+9"), 7)


def test_rounds_to_away_from_ties():
    assert rounds_to(D(4_790_600_000), D("4791E+6"), 6)
    assert not rounds_to(D(4_792_000_000), D("4791E+6"), 6)
    assert rounds_to(D("0.035"), D("0.04"), -2) and not rounds_to(D("0.036"), D("0.03"), -2)


def test_rounds_to_never_rounds_the_shown_value():
    assert not rounds_to(D(100), D(99), 6)  # 99 is not a value at the 10**6 step
