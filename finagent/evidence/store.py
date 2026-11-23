"""Evidence store: point-in-time FilingRecords from pinned SEC snapshots (blueprint 8.1)."""

import html
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

from finagent.evidence.integrity import (
    EvidenceIntegrityError,
    EvidenceNotPinnedError,
    InsufficientFilingsError,
    load_manifest,
    read_snapshot,
    read_snapshot_json,
)

# Blueprint 8.1: return up to five fiscal years, walking back from the latest
# visible 10-K while filings are pinned and consecutive.
MAX_FISCAL_YEARS = 5

# Fiscal years a face statement shows: the filing's own year and up to two before it.
TABLE_YEARS = 3

ANNUAL_FORM = "10-K"  # Blueprint 8.1: no amendments (10-K/A) or transition reports.

# Allowed days between consecutive fiscal period ends. A 52-week year is
# 364 days and a 53-week year is 371 days; calendar years are 365 or 366.
# Anything outside the bounds signals a fiscal-year change, which breaks
# the consecutive-years rule.
PERIOD_LENGTH_DAYS = (350, 380)

# Blueprint 6.2: a start derived as preceding period end + 1 day is a
# CALCULATION; a start that cannot be derived is an EVIDENCE_GAP.
StartClassification = Literal["CALCULATION", "EVIDENCE_GAP"]

EDGAR_ARCHIVE_ROOT = "https://www.sec.gov/Archives/edgar/data"

_SUBMISSION_COLUMNS = ("form", "reportDate", "filingDate", "accessionNumber", "primaryDocument")


@dataclass(frozen=True)
class FilingRecord:
    issuer: str
    cik: str
    form: str
    fiscal_year: int
    start: date | None
    start_classification: StartClassification
    end: date
    filed: date
    accession: str
    primary_document: str
    filing_url: str
    # The cover-page (DEI) fiscal year when it disagrees with the consecutive sequence and
    # fiscal_year was taken from the sequence instead; None when the DEI label agrees.
    dei_fiscal_year_mismatch: int | None = None


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


def merged_submissions(submissions: dict[str, Any], pages: Iterable[dict[str, Any]]) -> dict:
    """The submissions JSON with older pages' filing columns appended to filings.recent."""
    recent = {k: list(v) for k, v in submissions.get("filings", {}).get("recent", {}).items()}
    for page in pages:
        for column, values in page.items():
            if isinstance(values, list) and column in recent:
                recent[column].extend(values)
    return {"filings": {"recent": recent}}


def period_length_ok(earlier: date, later: date) -> bool:
    """True if two consecutive period ends are within PERIOD_LENGTH_DAYS of each other."""
    low, high = PERIOD_LENGTH_DAYS
    return low <= (later - earlier).days <= high


