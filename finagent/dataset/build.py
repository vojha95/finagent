"""Row dataset: every face-statement row of every pinned filing, labelled and split (9.4, 9.6).

Usage:
    python -m finagent.dataset.build --version v1 --as-of 2026-11-24 [--include-held-out]
writes datasets/rows_v1.parquet, its reproducibility record datasets/rows_v1.meta.json and the
measured counts docs/dataset_v1.md (Phase 2 exit criterion; finagent.dataset.report).
Held-out companies are left out unless --include-held-out is passed (final E1 run only, 9.2);
their filings are not even opened.
"""

import argparse
import hashlib
import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from finagent.config import HELD_OUT, SPLITS, LineItemSpec, load_concept_map, load_universe
from finagent.dataset.labels import concept_owner, label_rows
from finagent.evidence.integrity import EvidenceIntegrityError, read_snapshot
from finagent.evidence.store import EvidenceStore, FilingRecord
from finagent.dataset.report import write_report
from finagent.document import gap_reason
from finagent.filing_rows import read_filing_rows
from finagent.ixbrl import parse_document

ROOT = Path(__file__).resolve().parents[2]
DATASETS_DIR = ROOT / "datasets"
STATEMENTS = ("IS", "BS", "CF")


@dataclass
class DatasetBuild:
    rows: pd.DataFrame
    skipped: dict[str, str] = field(default_factory=dict)  # ticker -> reason
    missing_statements: list[tuple[str, int, str]] = field(default_factory=list)
    location_gaps: list[tuple[str, int, str]] = field(default_factory=list)  # GAP reason codes
    held_out_excluded: list[str] = field(default_factory=list)  # never opened (9.2)
    filings: dict[str, int] = field(default_factory=dict)  # ticker -> pinned filings read


def filing_rows(
    store: EvidenceStore, record: FilingRecord, split: str, owner: dict[str, str]
) -> tuple[list[dict], list[str], str | None]:
    """Labelled row dicts for one filing, the statements not located and the GAP reason code."""
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
    missing = [s for s in STATEMENTS if s not in rows]
    reason = gap_reason(parse_document(filing.clean), rows) if missing else None
    return records, missing, reason


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
        build.filings[ticker] = len(filings)
        for record in filings:
            rows, missing, reason = filing_rows(store, record, split, owner)
            records.extend(rows)
            build.missing_statements.extend((ticker, record.fiscal_year, s) for s in missing)
            if reason is not None:
                build.location_gaps.append((ticker, record.fiscal_year, reason))
    build.rows = pd.DataFrame.from_records(records)
    return build


def measured_counts(rows: pd.DataFrame) -> pd.DataFrame:
    """Rows per split, labelled vs OTHER vs excluded: the counts 9.3 asks to replace the guess."""
    if rows.empty:
        return pd.DataFrame()
    kind = rows["item"].map(lambda i: "excluded" if i is None else "OTHER" if i == "OTHER" else "item")
    return pd.crosstab(rows["split"], kind, margins=True)


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(["git", *args], capture_output=True, text=True, cwd=root)
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def universe_counts(build: DatasetBuild, universe: dict[str, str]) -> dict[str, dict[str, int]]:
    """Per split: companies configured, companies whose evidence was read, filings, rows."""
    rows_by_split = build.rows["split"].value_counts() if not build.rows.empty else {}
    counts = {}
    for split in SPLITS:
        tickers = [t for t, s in universe.items() if s == split]
        read = [t for t in tickers if t in build.filings]
        counts[split] = {
            "companies": len(tickers),
            "companies_read": len(read),
            "filings": sum(build.filings[t] for t in read),
            "rows": int(rows_by_split.get(split, 0)),
        }
    return counts


def dataset_meta(
    build: DatasetBuild, universe: dict[str, str], version: str, as_of: date,
    evidence_dir: Path, root: Path = ROOT,
) -> dict[str, Any]:
    """Reproducibility record for a dataset build: code, evidence, as_of, universe and counts."""
    manifest = (Path(evidence_dir) / "manifest.json").read_bytes()
    status = _git(root, "status", "--porcelain")
    return {
        "version": version,
        "git_commit": _git(root, "rev-parse", "HEAD"),
        "git_dirty": None if status is None else bool(status),
        "evidence_manifest_sha256": hashlib.sha256(manifest).hexdigest(),
        "as_of": as_of.isoformat(),
        "universe": universe_counts(build, universe),
        "row_count": len(build.rows),
        "missing_statements": [list(m) for m in build.missing_statements],
        "location_gaps": [list(g) for g in build.location_gaps],
        "skipped": build.skipped,
    }


def write_meta(path: Path, meta: dict[str, Any]) -> None:
    path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", required=True)
    parser.add_argument("--evidence", default=str(ROOT / "evidence"))
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--include-held-out", action="store_true",
                        help="add held-out companies (final E1 run only)")
    args = parser.parse_args(argv)

    universe = load_universe()
    build = build_dataset(EvidenceStore(args.evidence), universe, args.as_of,
                          include_held_out=args.include_held_out)
    meta = dataset_meta(build, universe, args.version, args.as_of, Path(args.evidence))
    DATASETS_DIR.mkdir(exist_ok=True)
    path = DATASETS_DIR / f"rows_{args.version}.parquet"
    build.rows.to_parquet(path, index=False)
    write_meta(DATASETS_DIR / f"rows_{args.version}.meta.json", meta)
    print(f"Wrote {len(build.rows)} rows to {path}")
    print(measured_counts(build.rows).to_string())
    if build.missing_statements:
        print(f"Statements not located: {build.missing_statements}")
    print(f"Tickers without pinned evidence ({len(build.skipped)}): {sorted(build.skipped)}")
    if build.held_out_excluded:
        print(f"Held-out companies excluded, not opened: {len(build.held_out_excluded)}")
    if not args.include_held_out:  # the report covers train and validation only
        print(f"Wrote {write_report(args.version, Path(args.evidence), DATASETS_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
