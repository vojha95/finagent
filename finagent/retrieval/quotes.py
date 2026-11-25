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
  outlook) are allowed;
- AMOUNT_MISMATCH (#185, Vineet): when the caller knows the computed change of the subject
  (an explanation: the metric's change), a sentence whose stated change (the first change
  phrase: "increased $12.4 billion or 28%", "a decrease of $170", "grew 18%") disagrees with
  it, in direction, amount or percentage, beyond the rounding tolerance is skipped. Tolerance
  (BC24): half a unit of the sentence's displayed precision, plus the rounding of the two
  reported amounts behind the computed change (one statement unit; in percent, that unit over
  the earlier amount). An amount with no magnitude word is read in the statement unit; when
  that unit is unknown the amount is not checked. A sentence that states no change is kept.

Score: the share of the query's IDF weight (the filing's own BM25 index; terms with a positive
IDF) found in the sentence, 0 to 1. Candidates rank by score, then chunk rank, then position;
the best max_quotes distinct sentences with a score of at least min_score are quoted. None:
the card or explanation is an EVIDENCE_GAP. No model writes or edits a quote.

Evidence v2 (D-EV, post-hoc; docs/evidence_v2.md; configs/signals.yaml evidence_version):
the hits come from hybrid_rrf_rerank; a chunk whose cross-encoder score is below
min_rerank_score supplies no sentence (it is reported as below the threshold), and candidates
rank by the chunk's rank first, then score, then position. Every sentence rule above is the
same in both versions.
"""

import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from finagent.config import RERANK_VARIANT, EvidenceSpec, RetrievalConfig
from finagent.records import Chunk, Hit, Quote
from finagent.retrieval.chunk import part_text, section_parts
from finagent.retrieval.index import FilingIndex, Retriever, tokenize

YEAR = re.compile(r"(?<![\d$.,])(?:19|20)\d{2}(?![\d%])")
SENTENCE_END = re.compile(r"[.!?][\"”’)\]]*$")
LEADERS = "•·▪-–—*\"“‘( "
NOT_A_SENTENCE, TOO_LONG, TOO_SHORT = "NOT_A_SENTENCE", "TOO_LONG", "TOO_SHORT"
NO_ANCHOR, OTHER_YEAR = "NO_ANCHOR", "OTHER_YEAR"
AMOUNT_MISMATCH = "AMOUNT_MISMATCH"  # #185

# #185: the first change phrase of a sentence: a change verb, then an amount and/or a
# percentage ("increased $12.4 billion or 28%", "an increase of $170", "declined 5 percent").
_UP = r"increased|increase of|rose|grew|growth of"
_DOWN = r"decreased|decrease of|declined|decline of|fell|dropped"
_AMOUNT = r"\$\s?(?P<{0}>\d[\d,]*(?:\.\d+)?)(?:\s*(?P<{0}_scale>billion|million|thousand)\b)?"
_PERCENT = r"(?P<{0}>\d+(?:\.\d+)?)\s?(?:%|percent(?!age))"
CHANGE_PHRASE = re.compile(
    rf"\b(?P<verb>{_UP}|{_DOWN})\s+(?:by\s+)?(?:approximately\s+|about\s+|nearly\s+)?"
    rf"(?:{_AMOUNT.format('amount')}(?:\s*,?\s*or\s+{_PERCENT.format('pct_after')})?"
    rf"|{_PERCENT.format('pct')}(?:\s*,?\s*or\s+{_AMOUNT.format('amount_after')})?)",
    re.IGNORECASE)
DOWN_VERB = re.compile(rf"^(?:{_DOWN})$", re.IGNORECASE)
SCALES = {"billion": Decimal(10) ** 9, "million": Decimal(10) ** 6, "thousand": Decimal(10) ** 3}


@dataclass(frozen=True)
class ChangeCheck:
    """The computed change a quoted sentence must agree with (#185)."""

    amount: Decimal  # signed change in USD
    base: Decimal  # the earlier amount (for the percentage)
    unit: Decimal | None = None  # the statement unit in USD (1e6 for $ millions), if known

    @property
    def percent(self) -> float | None:
        return float(self.amount / abs(self.base) * 100) if self.base else None


@dataclass(frozen=True)
class StatedChange:
    """The first change phrase of a sentence: direction, amount (USD) and percentage, each with
    half a unit of its displayed precision."""

    sign: int
    amount: Decimal | None = None
    amount_half_unit: Decimal = Decimal(0)
    percent: float | None = None
    percent_half_unit: float = 0.0


def _decimals(text: str) -> int:
    return len(text.split(".")[1]) if "." in text else 0


def stated_change(sentence: str, unit: Decimal | None) -> StatedChange | None:
    """The sentence's first change phrase, or None when it states no amount or percentage.
    An amount with no magnitude word is in the statement unit; unknown unit: not read."""
    m = CHANGE_PHRASE.search(sentence)
    if m is None:
        return None
    sign = -1 if DOWN_VERB.match(m.group("verb").lower()) else 1
    found = StatedChange(sign)
    for key in ("amount", "amount_after"):
        if m.group(key):
            word = m.group(f"{key}_scale")
            scale = SCALES[word.lower()] if word else unit
            if scale is not None:
                digits = m.group(key).replace(",", "")
                found = StatedChange(sign, Decimal(digits) * scale,
                                     Decimal("0.5") * scale / Decimal(10) ** _decimals(digits))
    for key in ("pct", "pct_after"):
        if m.group(key):
            digits = m.group(key)
            found = StatedChange(sign, found.amount, found.amount_half_unit, float(digits),
                                 0.5 / 10 ** _decimals(digits))
    return found if found.amount is not None or found.percent is not None else None


def disagrees(sentence: str, expected: ChangeCheck) -> bool:
    """#185: the sentence states a change that the computed change contradicts."""
    stated = stated_change(sentence, expected.unit)
    if stated is None:
        return False
    if (expected.amount > 0) != (stated.sign > 0):
        return True
    rounding = expected.unit or Decimal(0)  # two reported amounts, half a unit each
    if stated.amount is not None and abs(stated.sign * stated.amount - expected.amount) \
            > stated.amount_half_unit + rounding:
        return True
    percent = expected.percent
    if stated.percent is not None and percent is not None:
        slack = float(rounding / abs(expected.base) * 100) if expected.base else 0.0
        if abs(stated.sign * stated.percent - percent) > stated.percent_half_unit + slack \
                + 1e-9:
            return True
    return False


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
    below_threshold: tuple[Hit, ...] = ()  # v2: hits under min_rerank_score, not read


def gated_hits(hits: Sequence[Hit], spec: EvidenceSpec) -> tuple[list[Hit], list[Hit]]:
    """(hits that may supply a quote, hits below min_rerank_score). The gate reads the
    cross-encoder score, so every hit must come from the rerank variant."""
    if spec.min_rerank_score is None:
        return list(hits), []
    wrong = sorted({h.variant for h in hits if h.variant != RERANK_VARIANT})
    if wrong:
        raise ValueError(f"min_rerank_score needs {RERANK_VARIANT} hits, got {wrong}")
    return ([h for h in hits if h.score >= spec.min_rerank_score],
            [h for h in hits if h.score < spec.min_rerank_score])


def candidate_order(order: str) -> Callable[[Candidate], tuple]:
    """v1 "score": sentence score, chunk rank, position; v2 "rank": chunk rank first."""
    if order == "rank":
        return lambda c: (c.rank, -c.quote.score, c.position)
    return lambda c: (-c.quote.score, c.rank, c.position)


def threshold_note(result: QuoteResult, spec: EvidenceSpec) -> str:
    """v2: why a gap's passages were not read ("" when none was below the threshold)."""
    if not result.below_threshold:
        return ""
    best = max(h.score for h in result.below_threshold)
    return (f"{len(result.below_threshold)} passage(s) below the reranker threshold "
            f"{spec.min_rerank_score:.3f} (best {best:.3f})")


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
                fiscal_year: int, spec: EvidenceSpec, config: RetrievalConfig,
                expected: ChangeCheck | None = None) -> QuoteResult:
    """The quotes for one query from its hits in one filing (module docstring). expected: the
    subject's computed change, for the AMOUNT_MISMATCH rule (#185); None: not checked."""
    weights = idf_weights(index, query, config)
    found: list[Candidate] = []
    skipped: Counter[str] = Counter()
    seen: set[str] = set()
    read, below = gated_hits(hits, spec)
    for hit in read:
        for position, sentence in enumerate(chunk_sentences(index, hit.chunk, config)):
            problem = sentence_problem(sentence, anchors, fiscal_year, spec)
            if problem is None and expected is not None and disagrees(sentence, expected):
                problem = AMOUNT_MISMATCH
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
    found.sort(key=candidate_order(spec.order))
    quotes = tuple(c.quote for c in found if c.quote.score >= spec.min_score)[:spec.max_quotes]
    return QuoteResult(quotes, tuple(found), dict(skipped), tuple(hits), tuple(below))


def quotes_for(retriever: Retriever, issuer: str, fiscal_year: int, query: str,
               anchors: Sequence[str], sections: Sequence[str], spec: EvidenceSpec, *,
               as_of: date, expected: ChangeCheck | None = None,
               variant: str | None = None) -> QuoteResult:
    """Search the fiscal year's 10-K as of the date and pick its quotes (none: no filing).
    variant: the retrieval variant (7.4); None: the evidence version's, else the retriever's
    (default bm25)."""
    f = retriever.filing(issuer, fiscal_year, as_of)
    if f is None:
        return QuoteResult(())
    hits = retriever.search(issuer, fiscal_year, query, as_of=as_of, sections=sections,
                            k=spec.hits, variant=variant or spec.variant)
    return find_quotes(retriever.index(f), hits, query, anchors, fiscal_year, spec,
                       retriever.config, expected)
