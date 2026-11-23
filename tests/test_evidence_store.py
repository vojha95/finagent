import gzip
import hashlib
import json
import shutil
from dataclasses import fields
from datetime import date, timedelta
from pathlib import Path

import pytest

from finagent.evidence.integrity import (
    EvidenceIntegrityError,
    EvidenceNotPinnedError,
    InsufficientFilingsError,
    load_manifest,
    read_snapshot,
    read_snapshot_json,
)
from finagent.evidence.store import (
    EvidenceStore,
    FilingRecord,
    build_filing_url,
    period_length_ok,
    select_annual_filings,
)


EVIDENCE_DIR = Path(__file__).resolve().parents[1] / "evidence"
LATEST = date(2026, 12, 31)


# ---------- helpers ----------

def _copy_evidence(tmp_path: Path) -> Path:
    target = tmp_path / "evidence"
    shutil.copytree(EVIDENCE_DIR, target)
    return target


def _rewrite_manifest(evidence_dir: Path, edit) -> None:
    path = evidence_dir / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    edit(manifest)
    path.write_text(json.dumps(manifest), encoding="utf-8")


def _pin(evidence_dir: Path, manifest: dict, name: str, payload: bytes, url: str) -> None:
    (evidence_dir / f"{name}.gz").write_bytes(gzip.compress(payload))
    manifest[name] = {
        "file": f"{name}.gz",
        "url": url,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _submissions(rows: list[tuple[str, str, str, str, str]]) -> dict:
    columns = ("form", "reportDate", "filingDate", "accessionNumber", "primaryDocument")
    return {"filings": {"recent": {c: [row[i] for row in rows] for i, c in enumerate(columns)}}}


# Synthetic retailer with 52/53-week years ending in late Jan / early Feb.
# Each year is labelled by the calendar year in which it began, so the DEI
# fiscal year is one less than the calendar year of the period end.
SYNTH_TICKER = "RETL"
SYNTH_CIK = 9999999
SYNTH_PERIODS = [  # (period end, DEI fiscal year)
    (date(2020, 2, 1), 2019),   # preceding year: listed in submissions, not pinned
    (date(2021, 1, 30), 2020),
    (date(2022, 1, 29), 2021),
    (date(2023, 1, 28), 2022),
    (date(2024, 2, 3), 2023),   # 53-week year
    (date(2025, 2, 1), 2024),
]


def _build_synthetic_issuer(
    tmp_path: Path,
    periods: list[tuple[date, int]] = SYNTH_PERIODS,
    unpinned: frozenset[int] = frozenset({0}),
) -> Path:
    """Pinned evidence for a synthetic issuer; `unpinned` are period indexes listed only."""
    evidence_dir = tmp_path / "synthetic"
    evidence_dir.mkdir()
    manifest: dict = {}
    tickers = {"0": {"cik_str": SYNTH_CIK, "ticker": SYNTH_TICKER, "title": "Synthetic Retail"}}
    _pin(evidence_dir, manifest, "company_tickers.json",
         json.dumps(tickers).encode(), "https://www.sec.gov/files/company_tickers.json")

    rows = []
    for index, (period_end, fiscal_year) in enumerate(periods):
        accession = f"0009999999-{period_end.year % 100:02d}-{index + 1:06d}"
        document = f"retl-{period_end:%Y%m%d}.htm"
        filed = period_end + timedelta(days=40)
        rows.append(("10-K", period_end.isoformat(), filed.isoformat(), accession, document))
        if index in unpinned:
            continue
        body = (
            '<html><body><ix:nonNumeric name="dei:DocumentFiscalYearFocus" contextRef="c1">'
            f"{fiscal_year}</ix:nonNumeric></body></html>"
        ).encode()
        _pin(evidence_dir, manifest, document, body,
             build_filing_url(str(SYNTH_CIK), accession, document))

    cik = f"{SYNTH_CIK:010d}"
    _pin(evidence_dir, manifest, f"submissions_{cik}.json",
         json.dumps(_submissions(rows)).encode(),
         f"https://data.sec.gov/submissions/CIK{cik}.json")
    (evidence_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return evidence_dir


# ---------- integrity ----------

def test_corrupted_snapshot_byte_fails_hash_check(tmp_path):
    evidence_dir = _copy_evidence(tmp_path)
    snapshot = evidence_dir / "company_tickers.json.gz"
    payload = bytearray(gzip.decompress(snapshot.read_bytes()))
    payload[len(payload) // 2] ^= 0x01
    snapshot.write_bytes(gzip.compress(bytes(payload)))

    with pytest.raises(EvidenceIntegrityError, match="SHA-256 mismatch"):
        read_snapshot(evidence_dir, "company_tickers.json")


def test_corrupted_gzip_byte_fails_integrity(tmp_path):
    evidence_dir = _copy_evidence(tmp_path)
    snapshot = evidence_dir / "company_tickers.json.gz"
    raw = bytearray(snapshot.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    snapshot.write_bytes(bytes(raw))

    with pytest.raises(EvidenceIntegrityError):
        read_snapshot(evidence_dir, "company_tickers.json")


def test_load_manifest_missing_raises(tmp_path):
    with pytest.raises(EvidenceIntegrityError, match="Cannot load evidence manifest"):
        load_manifest(tmp_path)


def test_read_snapshot_json_rejects_non_json(tmp_path):
    manifest: dict = {}
    _pin(tmp_path, manifest, "not-json.json", b"<html></html>", "https://example.invalid/")
    with pytest.raises(EvidenceIntegrityError, match="not valid UTF-8 JSON"):
        read_snapshot_json(tmp_path, "not-json.json", manifest)


def test_read_snapshot_json_parses_pinned_json():
    data = read_snapshot_json(EVIDENCE_DIR, "company_tickers.json")
    assert isinstance(data, dict) and data


# ---------- CIK ----------

@pytest.mark.parametrize(("ticker", "expected"), [("AMZN", "0001018724"), ("MSFT", "0000789019")])
def test_resolve_cik(ticker, expected):
    assert EvidenceStore(EVIDENCE_DIR).resolve_cik(ticker) == expected


def test_resolve_cik_unknown_ticker_raises():
    with pytest.raises(KeyError):
        EvidenceStore(EVIDENCE_DIR).resolve_cik("NOT-A-TICKER")


# ---------- URL ----------

def test_build_filing_url():
    assert build_filing_url("0001018724", "0001018724-25-000004", "amzn-20241231.htm") == (
        "https://www.sec.gov/Archives/edgar/data/1018724/000101872425000004/amzn-20241231.htm"
    )


def test_manifest_url_mismatch_raises(tmp_path):
    evidence_dir = _copy_evidence(tmp_path)

    def tamper(manifest):
        manifest["amzn-20231231.htm"]["url"] = manifest["amzn-20231231.htm"]["url"].replace(
            "000101872424000008", "000101872424999999"
        )

    _rewrite_manifest(evidence_dir, tamper)
    with pytest.raises(EvidenceIntegrityError, match="does not match"):
        EvidenceStore(evidence_dir).load_filings("AMZN", LATEST)


# ---------- selection ----------

def test_select_annual_filings_dedupes_and_filters():
    submissions = _submissions([
        ("10-K", "2024-12-31", "2025-03-01", "a-late", "late.htm"),
        ("10-K/A", "2024-12-31", "2025-02-15", "a-amend", "amend.htm"),
        ("10-K", "2024-12-31", "2025-02-01", "a-first", "first.htm"),
        ("10-Q", "2024-09-30", "2024-11-01", "q", "q.htm"),
        ("10-K", "2025-12-31", "2026-02-01", "a-future", "future.htm"),
        ("10-K", "2023-12-31", "2024-02-01", "a-prior", "prior.htm"),
    ])
    selected = select_annual_filings(submissions, as_of=date(2025, 12, 31))
    assert [f.accession for f in selected] == ["a-prior", "a-first"]
    assert all(f.form == "10-K" for f in selected)


def test_select_annual_filings_empty_report_date_raises():
    submissions = _submissions([("10-K", "", "2025-02-01", "a1", "x.htm")])
    with pytest.raises(EvidenceIntegrityError, match="no reportDate"):
        select_annual_filings(submissions, as_of=LATEST)


@pytest.mark.parametrize(
    ("earlier", "later", "expected"),
    [
        (date(2023, 1, 28), date(2024, 2, 3), True),    # 53-week year, 371 days
        (date(2024, 2, 3), date(2025, 2, 1), True),     # 52-week year, 364 days
        (date(2023, 12, 31), date(2024, 12, 31), True),  # leap year, 366 days
        (date(2023, 12, 31), date(2024, 6, 30), False),  # fiscal-year change
    ],
)
def test_period_length_ok(earlier, later, expected):
    assert period_length_ok(earlier, later) is expected


# ---------- load_filings: full data ----------

@pytest.mark.parametrize(
    ("ticker", "expected_fiscal_years"),
    [("AMZN", [2021, 2022, 2023, 2024, 2025]), ("MSFT", [2022, 2023, 2024, 2025, 2026])],
)
def test_loads_five_consecutive_fiscal_years(ticker, expected_fiscal_years):
    records = EvidenceStore(EVIDENCE_DIR).load_filings(ticker, LATEST)
    assert [r.fiscal_year for r in records] == expected_fiscal_years
    assert all(r.form == "10-K" for r in records)
    assert all(r.filed <= LATEST for r in records)


@pytest.mark.parametrize(
    ("ticker", "first_start"),
    [("AMZN", date(2021, 1, 1)), ("MSFT", date(2021, 7, 1))],
)
def test_start_dates_follow_preceding_period_end(ticker, first_start):
    records = EvidenceStore(EVIDENCE_DIR).load_filings(ticker, LATEST)
    assert records[0].start == first_start
    assert all(
        later.start == earlier.end + timedelta(days=1)
        for earlier, later in zip(records, records[1:])
    )
    assert all(r.start_classification == "CALCULATION" for r in records)


def test_microsoft_fiscal_year_matches_filing_dei_label():
    records = EvidenceStore(EVIDENCE_DIR).load_filings("MSFT", LATEST)
    assert [(r.fiscal_year, r.end) for r in records] == [
        (2022, date(2022, 6, 30)),
        (2023, date(2023, 6, 30)),
        (2024, date(2024, 6, 30)),
        (2025, date(2025, 6, 30)),
        (2026, date(2026, 6, 30)),
    ]


def test_fiscal_year_is_dei_label_not_period_end_calendar_year(tmp_path):
    store = EvidenceStore(_build_synthetic_issuer(tmp_path))
    records = store.load_filings(SYNTH_TICKER, LATEST)

    assert [r.fiscal_year for r in records] == [fy for _, fy in SYNTH_PERIODS[1:]]
    assert [r.end.year for r in records] != [r.fiscal_year for r in records]
    assert records[0].start == SYNTH_PERIODS[0][0] + timedelta(days=1)


# ---------- load_filings: as_of and walk-back ----------

@pytest.mark.parametrize(
    ("ticker", "as_of", "latest_excluded_end", "expected_latest_end"),
    [
        ("AMZN", date(2026, 2, 5), date(2025, 12, 31), date(2024, 12, 31)),
        ("MSFT", date(2026, 7, 28), date(2026, 6, 30), date(2025, 6, 30)),
    ],
)
def test_as_of_excludes_latest_filing(ticker, as_of, latest_excluded_end, expected_latest_end):
    store = EvidenceStore(EVIDENCE_DIR)
    submissions = read_snapshot_json(EVIDENCE_DIR, f"submissions_{store.resolve_cik(ticker)}.json")
    selected = select_annual_filings(submissions, as_of)
    assert selected[-1].period_end == expected_latest_end
    assert latest_excluded_end not in {f.period_end for f in selected}


@pytest.mark.parametrize(
    ("ticker", "as_of", "expected_fiscal_years", "first_start"),
    [
        ("AMZN", date(2025, 12, 31), [2021, 2022, 2023, 2024], date(2021, 1, 1)),
        ("MSFT", date(2026, 7, 28), [2022, 2023, 2024, 2025], date(2021, 7, 1)),
    ],
)
def test_walk_back_stops_at_unpinned_and_uses_it_for_start(
    ticker, as_of, expected_fiscal_years, first_start
):
    records = EvidenceStore(EVIDENCE_DIR).load_filings(ticker, as_of)
    assert [r.fiscal_year for r in records] == expected_fiscal_years
    assert records[0].start == first_start
    assert records[0].start_classification == "CALCULATION"
    assert all(r.filed <= as_of for r in records)


def test_latest_not_pinned_raises(tmp_path):
    evidence_dir = _copy_evidence(tmp_path)
    _rewrite_manifest(evidence_dir, lambda m: m.pop("amzn-20251231.htm"))
    with pytest.raises(EvidenceNotPinnedError, match="amzn-20251231.htm"):
        EvidenceStore(evidence_dir).load_filings("AMZN", LATEST)


def test_middle_filing_not_pinned_stops_walk(tmp_path):
    evidence_dir = _copy_evidence(tmp_path)
    _rewrite_manifest(evidence_dir, lambda m: m.pop("amzn-20231231.htm"))
    records = EvidenceStore(evidence_dir).load_filings("AMZN", LATEST)
    assert [r.fiscal_year for r in records] == [2024, 2025]
    assert records[0].start == date(2024, 1, 1)


def test_corrupted_pinned_filing_mid_walk_raises(tmp_path):
    evidence_dir = _copy_evidence(tmp_path)
    snapshot = evidence_dir / "amzn-20231231.htm.gz"
    payload = bytearray(gzip.decompress(snapshot.read_bytes()))
    payload[len(payload) // 2] ^= 0x01
    snapshot.write_bytes(gzip.compress(bytes(payload)))

    with pytest.raises(EvidenceIntegrityError, match="SHA-256 mismatch"):
        EvidenceStore(evidence_dir).load_filings("AMZN", LATEST)


def test_no_preceding_10k_gives_start_gap(tmp_path):
    evidence_dir = _build_synthetic_issuer(tmp_path, SYNTH_PERIODS[1:], unpinned=frozenset())
    records = EvidenceStore(evidence_dir).load_filings(SYNTH_TICKER, LATEST)
    assert [r.fiscal_year for r in records] == [2020, 2021, 2022, 2023, 2024]
    assert records[0].start is None
    assert records[0].start_classification == "EVIDENCE_GAP"
    assert all(r.start_classification == "CALCULATION" for r in records[1:])


def test_fiscal_year_change_stops_walk_with_start_gap(tmp_path):
    periods = [
        (date(2021, 6, 30), 2021),
        (date(2021, 12, 31), 2021),  # six-month gap: fiscal-year change
        (date(2022, 12, 31), 2022),
        (date(2023, 12, 31), 2023),
    ]
    evidence_dir = _build_synthetic_issuer(tmp_path, periods, unpinned=frozenset())
    records = EvidenceStore(evidence_dir).load_filings(SYNTH_TICKER, LATEST)
    assert [r.end for r in records] == [date(2021, 12, 31), date(2022, 12, 31), date(2023, 12, 31)]
    assert records[0].start is None
    assert records[0].start_classification == "EVIDENCE_GAP"


def test_dei_label_jump_between_consecutive_filings_raises(tmp_path):
    periods = [(date(2022, 12, 31), 2022), (date(2023, 12, 31), 2025)]
    evidence_dir = _build_synthetic_issuer(tmp_path, periods, unpinned=frozenset())
    with pytest.raises(EvidenceIntegrityError, match=r"FY2022.*FY2025") as info:
        EvidenceStore(evidence_dir).load_filings(SYNTH_TICKER, LATEST)
    assert "retl-20221231.htm" in str(info.value) and "retl-20231231.htm" in str(info.value)
    assert not isinstance(info.value, EvidenceNotPinnedError)


def test_no_visible_filings_raises():
    with pytest.raises(InsufficientFilingsError, match="no 10-K filings visible"):
        EvidenceStore(EVIDENCE_DIR).load_filings("AMZN", date(2021, 1, 1))


def test_filing_record_has_blueprint_fields():
    assert [f.name for f in fields(FilingRecord)] == [
        "issuer", "cik", "form", "fiscal_year", "start", "start_classification", "end",
        "filed", "accession", "primary_document", "filing_url",
    ]
