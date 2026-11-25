"""BM25 search over one 10-K at a time, as of a date (blueprint 8.14; Phase 7.2).

Each filing has its own index (BM25 over its text chunks; tables are stored, not searched), so
term statistics are never pooled across filings or dates. search(issuer, fiscal_year, query,
as_of=...) reads the issuer's 10-Ks filed on or before as_of from the evidence store (held-out
issuers refused), indexes the one for that fiscal year from its pinned, hash-checked primary
document (clean document: no tags), and returns the best chunks, optionally restricted to
sections ("ITEM_7" matches Item 7; "ITEM_8" matches Item 8 and its notes). Every hit is asserted
to come from that filing. Dense retrieval, fusion and the reranker of 8.14 wait for D8 (model
downloads, pending Vineet).

Tokens: lower-case letters and digits, configured stop words removed, plural endings folded
("inventories" -> "inventory", "receivables" -> "receivable").
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from rank_bm25 import BM25Okapi

from finagent.config import (RetrievalConfig, check_not_held_out, load_retrieval_config,
                             load_universe)
from finagent.evidence.integrity import read_snapshot
from finagent.evidence.store import EvidenceStore, FilingRecord
from finagent.ixbrl import clean_document
from finagent.ixbrl.tagstore import parse_document
from finagent.records import Chunk, Hit
from finagent.retrieval.chunk import chunk_document
from finagent.retrieval.sections import Block, SectionMap, detect_sections, document_blocks

_TOKEN = re.compile(r"[a-z0-9]+")


class RetrievalError(Exception):
    """A search that cannot be answered as asked (bad as_of, a hit from another filing)."""


def fold(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def tokenize(text: str, stopwords: frozenset[str]) -> list[str]:
    return [fold(t) for t in _TOKEN.findall(text.lower()) if t not in stopwords]


def in_sections(section_id: str, sections: Sequence[str] | None) -> bool:
    return sections is None or any(section_id == s or section_id.startswith(s + "/")
                                   for s in sections)


@dataclass
class FilingIndex:
    """One filing's blocks, sections, chunks and BM25 index (over its text chunks)."""

    filing: FilingRecord
    blocks: list[Block]
    sections: SectionMap
    chunks: tuple[Chunk, ...]
    bm25: BM25Okapi | None
    searched: tuple[int, ...]  # positions in chunks of the indexed (text) chunks


def build_index(filing: FilingRecord, document: bytes, config: RetrievalConfig) -> FilingIndex:
    """Index one filing's primary document (the raw pinned bytes; cleaned here)."""
    blocks = document_blocks(parse_document(clean_document(document)), config)
    sections = detect_sections(blocks, config)
    chunks = chunk_document(filing.accession, blocks, sections, config)
    searched = tuple(i for i, c in enumerate(chunks) if c.kind == "text")
    corpus = [tokenize(chunks[i].text, config.stopwords) for i in searched]
    bm25 = BM25Okapi(corpus, k1=config.k1, b=config.b) if corpus else None
    return FilingIndex(filing, blocks, sections, chunks, bm25, searched)


def rank(index: FilingIndex, query: str, config: RetrievalConfig,
         sections: Sequence[str] | None, k: int) -> list[tuple[Chunk, float]]:
    """The k best text chunks with a positive score, in the given sections; ties by order."""
    if index.bm25 is None:
        return []
    scores = index.bm25.get_scores(tokenize(query, config.stopwords))
    scored = [(index.chunks[i], float(s)) for i, s in zip(index.searched, scores)
              if s > 0 and in_sections(index.chunks[i].section_id, sections)]
    order = {c.chunk_id: n for n, c in enumerate(index.chunks)}
    return sorted(scored, key=lambda pair: (-pair[1], order[pair[0].chunk_id]))[:k]


def check_date(as_of: Any) -> date:
    if not isinstance(as_of, date) or isinstance(as_of, datetime):
        raise RetrievalError(f"as_of must be a date, got {as_of!r}")
    return as_of


@dataclass
class Retriever:
    """Point-in-time search over the evidence store (module docstring)."""

    store: EvidenceStore
    universe: Mapping[str, str] = field(default_factory=load_universe)
    config: RetrievalConfig = field(default_factory=load_retrieval_config)
    indexes: dict[str, FilingIndex] = field(default_factory=dict)  # by accession

    def filing(self, issuer: str, fiscal_year: int, as_of: date) -> FilingRecord | None:
        """The issuer's 10-K for the fiscal year among those filed by as_of."""
        check_date(as_of)
        check_not_held_out([issuer], self.universe, include_held_out=False)
        return next((f for f in self.store.load_filings(issuer, as_of)
                     if f.filed <= as_of and f.fiscal_year == fiscal_year), None)

    def index(self, filing: FilingRecord) -> FilingIndex:
        if filing.accession not in self.indexes:
            document = read_snapshot(self.store.evidence_dir, filing.primary_document,
                                     self.store.manifest)
            self.indexes[filing.accession] = build_index(filing, document, self.config)
        return self.indexes[filing.accession]

    def search(self, issuer: str, fiscal_year: int, query: str, *, as_of: date,
               sections: Sequence[str] | None = None, k: int | None = None) -> tuple[Hit, ...]:
        """The best chunks of the fiscal year's 10-K as of the date; () when there is none."""
        f = self.filing(issuer, fiscal_year, as_of)
        if f is None:
            return ()
        hits = []
        for n, (chunk, score) in enumerate(rank(self.index(f), query, self.config, sections,
                                                k or self.config.top_k), start=1):
            if chunk.accession != f.accession or f.filed > as_of:
                raise RetrievalError(f"hit {chunk.chunk_id} is not from {f.accession}")
            hits.append(Hit(chunk, score, n, issuer, f.fiscal_year, f.filed, f.filing_url))
        return tuple(hits)
