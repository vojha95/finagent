from datetime import date

import pytest

from finagent.ixbrl import TagParseError, parse_contexts, parse_document, parse_units

HEADER = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
 xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi">
<body><ix:header><ix:resources>{body}</ix:resources></ix:header></body></html>"""


def _root(body: str):
    return parse_document(HEADER.format(body=body).encode())


ENTITY = "<xbrli:entity><xbrli:identifier scheme='x'>1</xbrli:identifier>{seg}</xbrli:entity>"


def _context(cid: str, period: str, seg: str = "") -> str:
    return (
        f"<xbrli:context id='{cid}'>{ENTITY.format(seg=seg)}"
        f"<xbrli:period>{period}</xbrli:period></xbrli:context>"
    )


def test_parse_contexts_duration_instant_and_dimensions():
    seg = (
        "<xbrli:segment>"
        "<xbrldi:explicitMember dimension='srt:SegmentAxis'>amzn:AwsMember</xbrldi:explicitMember>"
        "<xbrldi:typedMember dimension='x:TrancheAxis'><x>A1</x></xbrldi:typedMember>"
        "</xbrli:segment>"
    )
    root = _root(
        _context("d", "<xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate>")
        + _context("i", "<xbrli:instant>2025-12-31</xbrli:instant>", seg)
    )
    contexts = parse_contexts(root)
    assert contexts["d"].start == date(2025, 1, 1)
    assert contexts["d"].end == date(2025, 12, 31)
    assert contexts["d"].dimensions == {}
    assert not contexts["d"].is_instant
    assert contexts["i"].is_instant and contexts["i"].start is None
    assert contexts["i"].end == date(2025, 12, 31)
    assert contexts["i"].dimensions == {"srt:SegmentAxis": "amzn:AwsMember", "x:TrancheAxis": "A1"}


def test_parse_contexts_rejects_missing_period_dates():
    root = _root(_context("bad", "<xbrli:startDate>2025-01-01</xbrli:startDate>"))
    with pytest.raises(TagParseError):
        parse_contexts(root)


def test_parse_units_simple_and_divide():
    root = _root(
        "<xbrli:unit id='usd'><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>"
        "<xbrli:unit id='eps'><xbrli:divide>"
        "<xbrli:unitNumerator><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator>"
        "<xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator>"
        "</xbrli:divide></xbrli:unit>"
    )
    assert parse_units(root) == {"usd": "USD", "eps": "USD/shares"}


def test_parse_document_rejects_malformed_xml():
    with pytest.raises(TagParseError):
        parse_document(b"<html><body></html>")
