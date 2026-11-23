"""Pinned filing documents by company split (blueprint 9.2), without reading any filing.

A document belongs to the universe company whose CIK appears in its pinned SEC URL
(/edgar/data/{cik}/...). Only the manifest and the pinned ticker map are read, so held-out
filings are never opened to find out that they are held out.
"""

import re
from collections.abc import Mapping

from finagent.evidence.store import EvidenceStore

_ARCHIVE_CIK = re.compile(r"/Archives/edgar/data/(\d+)/")


def document_cik(url: str) -> int | None:
    """Issuer CIK from an EDGAR archive URL, or None for any other URL."""
    match = _ARCHIVE_CIK.search(url)
    return int(match.group(1)) if match else None


def document_splits(store: EvidenceStore, universe: Mapping[str, str]) -> dict[str, str]:
    """Pinned .htm document -> split of its universe company.

    Documents of issuers outside the universe, and universe tickers without a known CIK, are
    left out (a caller that needs every document must treat absence as "not allowed").
    """
    split_by_cik: dict[int, str] = {}
    for ticker, split in universe.items():
        try:
            split_by_cik[int(store.resolve_cik(ticker))] = split
        except KeyError:
            continue
    splits: dict[str, str] = {}
    for name, entry in store.manifest.items():
        if not name.endswith(".htm"):
            continue
        cik = document_cik(str(entry.get("url", "")))
        if cik in split_by_cik:
            splits[name] = split_by_cik[cik]
    return splits
