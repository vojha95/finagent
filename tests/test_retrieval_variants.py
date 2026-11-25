"""Retrieval variants through the Retriever (7.4): dense, hybrid_rrf, hybrid_rrf_rerank beside
the default bm25; point in time; never a model on the default path."""

from dataclasses import replace
from datetime import date

import numpy as np
import pytest
from retrieval_support import DOC, EVIDENCE, filing
from variant_support import FINGERPRINT, FakeCrossEncoder, FakeEncoder

from finagent.config import (RETRIEVAL_VARIANTS, ConfigError, ModelPin, SearchSpec,
                             load_retrieval_config, load_signal_config)
from finagent.config import _search_spec as search_spec
from finagent.retrieval.dense import Embedder, EmbeddingCache, dense_rank
from finagent.retrieval.index import RetrievalError, Retriever, build_index, rank
from finagent.retrieval.quotes import quotes_for
from finagent.retrieval.rerank import Reranker, rrf
from finagent.retrieval.variants import SearchModels, rank_variant

CONFIG = load_retrieval_config()
AS_OF = date(2026, 11, 24)
FY2025 = filing(2025, date(2026, 2, 1), "0000000001-26-000001")
FY2026 = filing(2026, date(2027, 2, 1), "0000000001-27-000001")  # filed after AS_OF


class FakeStore:
    evidence_dir, manifest = None, {}

    def load_filings(self, issuer, as_of):
        return [FY2025, FY2026]


def fake_models(tmp_path, spec: SearchSpec = CONFIG.search) -> SearchModels:
    return SearchModels(spec, {}, EmbeddingCache(tmp_path),
                        _embedder=Embedder(FakeEncoder(), FINGERPRINT, spec.query_instruction),
                        _reranker=Reranker(FakeCrossEncoder()))


def retriever(tmp_path, document: str = DOC, models=True) -> Retriever:
    r = Retriever(FakeStore(), {"SYNT": "validation", "HOLD": "held_out"}, CONFIG)
    r.indexes[FY2025.accession] = build_index(FY2025, document.encode(), CONFIG)
    if models:
        r.models = fake_models(tmp_path)
    return r


def test_search_config_keeps_bm25_the_default():
    s = CONFIG.search
    assert s.default_variant == "bm25" and RETRIEVAL_VARIANTS[0] == "bm25"
    assert (s.rrf_k, s.fusion_top_k, s.rerank_keep) == (60, 20, 3)  # 8.14
    assert (s.embedder, s.reranker) == ("embedder", "reranker")
    assert s.query_instruction.startswith("Represent this sentence")
    assert search_spec(None) == SearchSpec()
    raw = {"default_variant": "bm25", "dense": {"model": "embedder", "batch_size": 8,
                                                 "cache_dir": "x"},
           "fusion": {"rrf_k": 60, "top_k": 20}, "rerank": {"model": "reranker", "keep": 3}}
    assert search_spec(raw).batch_size == 8
    with pytest.raises(ConfigError):
        search_spec({**raw, "default_variant": "magic"})
    with pytest.raises(ConfigError):
        search_spec({**raw, "rerank": {"model": "reranker", "keep": 0}})


def test_default_search_is_bm25_and_builds_no_model(tmp_path):
    r = retriever(tmp_path, models=False)
    hits = r.search("SYNT", 2025, "borrowings senior notes", as_of=AS_OF, k=3)
    assert hits and all(h.variant == "bm25" for h in hits) and r.models is None
    assert [h.chunk for h in hits] == [c for c, _ in rank(r.index(FY2025),
                                                          "borrowings senior notes", CONFIG,
                                                          None, 3)]
    with pytest.raises(RetrievalError):
        r.search("SYNT", 2025, "revenue", as_of=AS_OF, variant="magic")
    r.variant = "magic"
    with pytest.raises(RetrievalError):
        r.search("SYNT", 2025, "revenue", as_of=AS_OF)


