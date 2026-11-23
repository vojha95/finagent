"""Evidence store: point-in-time FilingRecords from pinned SEC snapshots (blueprint 8.1)."""

import html
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from finagent.evidence.integrity import (
    EvidenceIntegrityError,
    EvidenceNotPinnedError,
    InsufficientFilingsError,
    load_manifest,
    read_snapshot,
    read_snapshot_json,
)

# Blueprint 8.1: keep the last five fiscal years plus the preceding year,
# which is read from submissions only to obtain the first year's start date.
SELECTED_FISCAL_YEARS = 5
PRECEDING_YEARS = 1

ANNUAL_FORM = "10-K"  # Blueprint 8.1: no amendments (10-K/A) or transition reports.

# Allowed days between consecutive fiscal period ends. A 52-week year is
# 364 days and a 53-week year is 371 days; calendar years are 365 or 366.
# Anything outside the bounds signals a fiscal-year change, which breaks
# the consecutive-years rule.
PERIOD_LENGTH_DAYS = (350, 380)

EDGAR_ARCHIVE_ROOT = "https://www.sec.gov/Archives/edgar/data"

_SUBMISSION_COLUMNS = ("form", "reportDate", "filingDate", "accessionNumber", "primaryDocument")


@dataclass(frozen=True)
class FilingRecord:
    issuer: str
    cik: str
    form: str
    fiscal_year: int
    start: date
    end: date
    filed: date
    accession: str
    primary_document: str
    filing_url: str


@dataclass(frozen=True)
class SubmissionFiling:
    """One annual filing as listed in the submissions JSON (not necessarily pinned)."""

    form: str
    period_end: date
    filed: date
    accession: str
    primary_document: str


def build_filing_url(cik: str, accession: str, primary_document: str) -> str:
    """EDGAR archive URL from CIK, accession (dashes removed) and primary document."""
    return f"{EDGAR_ARCHIVE_ROOT}/{int(cik)}/{accession.replace('-', '')}/{primary_document}"


def select_annual_filings(submissions: dict[str, Any], as_of: date) -> list[SubmissionFiling]:
    """10-Ks filed on or before as_of, one per period end (first filed), oldest first."""
    recent = submissions.get("filings", {}).get("recent", {})
    if not isinstance(recent, dict) or any(c not in recent for c in _SUBMISSION_COLUMNS):
        raise EvidenceIntegrityError("Submissions filing metadata is incomplete")

    by_period_end: dict[date, SubmissionFiling] = {}
    for index, form in enumerate(recent["form"]):
        if form != ANNUAL_FORM:
            continue
        filed = date.fromisoformat(recent["filingDate"][index])
        if filed > as_of:
            continue
        report_date = recent["reportDate"][index]
        if not report_date:
            raise EvidenceIntegrityError(
                f"10-K {recent['accessionNumber'][index]} has no reportDate"
            )
        filing = SubmissionFiling(
            form=form,
            period_end=date.fromisoformat(report_date),
            filed=filed,
            accession=recent["accessionNumber"][index],
            primary_document=recent["primaryDocument"][index],
        )
        existing = by_period_end.get(filing.period_end)
        if existing is None or filing.filed < existing.filed:
            by_period_end[filing.period_end] = filing
    return [by_period_end[end] for end in sorted(by_period_end)]


def check_period_lengths(period_ends: list[date]) -> None:
    """Raise if any two consecutive period ends are outside PERIOD_LENGTH_DAYS apart."""
    low, high = PERIOD_LENGTH_DAYS
    for earlier, later in zip(period_ends, period_ends[1:]):
        days = (later - earlier).days
        if not low <= days <= high:
            raise EvidenceIntegrityError(
                f"Period from {earlier} to {later} is {days} days, outside {low}-{high}"
            )


class EvidenceStore:
    def __init__(self, evidence_dir: Path | str) -> None:
        self.evidence_dir = Path(evidence_dir)
        self.manifest = load_manifest(self.evidence_dir)

    def resolve_cik(self, ticker: str) -> str:
        ticker_map = read_snapshot_json(self.evidence_dir, "company_tickers.json", self.manifest)
        normalized = ticker.strip().upper()
        for entry in ticker_map.values():
            if str(entry.get("ticker", "")).upper() == normalized:
                return f"{int(entry['cik_str']):010d}"
        raise KeyError(f"Unknown ticker: {ticker}")

    def load_filings(self, ticker: str, as_of: date) -> list[FilingRecord]:
        cik = self.resolve_cik(ticker)
        submissions = read_snapshot_json(
            self.evidence_dir, f"submissions_{cik}.json", self.manifest
        )
        visible = select_annual_filings(submissions, as_of)
        needed = SELECTED_FISCAL_YEARS + PRECEDING_YEARS
        if len(visible) < needed:
            older_pages = submissions.get("filings", {}).get("files", [])
            raise InsufficientFilingsError(
                f"{ticker}: {len(visible)} 10-K filings visible as of {as_of}, need {needed}"
                f" (unpinned older submissions pages: {len(older_pages)})"
            )
        window = visible[-needed:]
        check_period_lengths([filing.period_end for filing in window])

        records = [
            self._build_record(ticker, cik, filing, start=previous.period_end + timedelta(days=1))
            for previous, filing in zip(window, window[1:])
        ]
        fiscal_years = [record.fiscal_year for record in records]
        if any(b != a + 1 for a, b in zip(fiscal_years, fiscal_years[1:])):
            raise EvidenceIntegrityError(
                f"Non-consecutive fiscal years for {ticker}: {fiscal_years}"
            )
        return records

    def _build_record(
        self, ticker: str, cik: str, filing: SubmissionFiling, start: date
    ) -> FilingRecord:
        name = filing.primary_document
        if name not in self.manifest:
            raise EvidenceNotPinnedError(
                f"{ticker}: 10-K for period ending {filing.period_end} "
                f"({name}, accession {filing.accession}) is not pinned"
            )
        filing_url = build_filing_url(cik, filing.accession, name)
        if not filing_url.startswith(f"{EDGAR_ARCHIVE_ROOT}/{int(cik)}/"):
            raise EvidenceIntegrityError(f"Filing URL outside issuer archive: {filing_url}")
        if self.manifest[name].get("url") != filing_url:
            raise EvidenceIntegrityError(
                f"Manifest URL for {name} does not match {filing_url}"
            )

        document = read_snapshot(self.evidence_dir, name, self.manifest).decode(
            "utf-8", errors="replace"
        )
        return FilingRecord(
            issuer=ticker.strip().upper(),
            cik=cik,
            form=filing.form,
            fiscal_year=_dei_integer(document, "DocumentFiscalYearFocus"),
            start=start,
            end=filing.period_end,
            filed=filing.filed,
            accession=filing.accession,
            primary_document=name,
            filing_url=filing_url,
        )


def _dei_integer(document: str, name: str) -> int:
    pattern = (
        rf'<(?:ix:)?nonNumeric[^>]*\bname=["\']dei:{name}["\'][^>]*>'
        rf"(.*?)</(?:ix:)?nonNumeric>"
    )
    match = re.search(pattern, document, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        raise EvidenceIntegrityError(f"Missing DEI: {name}")
    text = re.sub(r"<[^>]+>", "", html.unescape(match.group(1)))
    text = re.sub(r"\s+", "", text)
    if not re.fullmatch(r"\d{4}", text):
        raise EvidenceIntegrityError(f"Invalid DEI {name}: {text}")
    return int(text)
