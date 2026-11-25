"""Quoted sentences (finagent.retrieval.quotes; 8.13; #173) and the gate's quote check
(finagent.retrieval.pinned; #157)."""

from dataclasses import replace
from datetime import date

from retrieval_support import EVIDENCE, filing

from finagent.config import load_retrieval_config, load_signal_config
from finagent.records import AnswerObservation
from finagent.retrieval.index import Retriever, build_index, rank
from finagent.retrieval.pinned import PinnedChunks, quote_checker, quote_problem
from finagent.retrieval.quotes import (NO_ANCHOR, NOT_A_SENTENCE, OTHER_YEAR, TOO_LONG,
                                       TOO_SHORT, chunk_sentences, coverage, find_quotes,
                                       idf_weights, names_other_year, quotes_for,
                                       sentence_problem, whole_sentence)
from finagent.retrieval.templates import load_query_templates

CONFIG = load_retrieval_config()
SPEC = load_signal_config().evidence
AS_OF = date(2026, 11, 24)
FY2025 = filing(2025, date(2026, 2, 1), "0000000001-26-000001")
FY2026 = filing(2026, date(2027, 2, 1), "0000000001-27-000001")  # filed after AS_OF
R1 = load_query_templates().signals["R1"]


class FakeStore:
    evidence_dir, manifest = None, {}

    def load_filings(self, issuer, as_of):
        return [FY2025, FY2026]


def index():
    return build_index(FY2025, EVIDENCE.encode(), CONFIG)


def retriever():
    r = Retriever(FakeStore(), {"SYNT": "validation"}, CONFIG)
    r.indexes[FY2025.accession] = index()
    return r


def test_sentence_rules():
    assert whole_sentence("Revenue rose.") and whole_sentence("• Liquidity is ample.")
    assert not whole_sentence("revenue rose.") and not whole_sentence("Liquidity")
    assert names_other_year("Sales rose in 2024 compared with 2023.", 2025)
    assert not names_other_year("Sales rose in 2025 compared with 2024.", 2025)
    assert not names_other_year("Notes due 2030 were issued.", 2025)  # later years allowed
    assert not names_other_year("Sales were $2,024 million.", 2025)
    rule = lambda text: sentence_problem(text, ("receivable",), 2025, SPEC)  # noqa: E731
    assert rule("Accounts receivable rose in 2025 as terms lengthened.") is None
    assert rule("accounts receivable rose in 2025 as terms lengthened.") == NOT_A_SENTENCE
    assert rule("Receivables rose.") == TOO_SHORT
    assert rule("Receivables rose because " + "x " * 60 + "happened.") == TOO_LONG
    assert rule("Inventories rose in 2025 as demand slowed sharply.") == NO_ANCHOR
    assert rule("Accounts receivable rose in 2024 as terms lengthened.") == OTHER_YEAR


def test_coverage_is_the_share_of_query_idf_weight():
    ix = index()
    weights = idf_weights(ix, R1.query, CONFIG)
    assert weights and all(w > 0 for w in weights.values())
    assert coverage("Nothing relevant here.", weights, CONFIG) == 0.0
    full = " ".join(weights)
    assert abs(coverage(full, weights, CONFIG) - 1.0) < 1e-12


def test_chunk_sentences_are_the_chunkers_own():
    ix = index()
    for chunk in ix.chunks:
        sentences = chunk_sentences(ix, chunk, CONFIG)
        if chunk.kind == "text":
            assert " ".join(sentences) == chunk.text
        else:
            assert sentences == ()


def test_find_quotes_picks_whole_sentences_from_allowed_sections():
    ix = index()
    hits = retriever().search("SYNT", 2025, R1.query, as_of=AS_OF, sections=SPEC.sections,
                              k=SPEC.hits)
    found = find_quotes(ix, hits, R1.query, R1.anchors, 2025, replace(SPEC, min_score=0.0),
                        CONFIG)
    assert 1 <= len(found.quotes) <= SPEC.max_quotes
    best = found.quotes[0]
    assert best.text.startswith("Accounts receivable increased 33% in 2025")
    assert best.section == "ITEM_7" and best.accession == FY2025.accession
    chunk = next(c for c in ix.chunks if c.chunk_id == best.chunk_id)
    assert best.text in chunk.text and best.chunk_sha256 == chunk.sha256
    assert all(q.section.startswith(("ITEM_7", "ITEM_8")) for q in found.quotes)
    assert found.skipped.get(TOO_LONG) == 1 and found.skipped.get(OTHER_YEAR) == 1
    assert [c.quote.score for c in found.candidates] == sorted(
        (c.quote.score for c in found.candidates), reverse=True)
    none = find_quotes(ix, hits, R1.query, R1.anchors, 2025, replace(SPEC, min_score=1.0),
                       CONFIG)
    assert none.quotes == () and none.candidates  # nothing above the threshold: a gap


def test_quotes_for_is_point_in_time():
    r = retriever()
    assert quotes_for(r, "SYNT", 2026, R1.query, R1.anchors, SPEC.sections, SPEC,
                      as_of=AS_OF).quotes == ()  # the FY2026 10-K was filed after as_of
    got = quotes_for(r, "SYNT", 2025, R1.query, R1.anchors, SPEC.sections,
                     replace(SPEC, min_score=0.0), as_of=AS_OF)
    assert got.quotes and got.hits[0].filed <= AS_OF


