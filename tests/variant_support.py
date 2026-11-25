"""Fakes for the 7.4 retrieval variants: a bag-of-words encoder and a word-overlap
cross-encoder with the sentence-transformers call signatures (no model, no torch)."""

import hashlib
import re

import numpy as np

DIM = 256
FINGERPRINT = "f" * 64


def words(text: str) -> list[str]:
    return re.findall(r"[a-z]+", text.lower())


class FakeEncoder:
    """encode(): hashed word counts, unit length when asked; records every text it encodes."""

    def __init__(self) -> None:
        self.encoded: list[str] = []

    def encode(self, texts, batch_size=32, normalize_embeddings=False, convert_to_numpy=True,
               show_progress_bar=True):
        self.encoded += list(texts)
        out = np.zeros((len(texts), DIM), dtype=np.float32)
        for row, text in enumerate(texts):
            for w in words(text):
                out[row, int(hashlib.md5(w.encode()).hexdigest()[:8], 16) % DIM] += 1.0
        if normalize_embeddings:
            norms = np.linalg.norm(out, axis=1, keepdims=True)
            out = out / np.where(norms == 0, 1.0, norms)
        return out


class FakeCrossEncoder:
    """predict(): the number of distinct query words found in the passage."""

    def __init__(self) -> None:
        self.pairs: list[tuple[str, str]] = []

    def predict(self, pairs, batch_size=32, show_progress_bar=True):
        self.pairs += list(pairs)
        return np.array([float(len(set(words(q)) & set(words(p)))) for q, p in pairs])
