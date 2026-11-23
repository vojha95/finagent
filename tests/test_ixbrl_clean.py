from pathlib import Path

import pytest
from lxml import etree

from finagent.evidence.integrity import load_manifest, read_snapshot
from finagent.ixbrl import (
    build_tag_store,
    clean_document,
    declared_prefixes,
    find_leaks,
    is_camel_compound,
    ix_concept_names,
    leak_terms,
    parse_document,
    table_grid,
)
from finagent.ixbrl.tagstore import IX

EVIDENCE_DIR = Path(__file__).resolve().parents[1] / "evidence"
XHTML = "{http://www.w3.org/1999/xhtml}"

DOC = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
 xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
 xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"
 xmlns:us-gaap="http://fasb.org/us-gaap/2025" xmlns:srt="http://fasb.org/srt/2025"
 xmlns:acme="http://acme.example/2025" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<body>
<div style="display:none"><ix:header><ix:hidden>
<ix:nonFraction name="acme:HiddenFact" contextRef="d" unitRef="usd" scale="0" id="h1">5</ix:nonFraction>
</ix:hidden><ix:resources>
<xbrli:context id="d"><xbrli:entity><xbrli:identifier scheme="s">1</xbrli:identifier></xbrli:entity>
<xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="s"><xbrli:entity><xbrli:identifier scheme="s">1</xbrli:identifier>
<xbrli:segment><xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">us-gaap:ServiceMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
<xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
</ix:resources></ix:header>AFTER-HEADER</div>
<p>Policy: <ix:nonNumeric name="us-gaap:RevenueRecognitionPolicyTextBlock" contextRef="d" continuedAt="c1">We recognise</ix:nonNumeric> revenue.</p>
<p><ix:continuation id="c1">when control passes.</ix:continuation></p>
<p style="-sec-ix-hidden:h1;font-weight:bold">Shown</p>
<p style="-sec-ix-hidden:h1">Only</p>
<table>
<tr><td colspan="2">Header</td><td>2025</td></tr>
<tr><td>Net income</td><td>$</td>
<td>(<ix:nonFraction name="us-gaap:NetIncomeLoss" contextRef="d" unitRef="usd" scale="6"
 format="ixt:num-dot-decimal" id="f1">1,234</ix:nonFraction>)</td></tr>
<tr><td>Service</td><td>$</td>
<td><ix:nonFraction name="us-gaap:Revenues" contextRef="s" unitRef="usd" scale="6"
 format="ixt:num-dot-decimal" id="f2"><ix:nonFraction name="acme:Inner" contextRef="d" unitRef="usd" scale="6" id="f3">500</ix:nonFraction></ix:nonFraction></td></tr>
