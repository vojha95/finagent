"""E1: document channel accuracy against the held-back tags (blueprint 10.1).

Answer key: the tag channel's VERIFIED value for each (company, year, item); the document
channel is read with tags hidden. Field-level accuracy counts a value correct within displayed
precision. Misses are sorted into the 10.1 error taxonomy from the values alone (label noise
needs the hand audit, 9.5). Confidence intervals bootstrap over companies; paired models are
compared with McNemar's exact test.

BC13 (configs/e1_scoring.yaml): for an item pair such as net income vs net income including
NCI, when the filing reports no noncontrolling interest for the period (absent, nil or zero), the
two are the same amount and a reading of either item counts for the other. Each result keeps
the strict outcome (without BC13) alongside. BC14: when a label rule relabelled a row
(source -> target), the target's answer is the filing's verified source-item tag if the target
has none of its own.
"""

import json
import math
import random
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

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
    strict_outcome: str | None = None  # outcome without BC13; None means the same as outcome

    @property
    def strict(self) -> str:
        return self.strict_outcome or self.outcome


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
    equivalents: Mapping[str, str] | None = None,
) -> list[FieldResult]:
    """One FieldResult per item with a VERIFIED tag answer that is shown on a face statement.

    Items tagged only in the notes (on_face excludes them) are not document-channel misses.
    equivalents (item -> twin, BC13) lists the items whose twin's reading also counts for this
    filing; the strict outcome ignores it.
    """
    rows_by_id = rows_by_id or {}
    equivalents = equivalents or {}
    results = []

    def outcome(reading: DocumentReading, answer: Decimal) -> str:
        row = rows_by_id.get(reading.row_id) if reading.row_id else None
        return classify_field(reading, answer, row)

    for item, document in documents.items():
        if on_face is not None and item not in on_face:
            continue
        answer = tag_value(tags.get(item))
        if answer is None or tags[item].status != "VERIFIED":
            continue
        strict = outcome(document, answer)
        twin = documents.get(equivalents.get(item, ""))
        if strict != CORRECT and twin is not None and outcome(twin, answer) == CORRECT:
            results.append(FieldResult(issuer, fiscal_year, item, CORRECT, twin.value, answer,
                                       strict_outcome=strict))
        else:
            results.append(FieldResult(issuer, fiscal_year, item, strict, document.value, answer))
    return results


def strict_results(results: Sequence[FieldResult]) -> list[FieldResult]:
    """The same fields scored without BC13."""
    return [FieldResult(r.issuer, r.fiscal_year, r.item, r.strict, r.document_value, r.tag_value)
            for r in results]


def nci_status(facts: Iterable, concepts: Sequence[str], filing, period_type: str) -> str:
    """REPORTED, ZERO, NIL or ABSENT: the filing's NCI facts for its own period (BC13)."""
    from finagent.tags.inline import fact_matches

    matching = [f for f in facts for c in concepts if fact_matches(f, c, filing, period_type)]
    if any(f.amount is not None and f.amount != 0 for f in matching):
        return "REPORTED"
    if any(f.amount is not None for f in matching):
        return "ZERO"
    return "NIL" if matching else "ABSENT"


def equivalent_items(
    facts: Iterable, filing, equivalences: Sequence
) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    """BC13 twins for one filing ({item: twin}, only pairs without NCI) and the NCI status per
    pair."""
    facts = list(facts)
    twins: dict[str, str] = {}
    statuses: dict[tuple[str, str], str] = {}
    for equivalence in equivalences:
        status = nci_status(facts, equivalence.nci_concepts, filing, equivalence.period_type)
        statuses[equivalence.items] = status
        if status != "REPORTED":
            a, b = equivalence.items
            twins[a], twins[b] = b, a
    return twins, statuses


def nci_counts(log: Iterable[tuple[str, int, tuple[str, str], str]]) -> dict[str, dict[str, int]]:
    """{"a|b": {status: filings}} from run_e1's nci_log."""
    counts: dict[str, dict[str, int]] = {}
    for _, _, pair, status in log:
        entry = counts.setdefault("|".join(pair), {})
        entry[status] = entry.get(status, 0) + 1
    return {k: dict(sorted(v.items())) for k, v in sorted(counts.items())}


def rule_answer_keys(tags: dict[str, Outcome], labels: Iterable) -> dict[str, Outcome]:
    """BC14: for a row a label rule relabelled source -> target, the target's answer is the
    verified source tag when the target has no verified tag of its own (same row, same amount).
    Only items some rule relabelled in this filing change."""
    keyed = dict(tags)
    for label in labels:
        if label.rule is None or label.source_item is None:
            continue
        own, source = keyed.get(label.label), tags.get(label.source_item)
        if (own is None or own.status != "VERIFIED") and source is not None \
                and source.status == "VERIFIED":
            keyed[label.label] = source
    return keyed


def field_accuracy(results: Sequence[FieldResult]) -> float | None:
    if not results:
        return None
    return sum(r.outcome == CORRECT for r in results) / len(results)


def error_taxonomy(results: Sequence[FieldResult]) -> dict[str, int]:
    return dict(Counter(r.outcome for r in results if r.outcome != CORRECT))


def coverage(results: Sequence[FieldResult]) -> float | None:
    """Share of scored fields where the document channel produced a value (not MISSED)."""
    if not results:
        return None
    return sum(r.outcome != MISSED for r in results) / len(results)


def per_item_accuracy(results: Sequence[FieldResult]) -> dict[str, tuple[int, int]]:
    """item -> (correct fields, scored fields), sorted by item."""
    counts: dict[str, list[int]] = {}
    for r in results:
        entry = counts.setdefault(r.item, [0, 0])
        entry[0] += r.outcome == CORRECT
        entry[1] += 1
    return {item: (c, n) for item, (c, n) in sorted(counts.items())}


