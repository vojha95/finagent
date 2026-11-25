"""The gate's quote check (#157; blueprint 8.16 V2; Phase 7.3).

A quote is accepted only when the chunk it cites, rebuilt here from the pinned primary
document (read and hash-checked against the evidence manifest, cleaned of tags, split into
blocks, sections and chunks by the same deterministic code), still has the cited sha256, is a
text chunk of an allowed section, and contains the quoted sentence verbatim; the quote is at
most max_words words (#173). The rebuild is independent of the tool that made the quote: it
shares no state with the retriever (its own read of the file, its own cache).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from finagent.config import RetrievalConfig
from finagent.evidence.integrity import read_snapshot
from finagent.evidence.store import EvidenceStore, FilingRecord
from finagent.ixbrl import clean_document
from finagent.ixbrl.tagstore import parse_document
from finagent.records import AnswerObservation, Chunk, Quote
from finagent.retrieval.chunk import chunk_document
from finagent.retrieval.index import in_sections
from finagent.retrieval.sections import detect_sections, document_blocks

QuoteCheck = Callable[[AnswerObservation, FilingRecord], str | None]


@dataclass
class PinnedChunks:
    """Chunks rebuilt from the pinned primary documents, by accession."""

    store: EvidenceStore
    config: RetrievalConfig
    cache: dict[str, dict[str, Chunk]] = field(default_factory=dict)

    def chunks(self, filing: FilingRecord) -> dict[str, Chunk]:
        if filing.accession not in self.cache:
            document = read_snapshot(self.store.evidence_dir, filing.primary_document,
                                     self.store.manifest)
            blocks = document_blocks(parse_document(clean_document(document)), self.config)
            sections = detect_sections(blocks, self.config)
            self.cache[filing.accession] = {
                c.chunk_id: c for c in chunk_document(filing.accession, blocks, sections,
                                                      self.config)}
        return self.cache[filing.accession]


def quote_problem(q: Quote, chunk: Chunk | None, sections: Sequence[str],
                  max_words: int) -> str | None:
    """The first check one quote fails against its rebuilt chunk, or None."""
    if chunk is None:
        return "QUOTE_CHUNK_NOT_FOUND"
    if chunk.sha256 != q.chunk_sha256:
        return "QUOTE_CHUNK_CHANGED"
    if chunk.kind != "text":
        return "QUOTE_FROM_TABLE"
    if q.section != chunk.section_id or not in_sections(chunk.section_id, sections):
        return "QUOTE_SECTION_NOT_ALLOWED"
    if not q.text or q.text not in chunk.text:
        return "QUOTE_NOT_VERBATIM"
    if len(q.text.split()) > max_words:
        return "QUOTE_TOO_LONG"
    return None


def quote_checker(pinned: PinnedChunks, sections: Sequence[str], max_words: int) -> QuoteCheck:
    """A gate check of every quote of an observation against the cited 10-K's chunks."""
    def check(o: AnswerObservation, filing: FilingRecord) -> str | None:
        chunks = pinned.chunks(filing)
        for q in o.quotes:
            code = quote_problem(q, chunks.get(q.chunk_id), sections, max_words)
            if code:
                return code
        return None

    return check