def sequence_fiscal_years(ticker: str, documents: list[str], labels: list[int]) -> list[int]:
    """Fiscal years for a consecutive chain (oldest first) from its DEI labels.

    Consecutive period ends fix the order, so the years are anchor, anchor + 1, ...; the anchor
    is the one implied by more than half of the DEI labels. A filer's mislabelled cover page is
    thereby corrected and flagged by the caller; with no majority the labels cannot be trusted.
    """
    anchors = Counter(label - offset for offset, label in enumerate(labels))
    anchor, votes = anchors.most_common(1)[0]
    if votes * 2 <= len(labels):
        pairs = ", ".join(f"{d} (FY{y})" for d, y in zip(documents, labels))
        raise EvidenceIntegrityError(
            f"{ticker}: consecutive filings have non-consecutive fiscal year labels "
            f"and no majority to correct them: {pairs}"
        )
    return [anchor + offset for offset in range(len(labels))]


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
        """Up to MAX_FISCAL_YEARS pinned, consecutive 10-Ks visible as of the date, oldest first."""
        cik = self.resolve_cik(ticker)
        submissions = read_snapshot_json(
            self.evidence_dir, f"submissions_{cik}.json", self.manifest
        )
        visible = select_annual_filings(self._with_pinned_older_pages(submissions), as_of)
        if not visible:
            older_pages = submissions.get("filings", {}).get("files", [])
            raise InsufficientFilingsError(
                f"{ticker}: no 10-K filings visible as of {as_of}"
                f" (unpinned older submissions pages: {len(older_pages)})"
            )
        if visible[-1].primary_document not in self.manifest:
            latest = visible[-1]
            raise EvidenceNotPinnedError(
                f"{ticker}: latest 10-K as of {as_of} (period ending {latest.period_end}, "
                f"{latest.primary_document}, accession {latest.accession}) is not pinned"
            )

        selected, preceding = self._walk_back(ticker, cik, visible)
        records = []
        for filing, fiscal_year, dei_mismatch in selected:
            if records:
                start: date | None = records[-1].end + timedelta(days=1)
            elif preceding is not None:
                start = preceding.period_end + timedelta(days=1)
            else:
                start = None
            records.append(self._record(ticker, cik, filing, fiscal_year, start, dei_mismatch))
        return records

    def fiscal_periods(self, record: FilingRecord, years: int = TABLE_YEARS) -> dict[int, date]:
        """{fiscal year: period end} for the filing and the fiscal years before it (8.5).

        Read from the submissions listing as of the filing's own filing date (point in time):
        the filing's period end, then each earlier 10-K while consecutive, up to `years` in
        all. Fiscal years count back from the record's (BC2-corrected) fiscal year. A year
        that is not listed, or follows a fiscal-year change, is absent: a gap, never a guess.
        """
        submissions = read_snapshot_json(
            self.evidence_dir, f"submissions_{record.cik}.json", self.manifest
        )
        visible = select_annual_filings(self._with_pinned_older_pages(submissions), record.filed)
        ends = [f.period_end for f in visible if f.period_end <= record.end]
        if not ends or ends[-1] != record.end:
            return {}
        periods = {record.fiscal_year: record.end}
        for offset in range(1, years):
            index = len(ends) - 1 - offset
            if index < 0 or not period_length_ok(ends[index], ends[index + 1]):
                break
            periods[record.fiscal_year - offset] = ends[index]
        return periods

    def _with_pinned_older_pages(self, submissions: dict[str, Any]) -> dict[str, Any]:
        """Append the pinned older submissions pages, where large filers' earlier 10-Ks live."""
        names = [page.get("name") for page in submissions.get("filings", {}).get("files", [])]
        pages = [
            read_snapshot_json(self.evidence_dir, name, self.manifest)
            for name in names
            if name in self.manifest
        ]
        return merged_submissions(submissions, pages) if pages else submissions

    def _walk_back(
        self, ticker: str, cik: str, visible: list[SubmissionFiling]
    ) -> tuple[list[tuple[SubmissionFiling, int, int | None]], SubmissionFiling | None]:
        """Collect pinned, consecutive filings from the latest back, with fiscal-year labels.

        Returns the selection (oldest first) as (filing, fiscal year, DEI label if it disagreed)
        and the preceding listed 10-K when it is consecutive with the oldest selected one (it
        need not be pinned).
        """
        index = len(visible) - 1
        chain = [visible[index]]
        preceding = None
        while index > 0:
            earlier = visible[index - 1]
            if not period_length_ok(earlier.period_end, chain[0].period_end):
                break
            if len(chain) == MAX_FISCAL_YEARS or earlier.primary_document not in self.manifest:
                preceding = earlier
                break
            chain.insert(0, earlier)
            index -= 1
        labels = [self._read_fiscal_year(ticker, cik, filing) for filing in chain]
        years = sequence_fiscal_years(ticker, [f.primary_document for f in chain], labels)
        selected = [
            (filing, year, label if label != year else None)
            for filing, year, label in zip(chain, years, labels)
        ]
        return selected, preceding

    def _read_fiscal_year(self, ticker: str, cik: str, filing: SubmissionFiling) -> int:
        """Verify URL and hash of a pinned 10-K and return its DEI fiscal year."""
        name = filing.primary_document
        if name not in self.manifest:
            raise EvidenceNotPinnedError(f"{ticker}: {name} is not pinned")
        filing_url = build_filing_url(cik, filing.accession, name)
        if not filing_url.startswith(f"{EDGAR_ARCHIVE_ROOT}/{int(cik)}/"):
            raise EvidenceIntegrityError(f"Filing URL outside issuer archive: {filing_url}")
        if self.manifest[name].get("url") != filing_url:
            raise EvidenceIntegrityError(f"Manifest URL for {name} does not match {filing_url}")
        document = read_snapshot(self.evidence_dir, name, self.manifest).decode(
            "utf-8", errors="replace"
        )
        return _dei_integer(document, "DocumentFiscalYearFocus")

    @staticmethod
    def _record(
        ticker: str,
        cik: str,
        filing: SubmissionFiling,
        fiscal_year: int,
        start: date | None,
        dei_mismatch: int | None = None,
    ) -> FilingRecord:
        return FilingRecord(
            issuer=ticker.strip().upper(),
            cik=cik,
            form=filing.form,
            fiscal_year=fiscal_year,
            start=start,
            start_classification="CALCULATION" if start is not None else "EVIDENCE_GAP",
            end=filing.period_end,
            filed=filing.filed,
            accession=filing.accession,
            primary_document=filing.primary_document,
            filing_url=build_filing_url(cik, filing.accession, filing.primary_document),
            dei_fiscal_year_mismatch=dei_mismatch,
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