def write_results(path: Path, results: Sequence[FieldResult], header: dict[str, Any]) -> None:
    """JSON lines: the run header first, then one FieldResult per line (amounts as strings)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"header": header}, default=str) + "\n")
        for r in results:
            handle.write(json.dumps(asdict(r), default=str) + "\n")


def read_results(path: Path) -> tuple[dict[str, Any], list[FieldResult]]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])["header"]
    results = []
    for line in lines[1:]:
        raw = json.loads(line)
        results.append(FieldResult(
            raw["issuer"], int(raw["fiscal_year"]), raw["item"], raw["outcome"],
            None if raw["document_value"] is None else Decimal(raw["document_value"]),
            Decimal(raw["tag_value"]), raw.get("strict_outcome"),
        ))
    return header, results


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


def paired_bootstrap_difference(
    a: Sequence[FieldResult], b: Sequence[FieldResult], draws: int = 2000, level: float = 0.95,
    seed: int = 0,
) -> tuple[float, tuple[float, float]] | None:
    """Field accuracy of b minus a on the fields both scored, with a percentile interval that
    resamples companies (both models' fields of a drawn company move together)."""
    right_a = {(r.issuer, r.fiscal_year, r.item): r.outcome == CORRECT for r in a}
    right_b = {(r.issuer, r.fiscal_year, r.item): r.outcome == CORRECT for r in b}
    by_issuer: dict[str, list[tuple[bool, bool]]] = {}
    for key in sorted(right_a.keys() & right_b.keys()):
        by_issuer.setdefault(key[0], []).append((right_a[key], right_b[key]))
    issuers = sorted(by_issuer)
    if not issuers:
        return None

    def difference(pairs: list[tuple[bool, bool]]) -> float:
        return sum(y - x for x, y in pairs) / len(pairs)

    point = difference([p for i in issuers for p in by_issuer[i]])
    rng = random.Random(seed)
    estimates = sorted(
        difference([p for i in rng.choices(issuers, k=len(issuers)) for p in by_issuer[i]])
        for _ in range(draws))
    low = estimates[int((1 - level) / 2 * draws)]
    high = estimates[min(draws - 1, int((1 + level) / 2 * draws))]
    return point, (low, high)


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
    universe: dict[str, str] | None = None,
    include_held_out: bool = False,
    equivalences: Sequence | None = None,
    nci_log: list | None = None,
    row_log: list | None = None,
) -> list[FieldResult]:
    """Read every pinned filing of `tickers` with `classifier` (tags hidden) and score it.

    Raises HeldOutError for a held-out ticker unless include_held_out (final E1 run only, 9.2).
    equivalences default to configs/e1_scoring.yaml (BC13); nci_log, if given, collects
    (issuer, fiscal_year, item pair, NCI status) per filing; row_log, if given, collects the
    filings' labelled rows exactly as the row dataset builds them (row-level metrics for a
    split the dataset leaves out, i.e. held-out, from this same read).
    """
    from finagent.config import check_not_held_out, load_concept_map, load_universe

    universe = universe if universe is not None else load_universe()
    check_not_held_out(tickers, universe, include_held_out)
    from finagent.dataset.build import labelled_records
    from finagent.dataset.labels import concept_owner, label_rows
    from finagent.document.channel import read_document_channel
    from finagent.evidence.integrity import read_snapshot, read_snapshot_json
    from finagent.filing_rows import read_filing_rows
    from finagent.tags.channel import read_items

    from finagent.config import load_e1_equivalences

    concept_map = concept_map or load_concept_map()
    specs = list(concept_map.values())
    owner = concept_owner(concept_map)
    equivalences = load_e1_equivalences(concept_map=concept_map) if equivalences is None \
        else equivalences
    results: list[FieldResult] = []
    for ticker in tickers:
        for filing in store.load_filings(ticker, as_of):
            document = read_snapshot(store.evidence_dir, filing.primary_document, store.manifest)
            located = read_filing_rows(document, filing.accession,
                                       store.fiscal_periods(filing))
            rows = [r for rs in located.rows.values() for r in rs]
            readings = read_document_channel(rows, classifier.predict(rows), filing, specs)
            facts = located.facts
            company_facts = read_snapshot_json(
                store.evidence_dir, f"companyfacts_{filing.cik}.json", store.manifest
            )
            tags = read_items(filing, specs, company_facts, facts, as_of)
            labels = [label for _, label in label_rows(rows, facts, owner)]
            on_face = {label.label for label in labels}
            tags = rule_answer_keys(tags, labels)
            twins, statuses = equivalent_items(facts, filing, equivalences)
            if nci_log is not None:
                for pair, status in statuses.items():
                    nci_log.append((filing.issuer, filing.fiscal_year, pair, status))
            if row_log is not None:
                row_log.extend(labelled_records(filing, universe.get(ticker, ""), located.rows,
                                                facts, owner))
            results.extend(score_filing(filing.issuer, filing.fiscal_year, readings, tags,
                                        {r.row_id: r for r in rows}, on_face, twins))
    return results


def pinned_tickers(
    store, universe: dict[str, str], split: str, as_of, include_held_out: bool = False
) -> list[str]:
    """Tickers of `split` whose filings are pinned; the rest cannot be scored yet.

    Raises HeldOutError for the held-out split unless include_held_out (final E1 run only).
    """
    from finagent.config import HELD_OUT, HeldOutError
    from finagent.evidence.integrity import EvidenceIntegrityError

    if split == HELD_OUT and not include_held_out:
        raise HeldOutError("the held-out split needs include_held_out (final E1 run only)")

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
