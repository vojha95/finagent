from datetime import date
from pathlib import Path

from finagent.evidence import EvidenceStore
from finagent.evidence.integrity import read_snapshot
from finagent.filing_rows import read_filing_rows
from finagent.ixbrl import tagged_table_counts

EVIDENCE_DIR = Path(__file__).resolve().parents[1] / "evidence"


def test_read_filing_rows_locates_with_the_tagged_cell_map():
    store = EvidenceStore(EVIDENCE_DIR)
    record = store.load_filings("AAPL", date(2026, 12, 31))[0]
    document = read_snapshot(EVIDENCE_DIR, record.primary_document, store.manifest)
    filing = read_filing_rows(document, record.accession)
    assert filing.tagged_cells == tagged_table_counts(document, filing.clean, filing.facts)
    assert set(filing.rows) == {"IS", "BS", "CF"}
    # Every located table holds tagged cells.
    assert all(filing.tagged_cells.get(r.table_index, 0) > 0
               for rows in filing.rows.values() for r in rows)
