"""Phase 1 exit report: statements found, tagged statement cells mapped, no leaks (blueprint 15).

Exit criterion: "For 3 companies x 5 years: statements found; >= 95% of tagged statement cells
mapped to rows; leakage test passes." A filing passes when IS, BS and CF are all located, each
statement on its own reaches >= 95% coverage of a non-empty set of tagged cells, and the clean
document leaks nothing.

Usage:
    python -m finagent.evaluation.phase1 [--tickers AMZN MSFT] [--include-held-out]
prints a per-company summary and writes datasets/phase1_report.csv. Held-out companies
(blueprint 9.2) are excluded unless --include-held-out is passed.

Lives in evaluation, not document: it reads the tag store, which the document package may not
import (rule 7).
"""

import argparse
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

# HeldOutError is re-exported for callers of this module; the guard lives in finagent.config.
from finagent.config import HELD_OUT, HeldOutError, check_not_held_out, load_universe  # noqa: F401
from finagent.document import cell_coverage, gap_reason, label_cell_addresses
from finagent.document.coverage import CellAddress, Coverage
from finagent.evidence.integrity import EvidenceIntegrityError, read_snapshot
from finagent.evidence.store import EvidenceStore, FilingRecord
from finagent.filing_rows import read_filing_rows
from finagent.ixbrl import (
    TagFact,
    declared_prefixes,
    find_leaks,
    ix_concept_names,
    leak_terms,
    parse_document,
)
from finagent.records import Statement

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "datasets" / "phase1_report.csv"
STATEMENTS: tuple[Statement, ...] = ("IS", "BS", "CF")
MIN_SHARE = 0.95


@dataclass(frozen=True)
class FilingReport:
    ticker: str
    split: str
    fiscal_year: int
    accession: str
    document: str
    coverage: dict[Statement, Coverage | None]  # None: statement not located (a gap, not 0)
    leaks: tuple[str, ...]
    error: str | None = None  # the filing could not be processed; reported, never a pass
    # GAP reason code when a statement is not located (e.g. STATEMENTS_INCORPORATED_BY_REFERENCE)
    gap_reason: str | None = None
    tables: dict[Statement, tuple[int, ...]] | None = None  # located table indices per statement

    @property
    def located(self) -> tuple[Statement, ...]:
        return tuple(s for s in STATEMENTS if self.coverage.get(s) is not None)

    @property
    def covered(self) -> int:
        return sum(c.covered for c in self.coverage.values() if c is not None)

    @property
    def total(self) -> int:
        return sum(c.total for c in self.coverage.values() if c is not None)

    @property
    def share(self) -> float | None:
        return self.covered / self.total if self.total else None

    def statement_passes(self, statement: Statement) -> bool:
        c = self.coverage.get(statement)
        return c is not None and c.total > 0 and c.share is not None and c.share >= MIN_SHARE

    @property
    def passed(self) -> bool:
        return (
            self.error is None
            and all(self.statement_passes(s) for s in STATEMENTS)
            and not self.leaks
        )


def tagged_statement_cells(
    facts: Iterable[TagFact], label_cells: set[CellAddress]
) -> list[CellAddress]:
    """Addresses of tagged figures, excluding nil facts and numbers written inside a label
    (share counts, par values, allowances), which are not statement figures."""
    cells = (
        (f.cell_address.table_index, f.cell_address.row_index, f.cell_address.column_index)
        for f in facts
        if f.cell_address is not None and not f.is_nil
    )
    return [a for a in cells if a not in label_cells]


def leak_check(document: bytes, clean: bytes, facts: Iterable[TagFact]) -> list[str]:
    """Concept names, axes, members and prefixes of the filing found in its clean document."""
    return find_leaks(
        clean, leak_terms(facts) | ix_concept_names(document), declared_prefixes(document)
    )


def filing_report(store: EvidenceStore, record: FilingRecord, ticker: str, split: str) -> FilingReport:
    """Locate, build rows, measure coverage per statement and run the leakage test for one filing."""
    document = read_snapshot(store.evidence_dir, record.primary_document, store.manifest)
    filing = read_filing_rows(document, record.accession, store.fiscal_periods(record))
    clean, facts, rows = filing.clean, filing.facts, filing.rows
    root = parse_document(clean)
    coverage: dict[Statement, Coverage | None] = {}
    for statement in STATEMENTS:
        statement_rows = rows.get(statement)
        if not statement_rows:
            coverage[statement] = None
            continue
        labels = label_cell_addresses(root, {r.table_index for r in statement_rows})
        coverage[statement] = cell_coverage(statement_rows, tagged_statement_cells(facts, labels))
    return FilingReport(
        ticker=ticker,
        split=split,
        fiscal_year=record.fiscal_year,
        accession=record.accession,
        document=record.primary_document,
        coverage=coverage,
        leaks=tuple(leak_check(document, clean, facts)),
        gap_reason=gap_reason(root, rows),
        tables={s: tuple(sorted({r.table_index for r in rs})) for s, rs in rows.items()},
    )


