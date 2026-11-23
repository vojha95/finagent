from datetime import date
from decimal import Decimal

from finagent.config import LineItemSpec
from finagent.document.channel import (
    FOUND,
    NO_PERIOD_COLUMN,
    NOT_FOUND,
    DocumentReading,
    primary_statement,
    read_document_channel,
)
from finagent.evidence.store import FilingRecord
from finagent.records import Outcome, Prediction, Reading, RowInstance
from finagent.verify import (
    AGREE,
    DOCUMENT_ONLY,
    NEITHER,
    TAG_ONLY,
    VALUES_DIFFER,
    tag_value,
    verify_filing,
    verify_item,
    within_precision,
)

END = date(2025, 12, 31)
FILING = FilingRecord(
    issuer="SYNT", cik="0000999999", form="10-K", fiscal_year=2025, start=date(2025, 1, 1),
    start_classification="CALCULATION", end=END, filed=date(2026, 2, 1),
    accession="0000999999-26-000001", primary_document="s.htm", filing_url="u",
)


def _spec(item, statement="IS", sign="either"):
    return LineItemSpec(item, item, statement, "duration", sign, True, ("us-gaap:X",))


def _row(row_id, label, statement="IS", text="1,000", position=0, periods=(END,)):
    return RowInstance(
        row_id=row_id, accession=FILING.accession, statement=statement, label=label,
        parent_header=None, indent=0, is_total=False, is_bold=False, position=position,
        cell_texts=(text,) * len(periods), column_periods=periods, unit_text="(in millions)",
        table_index=0, source_row=position, column_spans=((1, 1),) * len(periods),
    )


def _prediction(row_id, item, p=1.0):
    return Prediction(row_id, ((item, p),), "test")


def _tag(status, value, reason="CORROBORATED"):
    reading = Reading("SYNT", 2025, "net_income", value, "tag", status, concept="us-gaap:X")
    return Outcome(status=status, reason=reason, observations=(reading,))


# ---------- document channel ----------

def test_primary_statement():
    assert primary_statement(_spec("net_income", "IS, CF")) == "IS"


def test_reading_prefers_primary_statement_then_probability():
    rows = [
        _row("cf", "Net income", "CF", "9", 0),
        _row("is_low", "Net income", "IS", "7", 1),
        _row("is_high", "Net income", "IS", "8", 2),
    ]
    predictions = [
        _prediction("cf", "net_income"), _prediction("is_low", "net_income", 0.6),
        _prediction("is_high", "net_income", 0.9),
    ]
    reading = read_document_channel(rows, predictions, FILING, [_spec("net_income", "IS, CF")])[
        "net_income"
    ]
    assert (reading.row_id, reading.value, reading.reason) == ("is_high", Decimal("8E+6"), FOUND)
    assert reading.alternatives == ("is_low", "cf")


def test_reading_gaps():
    specs = [_spec("revenue"), _spec("net_income")]
    rows = [_row("r", "Net income", periods=(date(2024, 12, 31),))]
    readings = read_document_channel(rows, [_prediction("r", "net_income")], FILING, specs)
    assert readings["revenue"].reason == NOT_FOUND and readings["revenue"].value is None
    assert readings["net_income"].reason == NO_PERIOD_COLUMN


def test_reading_blank_cell_is_a_gap_not_zero():
    rows = [_row("r", "Net income", text="")]
    reading = read_document_channel(rows, [_prediction("r", "net_income")], FILING,
                                    [_spec("net_income")])["net_income"]
    assert reading.value is None and reading.reason == "BLANK"


def test_positive_item_reading_is_a_magnitude():
    rows = [_row("r", "Purchases of property and equipment", "CF", "(131,819)")]
    reading = read_document_channel(rows, [_prediction("r", "capex")], FILING,
                                    [_spec("capex", "CF", "positive")])["capex"]
    assert reading.value == Decimal("131819E+6") and reading.sign_flipped


# ---------- verification ----------

def _doc(value, scale=6, reason=FOUND):
    return DocumentReading("net_income", END, value, reason, row_id="r", scale=scale)


def test_within_precision():
    assert within_precision(Decimal("4791E+6"), 6, Decimal("4791000000"))
    assert within_precision(Decimal("4791E+6"), 6, Decimal("4790600000"))  # rounds to 4,791
    assert not within_precision(Decimal("4791E+6"), 6, Decimal("4792000000"))
    assert not within_precision(Decimal("5"), 0, Decimal("6"))


def test_tag_value_only_from_usable_statuses():
    assert tag_value(_tag("VERIFIED", Decimal(1))) == 1
    assert tag_value(_tag("MISSING_PRIMARY", Decimal(1), "INLINE_ONLY")) == 1
    assert tag_value(_tag("CONFLICT", Decimal(1))) is None
    assert tag_value(None) is None


def test_verify_table_of_outcomes():
    value = Decimal("4791E+6")
    agree = verify_item(FILING, _doc(value), _tag("VERIFIED", Decimal("4791000000")))
    assert (agree.status, agree.reason) == ("VERIFIED", AGREE)
    assert {r.channel for r in agree.observations} == {"document", "tag"}

    differ = verify_item(FILING, _doc(-value), _tag("VERIFIED", Decimal("4791000000")))
    assert (differ.status, differ.reason) == ("DISAGREE", VALUES_DIFFER)

    doc_only = verify_item(FILING, _doc(value), _tag("GAP", None, "NOT_FOUND"))
    assert (doc_only.status, doc_only.reason) == ("SINGLE_CHANNEL", DOCUMENT_ONLY)
    assert doc_only.observations[0].single_source

    tag_only = verify_item(FILING, _doc(None, reason=NOT_FOUND), _tag("VERIFIED", value))
    assert (tag_only.status, tag_only.reason) == ("SINGLE_CHANNEL", TAG_ONLY)

    gap = verify_item(FILING, _doc(None, reason=NOT_FOUND), None)
    assert (gap.status, gap.reason) == ("GAP", NEITHER) and gap.observations == ()


def test_evaluation_mode_hides_tags():
    outcome = verify_item(FILING, _doc(Decimal("1E+6")), _tag("VERIFIED", Decimal("2E+6")),
                          mode="evaluation")
    assert outcome.status == "SINGLE_CHANNEL"
    assert all(r.channel == "document" for r in outcome.observations)


def test_verify_filing_covers_every_document_item():
    outcomes = verify_filing(FILING, {"net_income": _doc(Decimal("1E+6"))}, {})
    assert set(outcomes) == {"net_income"}