</table>
</body></html>"""


@pytest.fixture(scope="module")
def clean() -> bytes:
    return clean_document(DOC.encode())


def _texts(root: etree._Element, tag: str) -> list[str]:
    return ["".join(e.itertext()) for e in root.iter(f"{XHTML}{tag}")]


def _leak_check(document: bytes) -> list[str]:
    return find_leaks(
        clean_document(document),
        leak_terms(build_tag_store(document, "accession")) | ix_concept_names(document),
        declared_prefixes(document),
    )


# ---------- clean_document ----------

def test_no_ix_elements_or_hidden_facts_remain(clean):
    root = parse_document(clean)
    assert not [e for e in root.iter() if isinstance(e.tag, str) and e.tag.startswith(f"{{{IX}}}")]
    assert b"ix:" not in clean and b"us-gaap:" not in clean and b"xbrli" not in clean
    assert b"2025-01-01" not in clean  # contexts went with the header


def test_tail_after_deleted_header_is_kept(clean):
    root = parse_document(clean)
    hidden_div = next(e for e in root.iter(f"{XHTML}div") if e.get("style") == "display:none")
    assert "".join(hidden_div.itertext()).strip() == "AFTER-HEADER"


def test_text_kept_in_order(clean):
    root = parse_document(clean)
    paragraphs = _texts(root, "p")
    assert paragraphs[:2] == ["Policy: We recognise revenue.", "when control passes."]
    cells = _texts(root, "td")
    assert "(1,234)" in cells and "500" in cells  # parentheses and nested facts survive


def test_sec_ix_style_removed_but_element_kept(clean):
    root = parse_document(clean)
    shown, only = [e for e in root.iter(f"{XHTML}p") if "".join(e.itertext()) in ("Shown", "Only")]
    assert shown.get("style") == "font-weight:bold"
    assert only.get("style") is None


def test_unused_namespace_declarations_removed(clean):
    assert b"xmlns:" not in clean


def test_table_structure_unchanged(clean):
    def grid(document: bytes) -> list[tuple[int, int]]:
        table = next(parse_document(document).iter(f"{XHTML}table"))
        return sorted(table_grid(table).values())

    assert grid(clean) == grid(DOC.encode())


# ---------- vocabulary helpers ----------

def test_declared_prefixes():
    assert declared_prefixes(DOC.encode()) == {
        "ix", "xbrli", "xbrldi", "ixt", "us-gaap", "srt", "acme", "xsi",
    }


def test_leak_terms_include_axes_and_members():
    terms = leak_terms(build_tag_store(DOC.encode(), "a"))
    assert {"us-gaap:NetIncomeLoss", "srt:ProductOrServiceAxis", "us-gaap:ServiceMember"} <= terms


def test_ix_concept_names_include_text_blocks():
    names = ix_concept_names(DOC.encode())
    assert "us-gaap:RevenueRecognitionPolicyTextBlock" in names
    assert {"us-gaap:NetIncomeLoss", "acme:HiddenFact"} <= names


def test_find_leaks_catches_text_block_local_name():
    html = b"<p>RevenueRecognitionPolicyTextBlock</p>"
    leaks = find_leaks(html, ix_concept_names(DOC.encode()), declared_prefixes(DOC.encode()))
    assert leaks == ["local name: RevenueRecognitionPolicyTextBlock"]


@pytest.mark.parametrize(
    "name, expected",
    [("NetIncomeLoss", True), ("EMEAMember", True), ("ProductOrServiceAxis", True),
     ("Revenues", False), ("Goodwill", False), ("Net Income", False)],
)
def test_is_camel_compound(name, expected):
    assert is_camel_compound(name) is expected


# ---------- find_leaks ----------

TERMS = {"us-gaap:NetIncomeLoss", "us-gaap:Revenues", "msft:LinkedInRevenue", "srt:ProductOrServiceAxis"}
PREFIXES = {"us-gaap", "srt", "msft", "dei"}


def test_find_leaks_clean_synthetic(clean):
    assert _leak_check(DOC.encode()) == []


@pytest.mark.parametrize(
    "html, expected",
    [
        ("<p>see us-gaap:Foo</p>", "prefix: us-gaap:"),
        ("<p>dei:DocumentType</p>", "prefix: dei:"),
        ("<p>tag ix:nonFraction</p>", "prefix: ix:"),
        ("<p>msft:LinkedInRevenue</p>", "qualified name: msft:LinkedInRevenue"),
        ("<p>NetIncomeLoss</p>", "local name: NetIncomeLoss"),
        ("<p>by ProductOrServiceAxis.</p>", "local name: ProductOrServiceAxis"),
        ('<p data-c="contextRef">x</p>', "marker: contextRef"),
        ('<p data-u="unitRef">x</p>', "marker: unitRef"),
        ('<p style="-sec-ix-hidden:f1">x</p>', "marker: -sec-ix-"),
        ("<p>xbrli</p>", "marker: xbrli"),
    ],
)
def test_find_leaks_catches(html, expected):
    assert expected in find_leaks(html.encode(), TERMS, PREFIXES)


@pytest.mark.parametrize(
    "html",
    [
        "<p>LinkedIn revenue grew.</p>",           # prose, not the whole token LinkedInRevenue
        "<p>Revenues and Goodwill</p>",           # single-word local names are visible text
        "<p>netincomeloss</p>",                   # case-sensitive
        "<p>Revenue by country: United States</p>",  # undeclared prefix, and space after colon
        "<p>See Appendix: Exhibits</p>",          # 'ix:' inside a word is not the ix prefix
        "<p>Net sales mix:</p>",                  # seen in Amazon 10-Ks
    ],
)
def test_find_leaks_ignores_visible_text(html):
    assert find_leaks(html.encode(), TERMS, PREFIXES) == []


# ---------- real filings ----------

def _filing_bytes(name: str) -> bytes:
    return read_snapshot(EVIDENCE_DIR, name, load_manifest(EVIDENCE_DIR))


def test_pinned_filing_has_no_leaks(pinned_filing):
    document = _filing_bytes(pinned_filing)
    clean = clean_document(document)
    assert _leak_check(document) == []
    assert b"us-gaap:" not in clean
    # A raw b"ix:" search is wrong here: Amazon's visible text says "Net sales mix:".
    root = parse_document(clean)
    assert not [e for e in root.iter() if isinstance(e.tag, str) and e.tag.startswith(f"{{{IX}}}")]


def test_pinned_filing_keeps_visible_text(pinned_filing):
    document = _filing_bytes(pinned_filing)
    original = parse_document(document)
    etree.strip_elements(original, f"{{{IX}}}header", with_tail=False)
    body = next(original.iter(f"{XHTML}body"))
    clean_body = next(parse_document(clean_document(document)).iter(f"{XHTML}body"))
    assert "".join(clean_body.itertext()) == "".join(body.itertext())