def safe_filing_report(
    store: EvidenceStore, record: FilingRecord, ticker: str, split: str
) -> FilingReport:
    """filing_report, or a failing report carrying the error, so one bad filing does not stop
    the run and is not silently dropped."""
    try:
        return filing_report(store, record, ticker, split)
    except Exception as exc:  # noqa: BLE001 - every failure is reported in the table
        return FilingReport(
            ticker=ticker, split=split, fiscal_year=record.fiscal_year,
            accession=record.accession, document=record.primary_document,
            coverage={s: None for s in STATEMENTS}, leaks=(),
            error=f"{type(exc).__name__}: {exc}",
        )


def select_tickers(
    universe: dict[str, str], requested: Sequence[str] | None, include_held_out: bool
) -> dict[str, str]:
    """Ticker -> split to report on. Default: every non-held-out universe ticker.

    Raises HeldOutError for a requested held-out ticker without include_held_out, and KeyError
    for a ticker outside the universe.
    """
    if not requested:
        return {t: s for t, s in universe.items() if include_held_out or s != HELD_OUT}
    selected: dict[str, str] = {}
    for raw in requested:
        ticker = raw.strip().upper()
        if ticker not in universe:
            raise KeyError(f"{ticker} is not in the universe")
        check_not_held_out([ticker], universe, include_held_out)
        selected[ticker] = universe[ticker]
    return selected


def phase1_report(
    store: EvidenceStore, tickers: dict[str, str], as_of: date
) -> tuple[list[FilingReport], dict[str, str]]:
    """Reports for every pinned filing of the tickers, and the tickers skipped with the reason."""
    reports: list[FilingReport] = []
    skipped: dict[str, str] = {}
    for ticker, split in tickers.items():
        try:
            filings = store.load_filings(ticker, as_of)
        except (EvidenceIntegrityError, KeyError) as exc:
            skipped[ticker] = f"{type(exc).__name__}: {exc}"
            continue
        reports.extend(safe_filing_report(store, record, ticker, split) for record in filings)
    return reports, skipped


def report_frame(reports: Iterable[FilingReport]) -> pd.DataFrame:
    """One row per filing: located flags, covered/total/share per statement and overall, leaks."""
    records = []
    for r in reports:
        record: dict[str, object] = {
            "ticker": r.ticker,
            "split": r.split,
            "fiscal_year": r.fiscal_year,
            "accession": r.accession,
            "document": r.document,
            "located": "/".join(r.located),
        }
        for s in STATEMENTS:
            c = r.coverage.get(s)
            record[f"{s}_located"] = c is not None
            record[f"{s}_covered"] = c.covered if c is not None else None
            record[f"{s}_total"] = c.total if c is not None else None
            record[f"{s}_share"] = c.share if c is not None else None
            record[f"{s}_pass"] = r.statement_passes(s)
            located = (r.tables or {}).get(s)
            record[f"{s}_tables"] = " ".join(map(str, located)) if located else None
        record.update(
            covered=r.covered,
            total=r.total,
            share=r.share,
            leaks=len(r.leaks),
            leak_terms=";".join(r.leaks[:10]),
            error=r.error,
            gap_reason=r.gap_reason,
            passed=r.passed,
        )
        records.append(record)
    return pd.DataFrame.from_records(records)


def company_summary(frame: pd.DataFrame) -> pd.DataFrame:
    """Per ticker: split, filings passed of filings pinned, lowest statement share, total leaks."""
    if frame.empty:
        return pd.DataFrame()
    share_cols = [f"{s}_share" for s in STATEMENTS]
    work = frame.assign(min_share=frame[share_cols].min(axis=1, skipna=False))
    grouped = work.groupby("ticker", sort=False)
    return pd.DataFrame({
        "split": grouped["split"].first(),
        "years": grouped["fiscal_year"].agg(lambda y: f"{y.min()}-{y.max()}"),
        "passed": grouped["passed"].sum().astype(int),
        "filings": grouped.size(),
        "min_share": grouped["min_share"].min(),
        "not_located": grouped["located"].agg(lambda l: sum(x != "IS/BS/CF" for x in l)),
        "leaks": grouped["leaks"].sum(),
        "errors": grouped["error"].agg(lambda e: int(e.notna().sum())),
        "gap_reasons": grouped["gap_reason"].agg(
            lambda g: ";".join(sorted(set(g.dropna()))) or None
        ),
    })


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tickers", nargs="*", default=None)
    parser.add_argument("--include-held-out", action="store_true")
    parser.add_argument("--evidence", default=str(ROOT / "evidence"))
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)

    tickers = select_tickers(load_universe(), args.tickers, args.include_held_out)
    reports, skipped = phase1_report(EvidenceStore(args.evidence), tickers, args.as_of)
    frame = report_frame(reports)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False)

    with pd.option_context("display.max_rows", None, "display.width", 200):
        print(company_summary(frame).to_string(float_format=lambda v: f"{v:.4f}"))
    print(f"\n{int(frame['passed'].sum()) if not frame.empty else 0} / {len(frame)} filings pass")
    failures = frame[~frame["passed"]] if not frame.empty else frame
    if not failures.empty:
        cols = ["ticker", "fiscal_year", "document", "located",
                *(f"{s}_share" for s in STATEMENTS), "leaks", "gap_reason", "error"]
        print("\nFailing filings:")
        print(failures[cols].to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    if skipped:
        print(f"Skipped ({len(skipped)}): {skipped}")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
