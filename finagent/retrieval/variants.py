"""The retrieval variants of 8.14 beside BM25 (Phase 7.4): dense, hybrid_rrf, hybrid_rrf_rerank.

Imported only when a non-BM25 variant is asked for, so the default (bm25) path never loads a
model. SearchModels holds the pinned models (configs/models.yaml), each verified and loaded
offline on first use, and the on-disk embedding cache (finagent.retrieval.dense).

    dense              cosine similarity of the query and chunk embeddings
    hybrid_rrf         RRF (rrf_k) of the full BM25 and dense rankings of the section's chunks,
                       best fusion top_k
    hybrid_rrf_rerank  the hybrid_rrf list re-scored by the cross-encoder, best `keep`
"""

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from finagent.config import CONFIG_DIR, ModelPin, SearchSpec, load_model_pins
from finagent.records import Chunk
from finagent.retrieval.dense import (DenseStats, Embedder, EmbeddingCache, chunk_vectors,
                                      dense_rank)
from finagent.retrieval.index import FilingIndex, rank
from finagent.retrieval.rerank import Reranker, rerank, rrf

ROOT = CONFIG_DIR.parent


def _load_embedder(pin: ModelPin):  # indirection so tests never load torch
    from finagent.retrieval.models import load_embedder
    return load_embedder(pin)


def _load_cross_encoder(pin: ModelPin):
    from finagent.retrieval.models import load_cross_encoder
    return load_cross_encoder(pin)


@dataclass
class SearchModels:
    """The models of the non-BM25 variants, loaded (verified, offline) on first use."""

    spec: SearchSpec
    pins: Mapping[str, ModelPin] = field(default_factory=dict)
    cache: EmbeddingCache | None = None
    stats: DenseStats = field(default_factory=DenseStats)
    load_seconds: dict[str, float] = field(default_factory=dict)
    embed_seconds: float = 0.0
    load_embedder: Callable = _load_embedder
    load_cross_encoder: Callable = _load_cross_encoder
    _embedder: Embedder | None = None
    _reranker: Reranker | None = None

    @classmethod
    def from_config(cls, spec: SearchSpec) -> "SearchModels":
        return cls(spec, load_model_pins(), EmbeddingCache(ROOT / spec.cache_dir))

    def embedder(self) -> Embedder:
        if self._embedder is None:
            pin = self.pins[self.spec.embedder]
            start = time.perf_counter()
            self._embedder = Embedder(self.load_embedder(pin), pin.fingerprint,
                                      self.spec.query_instruction, self.spec.batch_size)
            self.load_seconds["embedder"] = time.perf_counter() - start
        return self._embedder

    def reranker(self) -> Reranker:
        if self._reranker is None:
            start = time.perf_counter()
            self._reranker = Reranker(self.load_cross_encoder(self.pins[self.spec.reranker]),
                                      self.spec.batch_size)
            self.load_seconds["reranker"] = time.perf_counter() - start
        return self._reranker

    def vectors(self, index: FilingIndex, known: dict[str, np.ndarray]) -> np.ndarray:
        """The filing's chunk vectors (memory, then disk cache, then the embedder)."""
        accession = index.filing.accession
        if accession not in known:
            embedder = self.embedder()  # its load time is counted in load_seconds
            start = time.perf_counter()
            known[accession] = chunk_vectors(index, embedder, self.cache, self.stats)
            self.embed_seconds += time.perf_counter() - start
        return known[accession]


def rank_variant(index: FilingIndex, query: str, variant: str, sections: Sequence[str] | None,
                 k: int, models: SearchModels, known: dict[str, np.ndarray],
                 config) -> list[tuple[Chunk, float]]:
    """The k best text chunks of one filing for a non-BM25 variant (module docstring)."""
    spec = models.spec
    vectors = models.vectors(index, known)
    query_vector = models.embedder().query(query)
    every = len(index.searched)
    dense = dense_rank(index, vectors, query_vector, sections, every)
    if variant == "dense":
        return dense[:k]
    order = {c.chunk_id: n for n, c in enumerate(index.chunks)}
    sparse = rank(index, query, config, sections, every)
    fused = rrf([[c for c, _ in sparse], [c for c, _ in dense]], spec.rrf_k,
                spec.fusion_top_k, order)
    if variant == "hybrid_rrf":
        return fused[:k]
    if variant == "hybrid_rrf_rerank":
        return rerank(models.reranker(), query, fused, min(k, spec.rerank_keep))
    raise ValueError(f"unknown retrieval variant {variant!r}")

