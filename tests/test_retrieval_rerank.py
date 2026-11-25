"""Reciprocal rank fusion and cross-encoder reranking (7.4; blueprint 8.14)."""

import pytest
from variant_support import FakeCrossEncoder

from finagent.records import Chunk
from finagent.retrieval.rerank import Reranker, rerank, rrf


def chunk(cid: str, text: str = "") -> Chunk:
    text = text or cid
    return Chunk(cid, "acc", "ITEM_7", 0, "text", text, len(text.split()), "0" * 64)


A, B, C, D = (chunk(x) for x in "abcd")
ORDER = {"a": 0, "b": 1, "c": 2, "d": 3}


def test_rrf_sums_reciprocal_ranks():
    fused = rrf([[A, B, C], [C, A]], 60, 20, ORDER)
    assert [c.chunk_id for c, _ in fused] == ["a", "c", "b"]
    scores = dict((c.chunk_id, s) for c, s in fused)
    assert scores["a"] == pytest.approx(1 / 61 + 1 / 62)
    assert scores["c"] == pytest.approx(1 / 63 + 1 / 61)
    assert scores["b"] == pytest.approx(1 / 62)  # absent from the second ranking: adds nothing


def test_rrf_keeps_top_and_breaks_ties_by_document_order():
    fused = rrf([[D, B], [B, D]], 60, 20, ORDER)
    assert [c.chunk_id for c, _ in fused] == ["b", "d"]  # equal scores
    assert len(rrf([[A, B, C, D]], 60, 2, ORDER)) == 2
    assert rrf([], 60, 20, ORDER) == []


def test_rerank_keeps_the_best_by_cross_encoder_score():
    ranked = [(chunk("a", "nothing relevant here"), 0.3),
              (chunk("b", "revenue increased due to unit sales"), 0.2),
              (chunk("c", "revenue fell"), 0.1), (chunk("d", "revenue increased"), 0.05)]
    model = FakeCrossEncoder()
    kept = rerank(Reranker(model, 8), "why revenue increased", ranked, 3)
    assert [c.chunk_id for c, _ in kept] == ["b", "d", "c"]
    assert [s for _, s in kept] == [2.0, 2.0, 1.0]  # b before d: the incoming order
    assert model.pairs[0] == ("why revenue increased", "nothing relevant here")
    assert Reranker(model).scores("q", []) == []
    assert rerank(Reranker(model), "q", [], 3) == []
