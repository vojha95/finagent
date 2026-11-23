"""Shared test configuration: which pinned filings the per-filing tests run on.

A test that takes a ``pinned_filing`` argument is parametrized over SAMPLE_FILINGS by
default, and with ``pytest --all-filings`` over every pinned 10-K document of the train and
validation companies. Held-out companies (blueprint 9.2) are never used for debugging:
``--include-held-out`` adds their filings and is for the final E1 run only. Filings are
mapped to splits by issuer CIK (finagent.evidence.splits), so no held-out filing is opened to
find out that it is held out.
"""
from pathlib import Path

import pytest

from finagent.config import HELD_OUT, load_universe
from finagent.evidence import EvidenceStore
from finagent.evidence.splits import document_splits

EVIDENCE_DIR = Path(__file__).resolve().parents[1] / "evidence"

# Fixed sample of train and validation filings covering known edge cases. Keep the list here
# only; tests/test_conftest.py checks that none belongs to a held-out company.
SAMPLE_FILINGS: tuple[str, ...] = (
    "amzn-20251231.htm",      # Amazon
    "msft-10k_20220630.htm",  # Microsoft, older document-name pattern
    "cop-20251231.htm",       # 'nil' number word (a reported zero)
    "eog-20251231.htm",       # leading '+' with no format ('+70')
    "de-20251102x10k.htm",    # decimals without a leading zero, no format (e.g. '.43')
    "goog-20211231.htm",      # listed only on an older submissions page
    "crm-20260131.htm",       # Salesforce FY2026: mislabelled DEI fiscal year
    "hon-20211231.htm",       # Honeywell FY2021: mislabelled DEI fiscal year
)


def pinned_filing_names(all_filings: bool, include_held_out: bool = False) -> list[str]:
    """The fixed sample, or with all_filings every pinned train and validation document.

    include_held_out adds the held-out documents (final E1 run only). A document whose issuer
    is outside the universe is never included.
    """
    if not all_filings:
        return list(SAMPLE_FILINGS)
    splits = document_splits(EvidenceStore(EVIDENCE_DIR), load_universe())
    return sorted(n for n, s in splits.items() if include_held_out or s != HELD_OUT)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--all-filings", action="store_true", default=False,
        help="run per-filing tests on every pinned train and validation filing",
    )
    parser.addoption(
        "--include-held-out", action="store_true", default=False,
        help="with --all-filings, add the held-out filings (final E1 run only)",
    )


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("--include-held-out") and not config.getoption("--all-filings"):
        raise pytest.UsageError("--include-held-out is only valid with --all-filings")


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "pinned_filing" in metafunc.fixturenames:
        names = pinned_filing_names(
            metafunc.config.getoption("--all-filings"),
            metafunc.config.getoption("--include-held-out"),
        )
        metafunc.parametrize("pinned_filing", names)
