"""Status matrix of the validated statement set (8.7 product mode; approved answers 1-2)."""

from decimal import Decimal

from statements_support import FILING, millions, obs, row, spec, tag_only, tag_outcome

from finagent.document.channel import FOUND, NOT_FOUND, DocumentReading
from finagent.statements.verify_set import (
    COMPARATIVE_DOCUMENT_ONLY,
    PENDING,
    STATEMENT_NOT_LOCATED,
    channel_rank,
    observe_comparative,
    observe_item,
    same_amount,
)

ITEM = spec("revenue", "IS")
ROW = row(0, "Total net sales", statement="IS")
ROWS = {ROW.row_id: ROW}


def _doc(value, reason=FOUND):
    return DocumentReading("revenue", FILING.end, value, reason,
                           row_id=ROW.row_id if value is not None else None, scale=6)


def test_verified_uses_the_tag_amount_and_keeps_the_document_value():
    o = observe_item(FILING, ITEM, _doc(millions(4791)), tag_outcome("VERIFIED", ["4790600000"]),
                     ROWS, ("IS",))
    assert (o.status, o.reason, o.accepted, o.validated) == ("VERIFIED", "AGREE", True, True)
    assert o.value == Decimal("4790600000") and o.document_value == millions(4791)
    assert o.classification == "REPORTED_FACT" and o.channels == ("document", "tag")
    assert (o.row_label, o.concepts, o.accession) == ("Total net sales", ("us-gaap:X",),
                                                      FILING.accession)


def test_bc15_agree_with_one_candidate_lists_the_others():
    tag = tag_outcome("CONFLICT", ["5000000", "7000000"], reason="MULTIPLE_VALUES_PRIMARY")
    o = observe_item(FILING, ITEM, _doc(millions(5)), tag, ROWS, ("IS",))
    assert (o.status, o.reason, o.value) == ("VERIFIED", "AGREE_WITH_ONE_CANDIDATE",
                                             Decimal(5000000))
    assert o.candidates == (Decimal(7000000),)


def test_disagree_keeps_both_readings_and_no_value():
    o = observe_item(FILING, ITEM, _doc(millions(10)), tag_outcome("VERIFIED", ["20000000"]),
                     ROWS, ("IS",))
    assert (o.status, o.value, o.accepted) == ("DISAGREE", None, False)
    assert (o.document_value, o.tag_value) == (millions(10), Decimal(20000000))


def test_document_only_is_pending_its_identity_check():
    o = observe_item(FILING, ITEM, _doc(millions(10)), tag_outcome("GAP", [], "NOT_FOUND"),
                     ROWS, ("IS",))
    assert (o.status, o.reason, o.accepted) == ("SINGLE_CHANNEL", "DOCUMENT_ONLY", False)
    assert o.value == millions(10) and PENDING in o.flags and o.channels == ("document",)


def test_tag_only_is_accepted_and_flagged():
    tag = tag_outcome("MISSING_PRIMARY", ["30"], "INLINE_ONLY", source="inline_only")
    o = observe_item(FILING, ITEM, _doc(None, NOT_FOUND), tag, ROWS, ("IS",))
    assert (o.status, o.reason, o.accepted, o.value) == ("SINGLE_CHANNEL", "TAG_ONLY", True,
                                                         Decimal(30))
    assert o.flags == ("TAG_ONLY", "INLINE_ONLY") and o.channels == ("tag",)


def test_gap_reasons_never_zero():
    neither = observe_item(FILING, ITEM, _doc(None, NOT_FOUND), None, ROWS, ("IS",))
    assert (neither.status, neither.reason, neither.value) == ("GAP", "NEITHER", None)
    unlocated = observe_item(FILING, ITEM, _doc(None, NOT_FOUND), None, ROWS, ("BS",))
    assert unlocated.reason == STATEMENT_NOT_LOCATED
    start = observe_item(FILING, ITEM, _doc(None, NOT_FOUND),
                         tag_outcome("GAP", [], "START_UNKNOWN"), ROWS, ("IS",))
    assert start.reason == "START_UNKNOWN"
    conflict = observe_item(FILING, ITEM, _doc(None, NOT_FOUND),
                            tag_outcome("CONFLICT", ["1", "2"], "MULTIPLE_VALUES_PRIMARY"),
                            ROWS, ("IS",))
    assert (conflict.status, conflict.candidates) == ("CONFLICT", (Decimal(1), Decimal(2)))


def test_comparative_document_only_is_not_accepted():
    pending_value = observe_item(FILING, ITEM, _doc(millions(10)), None, ROWS, ("IS",))
    compared = observe_comparative(pending_value)
    assert (compared.accepted, compared.reason) == (False, COMPARATIVE_DOCUMENT_ONLY)
    assert PENDING not in compared.flags
    verified = obs("revenue", 5)
    assert observe_comparative(verified) is verified


def test_channel_rank_and_same_amount():
    verified = obs("revenue", 5_000_000)
    tag = tag_only("revenue", 5_000_000)
    doc = obs("revenue", 5_000_000, status="SINGLE_CHANNEL", channels=("document",),
              reason="DOCUMENT_ONLY", scale=6)
    assert channel_rank(verified) > channel_rank(tag) > channel_rank(doc) > channel_rank(obs("x"))
    assert same_amount(verified, tag)
    assert same_amount(doc, obs("revenue", 5_200_000))  # displayed 5 (millions)
    assert not same_amount(verified, obs("revenue", 5_200_000))
    assert not same_amount(verified, obs("revenue"))
