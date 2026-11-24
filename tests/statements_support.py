"""Synthetic filings, rows, facts and observations for the Phase 4a statement-set tests."""

from datetime import date
from decimal import Decimal

from finagent.config import LineItemSpec, ModelInputSpec
from finagent.evidence.store import FilingRecord
from finagent.ixbrl.tagstore import CellAddress, TagFact
from finagent.records import Observation, Outcome, Reading, RowInstance
from finagent.statements.verify_set import PENDING, base_observation

START, END, PRIOR_END = date(2025, 1, 1), date(2025, 12, 31), date(2024, 12, 31)
ACCESSION = "0000999999-26-000001"
FILING = FilingRecord(
    issuer="SYNT", cik="0000999999", form="10-K", fiscal_year=2025, start=START,
    start_classification="CALCULATION", end=END, filed=date(2026, 2, 1),
    accession=ACCESSION, primary_document="s.htm", filing_url="https://example/s.htm",
)
M = Decimal(10) ** 6


def millions(n) -> Decimal:
    return Decimal(n) * M


def spec(item="revenue", statement="IS", period="duration", sign="either",
         concepts=("us-gaap:X",)) -> LineItemSpec:
    return LineItemSpec(item, item, statement, period, sign, True, concepts)


def input_spec(input_id="retained_earnings", period="instant", concepts=("us-gaap:RE",),
               dims=(), sign="either") -> ModelInputSpec:
    return ModelInputSpec(input_id, input_id, False, concepts, period, dims, sign, "required")


def row(position, label, texts=("100", "90"), statement="BS", is_total=False, table=1,
        parent=None, periods=(END, PRIOR_END), unit="(in millions)") -> RowInstance:
    return RowInstance(
        row_id=f"{ACCESSION}:{statement}:{position}", accession=ACCESSION, statement=statement,
        label=label, parent_header=parent, indent=0, is_total=is_total, is_bold=is_total,
        position=position, cell_texts=tuple(texts), column_periods=tuple(periods),
        unit_text=unit, table_index=table, source_row=position,
        column_spans=tuple((3 + 6 * i, 5 + 6 * i) for i in range(len(texts))),
    )


def fact(concept, amount, end=END, start=None, dims=None, cell=None, decimals="-6",
         accession=ACCESSION, nil=False) -> TagFact:
    """cell = (table, row, column) or None for a fact outside any table."""
    address = CellAddress(*cell, None) if cell else None
    return TagFact(
        accession=accession, concept=concept, context_id="c", start=start, end=end,
        dimensions=dict(dims or {}), unit="USD", scale=6, sign=1,
        amount=None if nil else Decimal(amount), cell_address=address, decimals=decimals,
        is_nil=nil,
    )


def tag_outcome(status, values, reason="CORROBORATED", item="revenue", concept="us-gaap:X",
                source="companyfacts") -> Outcome:
    readings = tuple(Reading("SYNT", 2025, item, Decimal(v), "tag", status, concept=concept,
                             source=source) for v in values)
    return Outcome(status=status, reason=reason, observations=readings)


def obs(metric, value=None, status="VERIFIED", accepted=True, channels=("document", "tag"),
        reason="AGREE", classification="REPORTED_FACT", flags=(), **fields) -> Observation:
    """A synthetic Observation; value None gives a GAP unless status says otherwise."""
    if value is None and status == "VERIFIED":
        status, accepted, classification, channels = "GAP", False, "EVIDENCE_GAP", ()
        reason = "NEITHER" if reason == "AGREE" else reason
    return base_observation(
        FILING, metric, value=None if value is None else Decimal(value), status=status,
        accepted=accepted, channels=channels, reason=reason, classification=classification,
        flags=flags, concepts=fields.pop("concepts", ("us-gaap:X",)), **fields)


def pending(metric, value, **fields) -> Observation:
    """A document-only value awaiting its identity check."""
    return obs(metric, value, status="SINGLE_CHANNEL", accepted=False, channels=("document",),
               reason="DOCUMENT_ONLY", flags=(PENDING,), **fields)


def tag_only(metric, value, **fields) -> Observation:
    return obs(metric, value, status="SINGLE_CHANNEL", channels=("tag",), reason="TAG_ONLY",
               **fields)
