"""Comparing an amount with a value shown or declared at a coarser precision.

Filers round ties either way, so both half-up and half-even are accepted. Shared by the tag
channel's consistent-duplicates test (8.6) and dual-channel verification (8.7).
"""

from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

TIE_RULES = (ROUND_HALF_UP, ROUND_HALF_EVEN)


def rounds_to(exact: Decimal, shown: Decimal, exponent: int) -> bool:
    """True when exact, rounded to 10**exponent with either tie rule, equals shown.

    shown is compared as it is, never rounded: rounds_to(Decimal(2_065_000_000),
    Decimal("2.07E+9"), 7) is True (half up); rounds_to(Decimal(100), Decimal(99), 6) is False.
    """
    step = Decimal(1).scaleb(exponent)
    return any(exact.quantize(step, rounding=rule) == shown for rule in TIE_RULES)
