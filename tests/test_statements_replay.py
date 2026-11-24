"""Evidence views for E8 replays (#119): the default path is unchanged, hidden evidence is empty."""

from datetime import date
from pathlib import Path
from types import SimpleNamespace

from statements_support import fact, row

from finagent.evidence import EvidenceStore
from finagent.evidence.integrity import read_snapshot
from finagent.filing_rows import read_filing_rows
from finagent.statements.replay import (ABSENT, E8_CONDITIONS, FULL, NO_COMPANYFACTS,
                                        NO_DOCUMENT, UNTAGGED, EvidenceView, untagged_nci,
                                        view_companyfacts, view_facts, view_rows)

EVIDENCE_DIR = Path(__file__).resolve().parents[1] / "evidence"
NCI = ((("us-gaap:MinorityInterest",), "noncontrolling interest"),
       (("us-gaap:NetIncomeLossAttributableToNoncontrollingInterest",), "NCI net income"))


def test_view_properties_and_names():
    assert FULL.complete and not FULL.untagged and not FULL.single_channel
    assert FULL.name == "complete"
    assert not NO_COMPANYFACTS.single_channel and NO_COMPANYFACTS.name == "hidden:companyfacts"
    assert UNTAGGED.untagged and UNTAGGED.single_channel
    assert UNTAGGED.name == "hidden:companyfacts+inline_tags"
    assert NO_DOCUMENT.single_channel and NO_DOCUMENT.name == "hidden:document"
    assert E8_CONDITIONS == (FULL, NO_COMPANYFACTS, UNTAGGED, NO_DOCUMENT)
    assert len({FULL, EvidenceView(), UNTAGGED}) == 2  # hashable: a cache key


def test_view_helpers_hide_only_their_evidence():
    facts, rows, cf = [fact("us-gaap:X", 1)], [row(1, "Cash")], {"facts": {}}
    assert view_companyfacts(FULL, cf) is cf and view_companyfacts(NO_COMPANYFACTS, cf) == {}
    assert view_facts(FULL, facts) is facts and view_facts(UNTAGGED, facts) == []
    assert view_facts(NO_COMPANYFACTS, facts) is facts
    assert view_rows(FULL, rows) is rows and view_rows(NO_DOCUMENT, rows) == []


def test_untagged_nci_follows_the_visible_label_rule():
    plain = [row(1, "Total equity", statement="BS"), row(2, "Net income", statement="IS")]
    read = {"revenue": SimpleNamespace(value=1)}
    assert untagged_nci(read, plain, NCI) == {c: ABSENT for c, _ in NCI}
    labelled = plain + [row(3, "Net income attributable to noncontrolling interests",
                            statement="IS")]
    assert untagged_nci(read, labelled, NCI) == {}
    assert untagged_nci({"total_equity": SimpleNamespace(value=5)}, plain, NCI) == {}
    assert untagged_nci({"total_equity": SimpleNamespace(value=None)}, plain, NCI) != {}


def test_read_filing_rows_untagged_reads_no_tags():
    store = EvidenceStore(EVIDENCE_DIR)
    record = store.load_filings("AAPL", date(2026, 12, 31))[0]
    document = read_snapshot(EVIDENCE_DIR, record.primary_document, store.manifest)
    tagged = read_filing_rows(document, record.accession)
    untagged = read_filing_rows(document, record.accession, tagged=False)
    assert untagged.facts == [] and untagged.tagged_cells == {}
    assert untagged.clean == tagged.clean
    assert tagged.facts and tagged.tagged_cells
