"""Tests for the pinned-filing sample in conftest.py."""
import json

from conftest import EVIDENCE_DIR, SAMPLE_FILINGS, pinned_filing_names
from finagent.evidence.integrity import load_manifest, read_snapshot


def _pinned_documents() -> list[str]:
    return sorted(n for n in load_manifest(EVIDENCE_DIR) if n.endswith(".htm"))


def test_every_sample_filing_is_pinned():
    assert set(SAMPLE_FILINGS) <= set(_pinned_documents())


def test_sample_has_no_duplicates():
    assert len(set(SAMPLE_FILINGS)) == len(SAMPLE_FILINGS)


def test_pinned_filing_names_default_is_the_sample():
    assert pinned_filing_names(False) == list(SAMPLE_FILINGS)


def test_pinned_filing_names_all_is_every_pinned_document():
    assert pinned_filing_names(True) == _pinned_documents()


def test_sample_alphabet_filing_is_only_on_an_older_submissions_page():
    manifest = load_manifest(EVIDENCE_DIR)
    main = json.loads(read_snapshot(EVIDENCE_DIR, "submissions_0001652044.json", manifest))
    older = json.loads(read_snapshot(EVIDENCE_DIR, "CIK0001652044-submissions-001.json", manifest))
    assert "goog-20211231.htm" in SAMPLE_FILINGS
    assert "goog-20211231.htm" not in main["filings"]["recent"]["primaryDocument"]
    assert "goog-20211231.htm" in older["primaryDocument"]
