from finagent.evidence.splits import document_cik, document_splits


class _Store:
    """Just enough of EvidenceStore: a manifest and a ticker -> CIK map."""

    def __init__(self, manifest: dict, ciks: dict[str, str]) -> None:
        self.manifest, self._ciks = manifest, ciks

    def resolve_cik(self, ticker: str) -> str:
        if ticker not in self._ciks:
            raise KeyError(ticker)
        return self._ciks[ticker]


def _entry(cik: int, name: str) -> dict:
    return {"url": f"https://www.sec.gov/Archives/edgar/data/{cik}/000/{name}"}


def test_document_cik():
    assert document_cik("https://www.sec.gov/Archives/edgar/data/1018724/0001/a.htm") == 1018724
    assert document_cik("https://www.sec.gov/files/company_tickers.json") is None


def test_document_splits_by_issuer_cik_not_file_name():
    manifest = {
        "abc-2025.htm": _entry(11, "abc-2025.htm"),
        "zzz-2025.htm": _entry(22, "zzz-2025.htm"),  # held-out issuer, misleading name
        "other.htm": _entry(33, "other.htm"),  # issuer outside the universe
        "submissions_0000000011.json": {"url": "https://data.sec.gov/x.json"},
    }
    store = _Store(manifest, {"AAA": "0000000011", "BBB": "0000000022"})
    universe = {"AAA": "train", "BBB": "held_out", "CCC": "validation"}  # CCC: no known CIK
    assert document_splits(store, universe) == {
        "abc-2025.htm": "train", "zzz-2025.htm": "held_out",
    }
