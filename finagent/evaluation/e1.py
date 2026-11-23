"""E1: document channel accuracy against the held-back tags (blueprint 10.1).

Answer key: the tag channel's VERIFIED value for each (company, year, item); the document
channel is read with tags hidden. Field-level accuracy counts a value correct within displayed
precision. Misses are sorted into the 10.1 error taxonomy from the values alone (label noise
needs the hand audit, 9.5). Confidence intervals bootstrap over companies; paired models are
compared with McNemar's exact test.
"""

import math
import random
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from finagent.document.channel import DocumentReading
from finagent.document.normalize import normalise_row
from finagent.records import Outcome, RowInstance
from finagent.verify import tag_value, within_precision

CORRECT = "CORRECT"
MISSED = "MISSED"  # no document value where the tag has one
SIGN = "SIGN"
SCALE = "SCALE"
PERIOD = "PERIOD"  # the chosen row holds the tag value in another period column
WRONG_ROW = "WRONG_ROW"

_SCALE_SHIFTS = (-9, -6, -3, 3, 6, 9)  # thousands / millions / billions misread


@dataclass(frozen=True)
class FieldResult:
    issuer: str
    fiscal_year: int
    item: str
    outcome: str  # CORRECT or an error code
    document_value: Decimal | None
    tag_value: Decimal


def classify_field(
    document: DocumentReading, tag: Decimal, row: RowInstance | None = None
) -> str:
    """CORRECT or the error type for one field whose answer key is `tag`."""
    value = document.value
    if value is None:
        return MISSED
    if within_precision(value, document.scale, tag):
        return CORRECT
    if within_precision(-value, document.scale, tag):
        return SIGN
    scale = document.scale or 0
    for shift in _SCALE_SHIFTS:
        shifted = value.scaleb(shift)
        if within_precision(shifted, scale + shift, tag) or within_precision(-shifted, scale + shift, tag):
            return SCALE
    if row is not None:
        others = [v.value for v in normalise_row(row) if v.value is not None and v.value != value]
        if any(within_precision(o, document.scale, tag) or within_precision(-o, document.scale, tag)
               for o in others):
            return PERIOD
    return WRONG_ROW


def score_filing(
    issuer: str,
    fiscal_year: int,
    documents: dict[str, DocumentReading],
    tags: dict[str, Outcome],
    rows_by_id: dict[str, RowInstance] | None = None,
    on_face: set[str] | None = None,
) -> list[FieldResult]:
    """One FieldResult per item with a VERIFIED tag answer that is shown on a face statement.

    Items tagged only in the notes (on_face excludes them) are not document-channel misses.
    """
    rows_by_id = rows_by_id or {}
    results = []
    for item, document in documents.items():
        if on_face is not None and item not in on_face:
            continue
        answer = tag_value(tags.get(item))
        if answer is None or tags[item].status != "VERIFIED":
            continue
        row = rows_by_id.get(document.row_id) if document.row_id else None
        results.append(FieldResult(issuer, fiscal_year, item, classify_field(document, answer, row),
                                   document.value, answer))
    return results


def field_accuracy(results: Sequence[FieldResult]) -> float | None:
    if not results:
        return None
    return sum(r.outcome == CORRECT for r in results) / len(results)


def error_taxonomy(results: Sequence[FieldResult]) -> dict[str, int]:
    return dict(Counter(r.outcome for r in results if r.outcome != CORRECT))


def bootstrap_ci(
    results: Sequence[FieldResult], draws: int = 2000, level: float = 0.95, seed: int = 0
) -> tuple[float, float] | None:
    """Percentile interval for field accuracy, resampling companies (rows repeat within one)."""
    by_issuer: dict[str, list[FieldResult]] = {}
    for result in results:
        by_issuer.setdefault(result.issuer, []).append(result)
    issuers = sorted(by_issuer)
    if not issuers:
        return None
    rng = random.Random(seed)
    estimates = []
    for _ in range(draws):
        sample = [r for i in rng.choices(issuers, k=len(issuers)) for r in by_issuer[i]]
        estimates.append(field_accuracy(sample))
    estimates.sort()
    low = estimates[int((1 - level) / 2 * draws)]
    high = estimates[min(draws - 1, int((1 + level) / 2 * draws))]
    return low, high


def mcnemar(a: Sequence[FieldResult], b: Sequence[FieldResult]) -> tuple[int, int, float]:
    """Exact McNemar test on fields scored by both models: (a-only right, b-only right, p)."""
    right_a = {(r.issuer, r.fiscal_year, r.item): r.outcome == CORRECT for r in a}
    right_b = {(r.issuer, r.fiscal_year, r.item): r.outcome == CORRECT for r in b}
    shared = right_a.keys() & right_b.keys()
    only_a = sum(right_a[k] and not right_b[k] for k in shared)
    only_b = sum(right_b[k] and not right_a[k] for k in shared)
    n = only_a + only_b
    if n == 0:
        return only_a, only_b, 1.0
    tail = sum(math.comb(n, k) for k in range(min(only_a, only_b) + 1)) / 2**n
    return only_a, only_b, min(1.0, 2 * tail)


def run_e1(
    store,
    tickers: Sequence[str],
    as_of,
    classifier,
    concept_map: dict | None = None,
) -> list[FieldResult]:
    """Read every pinned filing of `tickers` with `classifier` (tags hidden) and score it."""
    from finagent.config import load_concept_map
    from finagent.dataset.labels import concept_owner, label_rows
    from finagent.document.channel import read_document_channel
    from finagent.evidence.integrity import read_snapshot, read_snapshot_json
    from finagent.filing_rows import read_filing_rows
    from finagent.tags.channel import read_items

    concept_map = concept_map or load_concept_map()
    specs = list(concept_map.values())
    owner = concept_owner(concept_map)
    results: list[FieldResult] = []
    for ticker in tickers:
        for filing in store.load_filings(ticker, as_of):
            document = read_snapshot(store.evidence_dir, filing.primary_document, store.manifest)
            located = read_filing_rows(document, filing.accession)
            rows = [r for rs in located.rows.values() for r in rs]
            readings = read_document_channel(rows, classifier.predict(rows), filing, specs)
            facts = located.facts
            company_facts = read_snapshot_json(
                store.evidence_dir, f"companyfacts_{filing.cik}.json", store.manifest
            )
            tags = read_items(filing, specs, company_facts, facts, as_of)
            on_face = {label.label for _, label in label_rows(rows, facts, owner)}
            results.extend(score_filing(filing.issuer, filing.fiscal_year, readings, tags,
                                        {r.row_id: r for r in rows}, on_face))
    return results


def pinned_tickers(store, universe: dict[str, str], split: str, as_of) -> list[str]:
    """Tickers of `split` whose filings are pinned; the rest cannot be scored yet."""
    from finagent.evidence.integrity import EvidenceIntegrityError

    tickers = []
    for ticker, ticker_split in universe.items():
        if ticker_split != split:
            continue
        try:
            store.load_filings(ticker, as_of)
        except (EvidenceIntegrityError, KeyError):
            continue
        tickers.append(ticker)
    return tickers
