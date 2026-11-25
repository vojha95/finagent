"""Chunks of a 10-K's sections (blueprint 8.14; Phase 7.2).

Within each section, text blocks are split into sentences (a rule-based splitter: a full stop,
question or exclamation mark followed by a capital, digit or quote, unless the word before is
a configured abbreviation or an initial) and merged in order into chunks of about
target_words words (about 200 tokens), closed at a sentence boundary and never past max_words
(a single longer sentence is one chunk). A chunk never crosses a section. Each data table is
its own chunk (kind "table"), stored apart from the text. Chunk ids are deterministic,
{accession}:{section}:{ordinal}, with the sha256 of the text, so the same filing always yields
the same chunks.
"""

import hashlib
import re
from collections.abc import Sequence

from finagent.config import RetrievalConfig
from finagent.records import Chunk
from finagent.retrieval.sections import Block, SectionMap

_BOUNDARY = re.compile(r"([.!?][\"”’)\]]*)\s+(?=[\"“(\[]?[A-Z0-9$])")
_INITIALS = re.compile(r"(?:[a-z]\.)+")
_CLOSERS = "\"”’)]"


def split_sentences(text: str, abbreviations: frozenset[str]) -> list[str]:
    """The sentences of one text block, in order; their concatenation is the block's words."""
    out, start = [], 0
    for m in _BOUNDARY.finditer(text):
        end = m.end(1)
        words = text[start:end].split()
        last = words[-1].lower().rstrip(_CLOSERS) if words else ""
        if last in abbreviations or _INITIALS.fullmatch(last):
            continue
        out.append(text[start:end].strip())
        start = m.end()
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


def chunk_id(accession: str, section_id: str, ordinal: int) -> str:
    return f"{accession}:{section_id}:{ordinal:04d}"


def make_chunk(accession: str, section_id: str, ordinal: int, kind: str, text: str) -> Chunk:
    return Chunk(chunk_id(accession, section_id, ordinal), accession, section_id, ordinal,
                 kind, text, len(text.split()), hashlib.sha256(text.encode("utf-8")).hexdigest())


def chunk_section(accession: str, section_id: str, blocks: Sequence[Block],
                  config: RetrievalConfig) -> list[Chunk]:
    """The chunks of one section's blocks (module docstring)."""
    chunks: list[Chunk] = []
    sentences: list[str] = []
    words = 0

    def emit(kind: str = "text", text: str | None = None) -> None:
        nonlocal sentences, words
        if kind == "text" and sentences:
            chunks.append(make_chunk(accession, section_id, len(chunks), "text",
                                     " ".join(sentences)))
            sentences, words = [], 0
        elif kind == "table" and text:
            chunks.append(make_chunk(accession, section_id, len(chunks), "table", text))

    for block in blocks:
        if block.kind == "table":
            emit()
            emit("table", block.text)
            continue
        for sentence in split_sentences(block.text, config.abbreviations):
            n = len(sentence.split())
            if sentences and words + n > config.max_words:
                emit()
            sentences.append(sentence)
            words += n
            if words >= config.target_words:
                emit()
    emit()
    return chunks


def chunk_document(accession: str, blocks: Sequence[Block], sections: SectionMap,
                   config: RetrievalConfig) -> tuple[Chunk, ...]:
    """Every chunk of the document, section by section in reading order."""
    return tuple(c for s in sections.sections
                 for c in chunk_section(accession, s.section_id,
                                        blocks[s.start_block:s.end_block], config))
