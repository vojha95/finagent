"""Dense retrieval over the same text chunks as BM25 (blueprint 8.14; Phase 7.4).

The embedder (bge-small-en-v1.5, pinned and loaded offline by finagent.retrieval.models) turns
each text chunk of one filing into a unit vector; a query gets the configured instruction
prefix and its own unit vector; similarity is their dot product (cosine), in NumPy. Tables are
not searched, as for BM25.

Embedding cache (on disk): <cache_dir>/<model fingerprint[:16]>/<accession>.npz holds the
model's full fingerprint and, for each text chunk, its sha256 and vector. A cached vector is
reused only for the same chunk text (sha256) and the same model files (fingerprint, the hash of
the pinned files); any other chunk is embedded again and the file rewritten. The cache is
derived data (git-ignored); deleting it only costs time.
"""

import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from finagent.records import Chunk
from finagent.retrieval.index import FilingIndex, in_sections


@dataclass
class Embedder:
    """A sentence-transformers style encoder (`encode`) with its model fingerprint."""

    model: Any
    fingerprint: str
    query_instruction: str = ""
    batch_size: int = 32

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        """Unit vectors, one row per text."""
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        vectors = self.model.encode(list(texts), batch_size=self.batch_size,
                                    normalize_embeddings=True, convert_to_numpy=True,
                                    show_progress_bar=False)
        return np.asarray(vectors, dtype=np.float32)

    def query(self, text: str) -> np.ndarray:
        return self.encode([self.query_instruction + text])[0]


@dataclass
class EmbeddingCache:
    """Chunk vectors on disk, keyed by model fingerprint and chunk sha256 (module docstring)."""

    root: Path
    hits: int = 0  # vectors read from the cache
    misses: int = 0  # vectors embedded

    def path(self, fingerprint: str, accession: str) -> Path:
        return self.root / fingerprint[:16] / f"{accession}.npz"

    def load(self, fingerprint: str, accession: str) -> dict[str, np.ndarray]:
        path = self.path(fingerprint, accession)
        if not path.is_file():
            return {}
        with np.load(path, allow_pickle=False) as data:
            if str(data["fingerprint"]) != fingerprint:
                return {}
            return {str(sha): vector for sha, vector in zip(data["sha256"], data["vectors"])}

    def save(self, fingerprint: str, accession: str, vectors: Mapping[str, np.ndarray]) -> None:
        path = self.path(fingerprint, accession)
        path.parent.mkdir(parents=True, exist_ok=True)
        shas = sorted(vectors)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".npz")
        os.close(fd)
        np.savez(tmp, fingerprint=np.array(fingerprint), sha256=np.array(shas),
                 vectors=np.stack([vectors[s] for s in shas]).astype(np.float32))
        os.replace(tmp, path)  # never a half-written cache file


@dataclass
class DenseStats:
    """What dense retrieval did in one process (for the runtime report)."""

    embedded: int = 0
    cached: int = 0
    filings: list[str] = field(default_factory=list)


def chunk_vectors(index: FilingIndex, embedder: Embedder, cache: EmbeddingCache | None,
                  stats: DenseStats | None = None) -> np.ndarray:
    """The unit vectors of the filing's text chunks, in index.searched order."""
    chunks = [index.chunks[i] for i in index.searched]
    if not chunks:
        return np.zeros((0, 0), dtype=np.float32)
    accession = index.filing.accession
    known = cache.load(embedder.fingerprint, accession) if cache is not None else {}
    missing = list(dict.fromkeys(c.sha256 for c in chunks if c.sha256 not in known))
    if missing:
        text = {c.sha256: c.text for c in chunks}
        known.update(zip(missing, embedder.encode([text[s] for s in missing])))
        if cache is not None:
            cache.save(embedder.fingerprint, accession,
                       {c.sha256: known[c.sha256] for c in chunks})
    if stats is not None:
        stats.embedded += len(missing)
        stats.cached += len({c.sha256 for c in chunks}) - len(missing)
        stats.filings.append(accession)
    return np.stack([known[c.sha256] for c in chunks])


def dense_rank(index: FilingIndex, vectors: np.ndarray, query_vector: np.ndarray,
               sections: Sequence[str] | None, k: int) -> list[tuple[Chunk, float]]:
    """The k text chunks most similar to the query (cosine), in the given sections; ties by
    document order."""
    if not len(vectors):
        return []
    scores = vectors @ query_vector
    scored = [(index.chunks[i], float(s)) for i, s in zip(index.searched, scores)
              if in_sections(index.chunks[i].section_id, sections)]
    order = {c.chunk_id: n for n, c in enumerate(index.chunks)}
    return sorted(scored, key=lambda pair: (-pair[1], order[pair[0].chunk_id]))[:k]
