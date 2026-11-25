"""Quoted sentences for an insight card or an explanation (blueprint 8.13; Phase 7.3; #173).

The frozen query (configs/query_templates.yaml) is searched with BM25 over the text chunks of
the allowed sections of one 10-K (finagent.retrieval.index, as of a date). The `hits` best
chunks are read sentence by sentence (the chunker's own sentences, so each is verbatim in its
chunk). A sentence can be quoted only when:

- it is a whole sentence (ends with . ! or ?, does not start in lower case);
- it has min_words to max_words words; a longer sentence is skipped, never truncated (#173);
- it contains one of the query's anchor terms (lower-case substring);
- it does not name an earlier year without naming the card's fiscal year (a "2024 compared
  with 2023" sentence in a FY2025 10-K explains another year); later years (maturities,
  outlook) are allowed.

Score: the share of the query's IDF weight (the filing's own BM25 index; terms with a positive
IDF) found in the sentence, 0 to 1. Candidates rank by score, then chunk rank, then position;
the best max_quotes distinct sentences with a score of at least min_score are quoted. None:
the card or explanation is an EVIDENCE_GAP. No model writes or edits a quote.
"""

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from finagent.config import EvidenceSpec, RetrievalConfig
from finagent.records import Chunk, Hit, Quote
from finagent.retrieval.chunk import part_text, section_parts
from finagent.retrieval.index import FilingIndex, Retriever, tokenize

YEAR = re.compile(r"(?<![\d$.,])(?:19|20)\d{2}(?![\d%])")
SENTENCE_END = re.compile(r"[.!?][\"”’)\]]*$")
LEADERS = "•·▪-–—*\"“‘( "
NOT_A_SENTENCE, TOO_LONG, TOO_SHORT = "NOT_A_SENTENCE", "TOO_LONG", "TOO_SHORT"
NO_ANCHOR, OTHER_YEAR = "NO_ANCHOR", "OTHER_YEAR"


@dataclass(frozen=True)
class Candidate:
    """A sentence that passed the rules, with its score and where it was found."""

    quote: Quote
    rank: int  # the chunk's BM25 rank (1 = best)
    position: int  # the sentence's position in the chunk
    words: int


@dataclass(frozen=True)
class QuoteResult:
    quotes: tuple[Quote, ...]  # the quoted sentences, best first (empty: EVIDENCE_GAP)
    candidates: tuple[Candidate, ...] = ()  # every sentence that passed the rules, best first
    skipped: dict[str, int] = field(default_factory=dict)  # rule -> sentences it skipped
    hits: tuple[Hit, ...] = ()


def chunk_sentences(index: FilingIndex, chunk: Chunk,
                    config: RetrievalConfig) -> tuple[str, ...]:
    """The sentences of one text chunk (the chunker's own, in order)."""
    if chunk.kind != "text":
        return ()
    for s in index.sections.sections:
        if s.section_id != chunk.section_id:
            continue
        parts = section_parts(index.blocks[s.start_block:s.end_block], config)
        if chunk.ordinal < len(parts):
            kind, sentences = parts[chunk.ordinal]
            if kind == chunk.kind and part_text(kind, sentences) == chunk.text:
                return sentences
    raise ValueError(f"{chunk.chunk_id}: the chunk's sentences were not found")


def whole_sentence(sentence: str) -> bool:
    first = sentence.lstrip(LEADERS)[:1]
    return bool(SENTENCE_END.search(sentence)) and bool(first) and not first.islower()


def names_other_year(sentence: str, fiscal_year: int) -> bool:
    years = {int(y) for y in YEAR.findall(sentence)}
    return bool(years) and fiscal_year not in years and min(years) < fiscal_year


def sentence_problem(sentence: str, anchors: Sequence[str], fiscal_year: int,
                     spec: EvidenceSpec) -> str | None:
    """The first rule a sentence breaks, or None (module docstring)."""
    if not whole_sentence(sentence):
        return NOT_A_SENTENCE
    words = len(sentence.split())
    if words > spec.max_words:
        return TOO_LONG
    if words < spec.min_words:
        return TOO_SHORT
    lower = sentence.lower()
    if not any(a in lower for a in anchors):
        return NO_ANCHOR
    if names_other_year(sentence, fiscal_year):
        return OTHER_YEAR
    return None


def idf_weights(index: FilingIndex, query: str, config: RetrievalConfig) -> dict[str, float]:
    """The query's distinct terms with a positive IDF in the filing's index."""
    idf = index.bm25.idf if index.bm25 is not None else {}
    return {t: idf[t] for t in dict.fromkeys(tokenize(query, config.stopwords))
            if idf.get(t, 0) > 0}


def coverage(sentence: str, weights: dict[str, float], config: RetrievalConfig) -> float:
    """The share of the query's IDF weight found in the sentence (0 to 1)."""
    total = sum(weights.values())
    if total <= 0:
        return 0.0
    present = set(tokenize(sentence, config.stopwords))
    return sum(w for t, w in weights.items() if t in present) / total


def find_quotes(index: FilingIndex, hits: Sequence[Hit], query: str, anchors: Sequence[str],
                fiscal_year: int, spec: EvidenceSpec, config: RetrievalConfig) -> QuoteResult:
    """The quotes for one query from its hits in one filing (module docstring)."""
    weights = idf_weights(index, query, config)
    found: list[Candidate] = []
    skipped: Counter[str] = Counter()
    seen: set[str] = set()
    for hit in hits:
        for position, sentence in enumerate(chunk_sentences(index, hit.chunk, config)):
            problem = sentence_problem(sentence, anchors, fiscal_year, spec)
            if problem:
                skipped[problem] += 1
                continue
            if sentence in seen:
                continue
            seen.add(sentence)
            score = round(coverage(sentence, weights, config), 6)
            found.append(Candidate(Quote(hit.chunk.chunk_id, hit.chunk.accession, sentence,
                                         hit.chunk.section_id, score, hit.chunk.sha256),
                                   hit.rank, position, len(sentence.split())))
    found.sort(key=lambda c: (-c.quote.score, c.rank, c.position))
    quotes = tuple(c.quote for c in found if c.quote.score >= spec.min_score)[:spec.max_quotes]
    return QuoteResult(quotes, tuple(found), dict(skipped), tuple(hits))


def quotes_for(retriever: Retriever, issuer: str, fiscal_year: int, query: str,
               anchors: Sequence[str], sections: Sequence[str], spec: EvidenceSpec, *,
               as_of: date) -> QuoteResult:
    """Search the fiscal year's 10-K as of the date and pick its quotes (none: no filing)."""
    f = retriever.filing(issuer, fiscal_year, as_of)
    if f is None:
        return QuoteResult(())
    hits = retriever.search(issuer, fiscal_year, query, as_of=as_of, sections=sections,
                            k=spec.hits)
    return find_quotes(retriever.index(f), hits, query, anchors, fiscal_year, spec,
                       retriever.config)
