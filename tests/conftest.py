"""Shared test configuration: which pinned filings the per-filing tests run on.

A test that takes a ``pinned_filing`` argument is parametrized over SAMPLE_FILINGS by
default, and over every pinned 10-K document with ``pytest --all-filings``.
"""
from pathlib import Path

import pytest

from finagent.evidence.integrity import load_manifest

EVIDENCE_DIR = Path(__file__).resolve().parents[1] / "evidence"

# Fixed sample of pinned filings covering known edge cases. Keep the list here only.
SAMPLE_FILINGS: tuple[str, ...] = (
    "amzn-20251231.htm",      # Amazon
    "msft-10k_20220630.htm",  # Microsoft, older document-name pattern
    "fdx-20240531.htm",       # num-dot-decimal with no integer part ('.9')
    "cop-20251231.htm",       # 'nil' number word (a reported zero)
    "eog-20251231.htm",       # leading '+' with no format ('+70')
    "de-20251102x10k.htm",    # decimals without a leading zero, no format (e.g. '.43')
    "goog-20211231.htm",      # listed only on an older submissions page
    "crm-20260131.htm",       # Salesforce FY2026: mislabelled DEI fiscal year
    "hon-20211231.htm",       # Honeywell FY2021: mislabelled DEI fiscal year
)


def pinned_filing_names(all_filings: bool) -> list[str]:
    """Every pinned .htm document when all_filings, else the fixed sample."""
    if all_filings:
        return sorted(n for n in load_manifest(EVIDENCE_DIR) if n.endswith(".htm"))
    return list(SAMPLE_FILINGS)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--all-filings", action="store_true", default=False,
        help="run per-filing tests on every pinned filing, not only the sample",
    )


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "pinned_filing" in metafunc.fixturenames:
        names = pinned_filing_names(metafunc.config.getoption("--all-filings"))
        metafunc.parametrize("pinned_filing", names)
