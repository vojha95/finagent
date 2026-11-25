"""BM25 per filing and point-in-time search (finagent.retrieval.index; 8.14)."""

from datetime import date, datetime

import pytest
from retrieval_support import DOC, filing

from finagent.config import HeldOutError, load_retrieval_config
from finagent.retrieval.index import (FilingIndex, RetrievalError, Retriever, build_index, fold,
                                      in_sections, rank, tokenize)

CONFIG = load_retrieval_config()
AS_OF = date(2026, 11, 24)
FY2025 = filing(2025, date(2026, 2, 1), "0000000001-26-000001")
FY2026 = filing(2026, date(2027, 2, 1), "0000000001-27-000001")  # filed after AS_OF


class FakeStore:
    """Every filing, whatever the date: the retriever must apply as_of itself."""

    evidence_dir, manifest = None, {}

    def load_filings(self, issuer, as_of):
        return [FY2025, FY2026]


def retriever() -> Retriever:
    r = Retriever(FakeStore(), {"SYNT": "validation", "HOLD": "held_out"}, CONFIG)
    r.indexes[FY2025.accession] = build_index(FY2025, DOC.encode(), CONFIG)
    return r


def test_tokens_fold_plurals_and_drop_stop_words():
    assert tokenize("The Inventories and Receivables of the business", CONFIG.stopwords) == \
        ["inventory", "receivable", "business"]
    assert (fold("process"), fold("status"), fold("basis"), fold("notes")) == \
        ("process", "status", "basis", "note")


def test_section_filter():
    assert in_sections("ITEM_8/NOTE_02", ("ITEM_8",)) and in_sections("ITEM_7", None)
    assert not in_sections("ITEM_7A", ("ITEM_7",))


def test_index_searches_text_chunks_only():
    ix = build_index(FY2025, DOC.encode(), CONFIG)
    assert isinstance(ix, FilingIndex) and ix.bm25 is not None
    assert all(ix.chunks[i].kind == "text" for i in ix.searched)
    hits = rank(ix, "borrowings senior notes", CONFIG, ("ITEM_8",), 5)
    assert hits[0][0].section_id == "ITEM_8/NOTE_02" and hits[0][1] > 0
    assert rank(ix, "borrowings senior notes", CONFIG, ("ITEM_1",), 5) == []  # no overlap
    assert rank(ix, "zebra", CONFIG, None, 5) == []  # zero scores are not hits


def test_search_is_point_in_time_and_cites_its_filing():
    r = retriever()
    hits = r.search("SYNT", 2025, "revenue increased unit sales", as_of=AS_OF,
                    sections=("ITEM_7",), k=3)
    assert hits and hits[0].rank == 1 and hits[0].chunk.section_id == "ITEM_7"
    assert all(h.chunk.accession == FY2025.accession and h.filed <= AS_OF for h in hits)
    assert (hits[0].fiscal_year, hits[0].filing_url) == (2025, FY2025.filing_url)
    assert r.search("SYNT", 2026, "revenue", as_of=AS_OF) == ()  # filed after as_of
    with pytest.raises(HeldOutError):
        r.search("HOLD", 2025, "revenue", as_of=AS_OF)
    with pytest.raises(RetrievalError):
        r.search("SYNT", 2025, "revenue", as_of=datetime(2026, 11, 24))


def test_index_is_cached_by_accession():
    r = retriever()
    assert r.index(FY2025) is r.indexes[FY2025.accession]