@pytest.mark.parametrize("variant", ["dense", "hybrid_rrf", "hybrid_rrf_rerank"])
def test_variants_are_point_in_time_and_cite_their_filing(tmp_path, variant):
    r = retriever(tmp_path)
    hits = r.search("SYNT", 2025, "borrowings senior notes", as_of=AS_OF, sections=("ITEM_8",),
                    k=5, variant=variant)
    assert hits and all(h.variant == variant for h in hits)
    assert all(h.chunk.accession == FY2025.accession and h.filed <= AS_OF for h in hits)
    assert all(h.chunk.section_id.startswith("ITEM_8") for h in hits)
    assert [h.rank for h in hits] == list(range(1, len(hits) + 1))
    assert r.search("SYNT", 2026, "revenue", as_of=AS_OF, variant=variant) == ()
    from finagent.config import HeldOutError
    with pytest.raises(HeldOutError):
        r.search("HOLD", 2025, "revenue", as_of=AS_OF, variant=variant)


def test_hybrid_fuses_full_bm25_and_dense_rankings_and_rerank_keeps_three(tmp_path):
    r = retriever(tmp_path)
    index, query, models = r.index(FY2025), "revenue increased unit sales", r.models
    every = len(index.searched)
    dense = dense_rank(index, models.vectors(index, r.vectors),
                       models.embedder().query(query), None, every)
    sparse = rank(index, query, CONFIG, None, every)
    order = {c.chunk_id: n for n, c in enumerate(index.chunks)}
    fused = rrf([[c for c, _ in sparse], [c for c, _ in dense]], 60, 20, order)
    got = rank_variant(index, query, "hybrid_rrf", None, 4, models, r.vectors, CONFIG)
    assert got == fused[:4]
    assert rank_variant(index, query, "dense", None, 4, models, r.vectors, CONFIG) == dense[:4]
    kept = rank_variant(index, query, "hybrid_rrf_rerank", None, 5, models, r.vectors, CONFIG)
    assert len(kept) == min(3, len(fused))  # 8.14: keep top 3
    assert {c.chunk_id for c, _ in kept} <= {c.chunk_id for c, _ in fused}
    assert models.reranker().model.pairs[0][0] == query
    with pytest.raises(ValueError):
        rank_variant(index, query, "bm25", None, 4, models, r.vectors, CONFIG)


def test_search_models_embed_once_and_time_loading(tmp_path):
    loads = []
    pin = ModelPin("embedder", "tiny", "org/tiny", "a" * 40, tmp_path, {"x": ("b" * 64, 1)})
    spec = replace(CONFIG.search, query_instruction="")
    models = SearchModels(spec, {"embedder": pin, "reranker": pin}, EmbeddingCache(tmp_path),
                          load_embedder=lambda p: loads.append(p.key) or FakeEncoder(),
                          load_cross_encoder=lambda p: loads.append("cross") or
                          FakeCrossEncoder())
    index = build_index(FY2025, DOC.encode(), CONFIG)
    known: dict[str, np.ndarray] = {}
    first = models.vectors(index, known)
    assert models.vectors(index, known) is first and loads == ["embedder"]
    assert models.stats.embedded > 0 and set(models.load_seconds) == {"embedder"}
    assert models.embedder().fingerprint == pin.fingerprint
    models.reranker()
    models.reranker()
    assert loads == ["embedder", "cross"] and "reranker" in models.load_seconds
    fresh = SearchModels(spec, models.pins, EmbeddingCache(tmp_path),
                         load_embedder=lambda p: FakeEncoder())
    assert np.array_equal(fresh.vectors(index, {}), first)
    assert (fresh.stats.embedded, fresh.stats.cached) == (0, models.stats.embedded)


def test_quotes_for_passes_the_variant_through(tmp_path):
    r = retriever(tmp_path, EVIDENCE)
    spec = load_signal_config().evidence
    query = "accounts receivable increased customer payment terms"
    for variant in (None, "dense", "hybrid_rrf_rerank"):
        found = quotes_for(r, "SYNT", 2025, query, ("receivable",), ("ITEM_7",), spec,
                           as_of=AS_OF, variant=variant)
        assert found.hits and {h.variant for h in found.hits} == {variant or "bm25"}
        assert found.quotes[0].text.startswith("Accounts receivable increased 33% in 2025")
