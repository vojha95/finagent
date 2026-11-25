"""Dense retrieval: embedder, on-disk cache by model fingerprint and chunk hash, ranking (7.4)."""

from datetime import date

import numpy as np
from retrieval_support import DOC, filing
from variant_support import FINGERPRINT, FakeEncoder

from finagent.config import load_retrieval_config
from finagent.retrieval.dense import (DenseStats, Embedder, EmbeddingCache, chunk_vectors,
                                      dense_rank)
from finagent.retrieval.index import build_index

CONFIG = load_retrieval_config()
FY2025 = filing(2025, date(2026, 2, 1), "0000000001-26-000001")
INDEX = build_index(FY2025, DOC.encode(), CONFIG)


def embedder(fingerprint: str = FINGERPRINT) -> Embedder:
    return Embedder(FakeEncoder(), fingerprint, "Represent: ", 4)


def test_embedder_gives_unit_vectors_and_prefixes_queries():
    e = embedder()
    vectors = e.encode(["revenue increased", "borrowings senior notes"])
    assert vectors.dtype == np.float32 and np.allclose(np.linalg.norm(vectors, axis=1), 1.0)
    assert e.query("revenue").shape == (vectors.shape[1],)
    assert e.model.encoded[-1] == "Represent: revenue"
    assert e.encode([]).shape == (0, 0)


def test_cache_round_trip_is_keyed_by_fingerprint(tmp_path):
    cache = EmbeddingCache(tmp_path)
    vectors = {"s1": np.ones(3, dtype=np.float32), "s2": np.zeros(3, dtype=np.float32)}
    cache.save(FINGERPRINT, "acc-1", vectors)
    assert cache.path(FINGERPRINT, "acc-1") == tmp_path / FINGERPRINT[:16] / "acc-1.npz"
    loaded = cache.load(FINGERPRINT, "acc-1")
    assert set(loaded) == {"s1", "s2"} and np.array_equal(loaded["s1"], vectors["s1"])
    other = FINGERPRINT[:16] + "0" * 48  # same directory, different model files
    assert cache.load(other, "acc-1") == {} and cache.load(FINGERPRINT, "acc-2") == {}
    assert not list(tmp_path.rglob("*.tmp*"))  # written atomically


def test_chunk_vectors_embed_each_chunk_once_then_read_the_cache(tmp_path):
    cache, e, stats = EmbeddingCache(tmp_path), embedder(), DenseStats()
    first = chunk_vectors(INDEX, e, cache, stats)
    texts = [INDEX.chunks[i].text for i in INDEX.searched]
    distinct = len({INDEX.chunks[i].sha256 for i in INDEX.searched})
    assert first.shape[0] == len(INDEX.searched) and stats.embedded == distinct
    assert sorted(e.model.encoded) == sorted(dict.fromkeys(texts))
    again, stats2 = embedder(), DenseStats()
    second = chunk_vectors(INDEX, again, cache, stats2)
    assert np.array_equal(first, second) and again.model.encoded == []
    assert (stats2.embedded, stats2.cached) == (0, distinct)
    changed = embedder("e" * 64)  # other model files: embedded again
    chunk_vectors(INDEX, changed, cache)
    assert len(changed.model.encoded) == distinct
    assert np.array_equal(chunk_vectors(INDEX, embedder(), None), first)  # no cache: same


def test_dense_rank_filters_sections_and_breaks_ties_by_document_order():
    e = embedder()
    vectors = chunk_vectors(INDEX, e, None)
    query = e.encode(["borrowings senior notes"])[0]
    ranked = dense_rank(INDEX, vectors, query, ("ITEM_8",), 5)
    assert ranked and all(c.section_id.startswith("ITEM_8") for c, _ in ranked)
    assert ranked[0][0].section_id == "ITEM_8/NOTE_02"
    assert [s for _, s in ranked] == sorted((s for _, s in ranked), reverse=True)
    flat = dense_rank(INDEX, np.ones_like(vectors), np.zeros(vectors.shape[1], np.float32),
                      None, 50)
    order = [INDEX.chunks.index(c) for c, _ in flat]
    assert order == sorted(order) and len(flat) == len(INDEX.searched)
    assert len(dense_rank(INDEX, vectors, e.query("revenue"), None, 2)) == 2