def test_quote_problem_against_the_rebuilt_chunk():
    ix = index()
    chunk = next(c for c in ix.chunks if c.section_id == "ITEM_7" and c.kind == "text")
    text = next(t for t in chunk_sentences(ix, chunk, CONFIG) if t.startswith("Accounts"))
    from finagent.records import Quote

    q = Quote(chunk.chunk_id, chunk.accession, text, chunk.section_id, 0.5, chunk.sha256)
    assert quote_problem(q, chunk, SPEC.sections, SPEC.max_words) is None
    assert quote_problem(q, None, SPEC.sections, 60) == "QUOTE_CHUNK_NOT_FOUND"
    assert quote_problem(replace(q, chunk_sha256="0"), chunk, SPEC.sections, 60) == \
        "QUOTE_CHUNK_CHANGED"
    assert quote_problem(q, replace(chunk, kind="table"), SPEC.sections, 60) == \
        "QUOTE_FROM_TABLE"
    assert quote_problem(q, chunk, ("ITEM_8",), 60) == "QUOTE_SECTION_NOT_ALLOWED"
    assert quote_problem(replace(q, text=text + " Invented."), chunk, SPEC.sections, 60) == \
        "QUOTE_NOT_VERBATIM"
    assert quote_problem(q, chunk, SPEC.sections, 3) == "QUOTE_TOO_LONG"


def test_quote_checker_uses_chunks_rebuilt_apart_from_the_tool():
    ix = index()
    chunk = next(c for c in ix.chunks if c.section_id == "ITEM_7" and c.kind == "text")
    from finagent.records import Quote

    text = next(t for t in chunk_sentences(ix, chunk, CONFIG) if t.startswith("Accounts"))
    q = Quote(chunk.chunk_id, chunk.accession, text, chunk.section_id, 0.5, chunk.sha256)
    pinned = PinnedChunks(FakeStore(), CONFIG)
    pinned.cache[FY2025.accession] = {c.chunk_id: c for c in ix.chunks}  # no file read here
    o = AnswerObservation("SYNT", FY2025.cik, 2025, "R1.dso_change", "days", 6.0,
                          "CALCULATION", FY2025.accession, FY2025.filing_url, FY2025.filed,
                          FY2025.end, "b", True, quotes=(q,))
    assert quote_checker(pinned, SPEC.sections, SPEC.max_words)(o, FY2025) is None
    assert quote_checker(pinned, (), SPEC.max_words)(o, FY2025) == "QUOTE_SECTION_NOT_ALLOWED"
    bad = replace(o, quotes=(replace(q, text="Receivables doubled."),))
    assert quote_checker(pinned, SPEC.sections, SPEC.max_words)(bad, FY2025) == \
        "QUOTE_NOT_VERBATIM"
    assert pinned.chunks(FY2025) is pinned.cache[FY2025.accession]
    assert rank(ix, R1.query, CONFIG, ("ITEM_1",), 5)  # Item 1 matches, but is not quotable


def test_stated_change_and_amount_mismatch():
    """#185: the first change phrase (verb, amount and/or percentage) against the computed
    change, with half a displayed unit plus one statement unit of tolerance (BC24)."""
    from decimal import Decimal

    from finagent.retrieval.quotes import ChangeCheck, disagrees, stated_change

    m = Decimal(10) ** 6
    total = ChangeCheck(Decimal(26_709) * m, Decimal(128_528) * m, m)  # +20.8%
    segment = "Operating income increased $12.4 billion or 28%."
    assert stated_change(segment, m).amount == Decimal("12.4") * Decimal(10) ** 9
    assert disagrees(segment, total)
    assert not disagrees("Operating income increased $26.7 billion or 21% on cloud growth.",
                         total)
    assert disagrees("Operating income increased $26.7 billion or 28% on cloud growth.", total)
    assert disagrees("Operating income decreased $26.7 billion on lower sales.", total)
    assert not disagrees("Operating income grew 21 percent on higher volume.", total)
    # No change stated: a level, "increased to", percentage points, no number at all.
    for kept in ("Operating income was $155.2 billion in fiscal 2026.",
                 "Operating income increased to $155,237 in fiscal 2026.",
                 "Operating margin decreased 2.1 percentage points.",
                 "Operating income increased primarily due to cloud growth."):
        assert stated_change(kept, m) is None and not disagrees(kept, total)
    # An amount without a magnitude word is in the statement unit; unknown unit: not read.
    small = ChangeCheck(Decimal(170) * m, Decimal(12_081) * m, m)
    assert not disagrees("Gross profit reflects an increase of $170 from higher sales.", small)
    assert disagrees("Gross profit reflects an increase of $70 from higher sales.", small)
    no_unit = ChangeCheck(Decimal(170) * m, Decimal(12_081) * m, None)
    assert stated_change("an increase of $70 from higher sales.", None) is None
    assert not disagrees("Gross profit reflects an increase of $70 from higher sales.", no_unit)
