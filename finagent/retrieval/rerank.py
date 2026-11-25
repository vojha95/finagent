"""Fusion and reranking (blueprint 8.14; Phase 7.4).

rrf: reciprocal rank fusion of several rankings of the same filing's chunks: a chunk's score is
the sum over the rankings of 1 / (k + its rank there), rank 1 = best (k = 60 in 8.14); a chunk
absent from a ranking adds nothing. The best `top` are kept, ties by document order.

rerank: the cross-encoder (ms-marco-MiniLM-L-6-v2, pinned and loaded offline by
finagent.retrieval.models) scores each (query, chunk text) pair together; the best `keep` are
kept (8.14: keep top 3), ties by the incoming (fused) order.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from finagent.records import Chunk


def rrf(rankings: Sequence[Sequence[Chunk]], k: int, top: int,
        order: Mapping[str, int]) -> list[tuple[Chunk, float]]:
    """Reciprocal rank fusion (module docstring); order: chunk_id -> document position."""
    scores: dict[str, float] = defaultdict(float)
    chunks: dict[str, Chunk] = {}
    for ranking in rankings:
        for position, chunk in enumerate(ranking, start=1):
            scores[chunk.chunk_id] += 1.0 / (k + position)
            chunks[chunk.chunk_id] = chunk
    best = sorted(scores, key=lambda cid: (-scores[cid], order[cid]))[:top]
    return [(chunks[cid], scores[cid]) for cid in best]


@dataclass
class Reranker:
    """A sentence-transformers style cross-encoder (`predict` on (query, passage) pairs)."""

    model: Any
    batch_size: int = 32

    def scores(self, query: str, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        found = self.model.predict([(query, t) for t in texts], batch_size=self.batch_size,
                                   show_progress_bar=False)
        return [float(s) for s in found]


def rerank(reranker: Reranker, query: str, ranked: Sequence[tuple[Chunk, float]],
           keep: int) -> list[tuple[Chunk, float]]:
    """The `keep` best of the ranked chunks by cross-encoder score; ties by incoming order."""
    scores = reranker.scores(query, [chunk.text for chunk, _ in ranked])
    best = sorted(range(len(ranked)), key=lambda i: (-scores[i], i))[:keep]
    return [(ranked[i][0], scores[i]) for i in best]
