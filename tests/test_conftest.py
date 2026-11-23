"""Tests for the pinned-filing sample in conftest.py."""
import json

import pytest

from conftest import EVIDENCE_DIR, SAMPLE_FILINGS, pinned_filing_names, pytest_configure
from finagent.config import HELD_OUT, load_universe
from finagent.evidence import EvidenceStore
from finagent.evidence.integrity import load_manifest, read_snapshot
from finagent.evidence.splits import document_splits

# Names only: mapping a document to its split reads the manifest, never the filing.
SPLITS = document_splits(EvidenceStore(EVIDENCE_DIR), load_universe())


def _pinned_documents() -> list[str]:
    return sorted(n for n in load_manifest(EVIDENCE_DIR) if n.endswith(".htm"))


def test_every_sample_filing_is_pinned():
    assert set(SAMPLE_FILINGS) <= set(_pinned_documents())


def test_sample_has_no_duplicates():
    assert len(set(SAMPLE_FILINGS)) == len(SAMPLE_FILINGS)


def test_pinned_filing_names_default_is_the_sample():
    assert pinned_filing_names(False) == list(SAMPLE_FILINGS)


def test_no_sample_filing_belongs_to_a_held_out_company():
    assert all(SPLITS[name] != HELD_OUT for name in SAMPLE_FILINGS)


def test_all_filings_without_flag_yields_no_held_out_filing():
    names = pinned_filing_names(True)
    assert not [n for n in names if SPLITS[n] == HELD_OUT]
    assert names == sorted(n for n, s in SPLITS.items() if s != HELD_OUT)
    assert {SPLITS[n] for n in names} == {"train", "validation"}


def test_include_held_out_adds_exactly_the_held_out_filings():
    every = pinned_filing_names(True, include_held_out=True)
    assert every == _pinned_documents()  # all pinned documents belong to the universe
    added = set(every) - set(pinned_filing_names(True))
    assert added and all(SPLITS[n] == HELD_OUT for n in added)


class _Config:
    def __init__(self, **options: bool) -> None:
        self.options = options

    def getoption(self, name: str) -> bool:
        return self.options[name.lstrip("-").replace("-", "_")]


def test_include_held_out_without_all_filings_is_a_usage_error():
    with pytest.raises(pytest.UsageError):
        pytest_configure(_Config(all_filings=False, include_held_out=True))
    pytest_configure(_Config(all_filings=True, include_held_out=True))  # allowed together
    pytest_configure(_Config(all_filings=False, include_held_out=False))


def test_sample_alphabet_filing_is_only_on_an_older_submissions_page():
    manifest = load_manifest(EVIDENCE_DIR)
    main = json.loads(read_snapshot(EVIDENCE_DIR, "submissions_0001652044.json", manifest))
    older = json.loads(read_snapshot(EVIDENCE_DIR, "CIK0001652044-submissions-001.json", manifest))
    assert "goog-20211231.htm" in SAMPLE_FILINGS
    assert "goog-20211231.htm" not in main["filings"]["recent"]["primaryDocument"]
    assert "goog-20211231.htm" in older["primaryDocument"]
