"""A.2 model-input facts: periods, dimensions, face-row disambiguation, display check."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from statements_support import END, FILING, START, fact, input_spec, millions, row

from finagent.statements.model_inputs import (
    DIFFER,
    MATCH,
    NO_CELL,
    NOT_ON_FACE,
    display_check,
    face_row_resolution,
    input_period,
    matching_cells,
    on_face,
    read_model_input,
)
from finagent.tags.companyfacts import Period
from finagent.records import Outcome, Reading

OPENING_END = date(2024, 12, 31)
RE_ROW = row(0, "Retained earnings", texts=("100", "90"))
RE_MEMBER = (("us-gaap:StatementEquityComponentsAxis", "us-gaap:RetainedEarningsMember"),)


def _cf(concept, *entries):
    """Company Facts with (end, start, value) observations for one us-gaap concept."""
    rows = []
    for end, start, value in entries:
        raw = {"form": "10-K", "accn": FILING.accession, "filed": FILING.filed.isoformat(),
               "end": end.isoformat(), "val": value}
        if start:
            raw["start"] = start.isoformat()
        rows.append(raw)
    return {"facts": {"us-gaap": {concept.split(":")[1]: {"units": {"USD": rows}}}}}


def test_input_period():
    assert input_period(FILING, input_spec(period="instant")) == Period(None, END)
    assert input_period(FILING, input_spec(period="duration")) == Period(START, END)
    assert input_period(FILING, input_spec(period="opening_instant")) == Period(None, OPENING_END)
    assert input_period(replace(FILING, start=None), input_spec(period="opening_instant")) is None


def test_closing_and_opening_instants_are_tag_only_with_display_check():
    companyfacts = _cf("us-gaap:RE", (END, None, 100_000_000), (OPENING_END, None, 90_000_000))
    facts = [fact("us-gaap:RE", millions(100), cell=(1, 0, 3)),
             fact("us-gaap:RE", millions(90), end=OPENING_END, cell=(1, 0, 9))]
    closing = read_model_input(FILING, input_spec(), companyfacts, facts, [RE_ROW])
    assert (closing.status, closing.reason, closing.accepted) == ("SINGLE_CHANNEL", "TAG_ONLY",
                                                                  True)
    assert closing.value == millions(100) and closing.display_check == MATCH
    assert closing.channels == ("tag",) and closing.classification == "REPORTED_FACT"
    opening = read_model_input(FILING, input_spec("re_open", "opening_instant"), companyfacts,
                               facts, [RE_ROW])
    assert opening.value == millions(90) and opening.display_check == MATCH
    assert opening.fiscal_period_end == OPENING_END


def test_opening_instant_without_start_is_start_unknown():
    o = read_model_input(replace(FILING, start=None), input_spec(period="opening_instant"),
                         {}, [], [])
    assert (o.status, o.reason, o.value) == ("GAP", "START_UNKNOWN", None)


def test_dimensional_fact_is_inline_only_and_flagged():
    spec = input_spec("dividends_re", "duration", ("us-gaap:D",), RE_MEMBER, "positive")
    facts = [fact("us-gaap:D", millions(4), start=START, dims=dict(RE_MEMBER))]
    o = read_model_input(FILING, spec, {}, facts, [])
    assert o.value == millions(4)
    assert {"TAG_ONLY", "DIMENSIONAL", "INLINE_ONLY"} <= set(o.flags)
    assert o.display_check == NO_CELL


def test_several_values_keep_the_face_row_fact():
    spec = input_spec("debt", "instant", ("us-gaap:A", "us-gaap:B"))
    companyfacts = _cf("us-gaap:A", (END, None, 100_000_000))
    companyfacts["facts"]["us-gaap"]["B"] = _cf("us-gaap:B", (END, None, 7_000_000))[
        "facts"]["us-gaap"]["B"]
    facts = [fact("us-gaap:A", millions(100), cell=(1, 0, 3)),  # on the face row
             fact("us-gaap:B", millions(7), cell=(9, 4, 2))]  # in a note table
    o = read_model_input(FILING, spec, companyfacts, facts, [RE_ROW])
    assert (o.status, o.value) == ("SINGLE_CHANNEL", millions(100))
    assert "FACE_ROW_RESOLVED" in o.flags and o.candidates == (millions(7),)
    assert o.concepts == ("us-gaap:A",)


def test_several_values_on_face_rows_are_a_conflict():
    spec = input_spec("debt", "instant", ("us-gaap:A",))
    second = row(1, "Other", texts=("7", "1"))
    outcome = Outcome("CONFLICT", "MULTIPLE_VALUES_PRIMARY", tuple(
        Reading("SYNT", 2025, "debt", v, "tag", "CONFLICT", concept="us-gaap:A")
        for v in (millions(100), millions(7))))
    facts = [fact("us-gaap:A", millions(100), cell=(1, 0, 3)),
             fact("us-gaap:A", millions(7), cell=(1, 1, 3))]
    resolution = face_row_resolution(outcome, facts, FILING, spec, Period(None, END),
                                     [RE_ROW, second])
    assert resolution.amount is None and len(resolution.kept) == 2


def test_not_tagged_is_a_gap():
    o = read_model_input(FILING, input_spec(), {}, [], [])
    assert (o.status, o.reason, o.value, o.accepted) == ("GAP", "NEITHER", None, False)


def test_display_check_outcomes():
    shown = fact("us-gaap:RE", millions(100), cell=(1, 0, 3))
    assert display_check([shown], [RE_ROW], millions(100)) == MATCH
    assert display_check([shown], [RE_ROW], millions(120)) == DIFFER
    note = fact("us-gaap:RE", millions(100), cell=(9, 0, 3))
    assert display_check([note], [RE_ROW], millions(100)) == NOT_ON_FACE
    assert display_check([fact("us-gaap:RE", millions(100))], [RE_ROW], millions(100)) == NO_CELL
    assert on_face([shown], [RE_ROW]) and not on_face([note], [RE_ROW])


def test_matching_cells_filters_amount_and_period():
    spec = input_spec()
    facts = [fact("us-gaap:RE", millions(100)), fact("us-gaap:RE", millions(5)),
             fact("us-gaap:RE", millions(100), end=OPENING_END)]
    found = matching_cells(facts, FILING, spec, Period(None, END), millions(100))
    assert [f.amount for f in found] == [Decimal(100_000_000)]
