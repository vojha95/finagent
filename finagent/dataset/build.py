"""Row dataset: every face-statement row of every pinned filing, labelled and split (9.4, 9.6).

Usage:
    python -m finagent.dataset.build --version v1 [--include-held-out]
writes datasets/rows_v1.parquet and prints the measured counts (Phase 2 exit criterion).
Held-out companies are left out unless --include-held-out is passed (final E1 run only, 9.2);
their filings are not even opened.
"""

import argparse
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from finagent.config import HELD_OUT, LineItemSpec, load_concept_map, load_universe
from finagent.dataset.labels import concept_owner, label_rows
from finagent.evidence.integrity import EvidenceIntegrityError, read_snapshot
from finagent.evidence.store import EvidenceStore, FilingRecord
from finagent.filing_rows import read_filing_rows

DATASETS_DIR = Path(__file__).resolve().parents[2] / "datasets"
STATEMENTS = ("IS", "BS", "CF")


@dataclass
class DatasetBuild:
    rows: pd.DataFrame
    skipped: dict[str, str] = field(default_factory=dict)  # ticker -> reason
    missing_statements: list[tuple[str, int, str]] = field(default_factory=list)
    held_out_excluded: list[str] = field(default_factory=list)  # never opened (9.2)


def filing_rows(
    store: EvidenceStore, record: FilingRecord, split: str, owner: dict[str, str]
) -> tuple[list[dict], list[str]]:
    """Labelled row dicts for one filing, and the statements that could not be located."""
    document = read_snapshot(store.evidence_dir, record.primary_document, store.manifest)
    filing = read_filing_rows(document, record.accession)
    rows, facts = filing.rows, filing.facts
    records = []
    for statement_rows in rows.values():
        for row, label in label_rows(statement_rows, facts, owner):
            records.append({
                "issuer": record.issuer,
                "cik": record.cik,
                "fiscal_year": record.fiscal_year,
                "accession": record.accession,
                "split": split,
                "statement": row.statement,
                "row_id": row.row_id,
                "position": row.position,
                "row_text": row.label,
                "parent_header": row.parent_header,
                "indent": row.indent,
                "is_total": row.is_total,
                "is_bold": row.is_bold,
                "cell_texts": list(row.cell_texts),
                "column_periods": [p.isoformat() if p else None for p in row.column_periods],
                "column_kinds": list(row.column_kinds) or ["amount"] * len(row.cell_texts),
                "unit_text": row.unit_text,
                "table_index": row.table_index,
                "source_row": row.source_row,
                "item": label.label,
                "label_reason": label.reason,
                "label_concepts": list(label.concepts),  # audit only, never a feature
            })
    return records, [s for s in STATEMENTS if s not in rows]


def build_dataset(
    store: EvidenceStore,
    universe: dict[str, str],
    as_of: date,
    concept_map: dict[str, LineItemSpec] | None = None,
    include_held_out: bool = False,
) -> DatasetBuild:
    """Rows for every universe ticker with pinned evidence; the others are reported, not guessed.

    Held-out tickers are skipped, unopened, unless include_held_out (final E1 run only, 9.2).
    """
    owner = concept_owner(concept_map if concept_map is not None else load_concept_map())
    records: list[dict] = []
    build = DatasetBuild(rows=pd.DataFrame())
    for ticker, split in universe.items():
        if split == HELD_OUT and not include_held_out:
            build.held_out_excluded.append(ticker)
            continue
        try:
            filings = store.load_filings(ticker, as_of)
        except (EvidenceIntegrityError, KeyError) as exc:
            build.skipped[ticker] = f"{type(exc).__name__}: {exc}"
            continue
        for record in filings:
            rows, missing = filing_rows(store, record, split, owner)
            records.extend(rows)
            build.missing_statements.extend((ticker, record.fiscal_year, s) for s in missing)
    build.rows = pd.DataFrame.from_records(records)
    return build


def measured_counts(rows: pd.DataFrame) -> pd.DataFrame:
    """Rows per split, labelled vs OTHER vs excluded: the counts 9.3 asks to replace the guess."""
    if rows.empty:
        return pd.DataFrame()
    kind = rows["item"].map(lambda i: "excluded" if i is None else "OTHER" if i == "OTHER" else "item")
    return pd.crosstab(rows["split"], kind, margins=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", required=True)
    parser.add_argument("--evidence", default=str(Path(__file__).resolve().parents[2] / "evidence"))
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--include-held-out", action="store_true",
                        help="add held-out companies (final E1 run only)")
    args = parser.parse_args(argv)

    build = build_dataset(EvidenceStore(args.evidence), load_universe(), args.as_of,
                          include_held_out=args.include_held_out)
    DATASETS_DIR.mkdir(exist_ok=True)
    path = DATASETS_DIR / f"rows_{args.version}.parquet"
    build.rows.to_parquet(path, index=False)
    print(f"Wrote {len(build.rows)} rows to {path}")
    print(measured_counts(build.rows).to_string())
    if build.missing_statements:
        print(f"Statements not located: {build.missing_statements}")
    print(f"Tickers without pinned evidence ({len(build.skipped)}): {sorted(build.skipped)}")
    if build.held_out_excluded:
        print(f"Held-out companies excluded, not opened: {len(build.held_out_excluded)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
