from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from finagent.config import load_universe
from finagent.document.coverage import Coverage
from finagent.evaluation.phase1 import (
    FilingReport,
    HeldOutError,
    company_summary,
    filing_report,
    leak_check,
    main,
    phase1_report,
    safe_filing_report,
    report_frame,
    select_tickers,
    tagged_statement_cells,
)
from finagent.evidence import EvidenceStore
from finagent.ixbrl import CellAddress, TagFact

EVIDENCE_DIR = Path(__file__).resolve().parents[1] / "evidence"
AS_OF = date(2026, 12, 31)
UNIVERSE = {"AAA": "train", "BBB": "validation", "CCC": "held_out"}


def _fact(address: tuple[int, int, int] | None, is_nil: bool = False) -> TagFact:
    return TagFact(
        accession="a", concept="us-gaap:Cash", context_id="c", start=None, end=date(2025, 12, 31),
        dimensions={}, unit="USD", scale=6, sign=1,
        amount=None if is_nil else Decimal("1"),
        cell_address=CellAddress(*address, row_label="Cash") if address else None,
        is_nil=is_nil,
    )


def _report(coverage: dict, leaks: tuple[str, ...] = (), ticker: str = "AAA") -> FilingReport:
    return FilingReport(
        ticker=ticker, split="train", fiscal_year=2025, accession="a", document="d.htm",
        coverage=coverage, leaks=leaks,
    )


FULL = {s: Coverage(covered=20, total=20, missed=()) for s in ("IS", "BS", "CF")}


# ---------- synthetic ----------

def test_tagged_statement_cells_drops_nil_label_and_untabled_facts():
    facts = [_fact((0, 1, 2)), _fact((0, 1, 0)), _fact((0, 2, 2), is_nil=True), _fact(None)]
    assert tagged_statement_cells(facts, label_cells={(0, 1, 0)}) == [(0, 1, 2)]


DOC = """<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
 xmlns:us-gaap="http://fasb.org/us-gaap/2025"><body><p>{text}</p><table><tr><td>Net income</td>
<td><ix:nonFraction name="us-gaap:NetIncomeLoss" contextRef="d" unitRef="usd" scale="6"
 id="f1">1,234</ix:nonFraction></td></tr></table></body></html>"""


@pytest.mark.parametrize(("text", "leaked"), [("Net income rose.", False), ("See NetIncomeLoss.", True)])
def test_leak_check(text, leaked):
    from finagent.ixbrl import clean_document

    document = DOC.format(text=text).encode()
    leaks = leak_check(document, clean_document(document), [_fact((0, 0, 1))])
    assert bool(leaks) is leaked


def test_report_passes_only_when_every_statement_located_covered_and_no_leaks():
    assert _report(FULL).passed
    assert not _report({**FULL, "CF": None}).passed
    assert not _report({**FULL, "CF": Coverage(covered=94, total=100, missed=())}).passed
    assert not _report({**FULL, "IS": Coverage(covered=0, total=0, missed=())}).passed
    assert not _report(FULL, leaks=("NetIncomeLoss",)).passed
    # Per statement, not pooled: a large BS cannot hide a weak CF.
    weak_cf = {**FULL, "BS": Coverage(1000, 1000, ()), "CF": Coverage(9, 10, ())}
    assert _report(weak_cf).share > 0.95 and not _report(weak_cf).passed


def test_missing_statement_is_a_gap_not_zero():
    report = _report({**FULL, "BS": None})
    assert report.located == ("IS", "CF")
    row = report_frame([report]).iloc[0]
    assert not row["BS_located"] and pd.isna(row["BS_share"]) and pd.isna(row["BS_total"])
    assert (row["covered"], row["total"]) == (40, 40)


def test_report_frame_and_company_summary():
    reports = [
        _report(FULL),
        _report({**FULL, "CF": Coverage(90, 100, ((1, 2, 3),))}),
        _report(FULL, ticker="BBB"),
    ]
    frame = report_frame(reports)
    assert list(frame["passed"]) == [True, False, True]
    assert frame.loc[1, "CF_share"] == pytest.approx(0.9)
    summary = company_summary(frame)
    assert summary.loc["AAA", "passed"] == 1 and summary.loc["AAA", "filings"] == 2
    assert summary.loc["AAA", "min_share"] == pytest.approx(0.9)
    assert summary.loc["BBB", "passed"] == 1


def test_select_tickers_excludes_held_out_by_default():
    assert select_tickers(UNIVERSE, None, include_held_out=False) == {"AAA": "train", "BBB": "validation"}
    assert "CCC" in select_tickers(UNIVERSE, None, include_held_out=True)
    assert select_tickers(UNIVERSE, ["aaa"], include_held_out=False) == {"AAA": "train"}


def test_select_tickers_refuses_held_out_without_flag():
    with pytest.raises(HeldOutError):
        select_tickers(UNIVERSE, ["AAA", "CCC"], include_held_out=False)
    assert select_tickers(UNIVERSE, ["CCC"], include_held_out=True) == {"CCC": "held_out"}
    with pytest.raises(KeyError):
        select_tickers(UNIVERSE, ["ZZZ"], include_held_out=False)


def test_main_refuses_a_held_out_ticker(tmp_path):
    held_out = next(t for t, s in load_universe().items() if s == "held_out")
    with pytest.raises(HeldOutError):
        main(["--tickers", held_out, "--out", str(tmp_path / "r.csv")])
    assert not (tmp_path / "r.csv").exists()


def test_safe_filing_report_records_error_as_failure():
    record = _STORE.load_filings("MSFT", AS_OF)[0]

    class BrokenStore:
        evidence_dir, manifest = EVIDENCE_DIR, {}  # empty manifest: snapshot read fails

    report = safe_filing_report(BrokenStore(), record, "MSFT", "validation")
    assert report.error and not report.passed and report.located == ()
    row = report_frame([report]).iloc[0]
    assert row["error"] == report.error and not row["passed"]
    assert company_summary(report_frame([report])).loc["MSFT", "errors"] == 1


def test_phase1_report_skips_unknown_ticker():
    reports, skipped = phase1_report(EvidenceStore(EVIDENCE_DIR), {"NOT-A-TICKER": "train"}, AS_OF)
    assert reports == [] and "NOT-A-TICKER" in skipped


def test_main_writes_csv(tmp_path, capsys):
    out = tmp_path / "phase1.csv"
    assert main(["--tickers", "MSFT", "--as-of", AS_OF.isoformat(), "--out", str(out)]) == 0
    frame = pd.read_csv(out)
    assert set(frame["ticker"]) == {"MSFT"} and len(frame) >= 1
    assert "filings pass" in capsys.readouterr().out


# ---------- Phase 1 exit criterion (blueprint 15): AMZN, MSFT (validation), AAPL (train) ----------

EXIT_COMPANIES = ("AMZN", "MSFT", "AAPL")
_STORE = EvidenceStore(EVIDENCE_DIR)
_SPLITS = load_universe()
EXIT_FILINGS = [(t, r) for t in EXIT_COMPANIES for r in _STORE.load_filings(t, AS_OF)]
# Known exit-criterion failures, tracked in the issues log; strict so a fix is noticed.
KNOWN_FAILURES = {
    "AAPL": pytest.mark.xfail(
        strict=True, reason="Phase 1 exit: AAPL cash flow statement not located; issues_log #30"
    ),
}
EXIT_PARAMS = [
    pytest.param(
        t, r, id=f"{t}-FY{r.fiscal_year}", marks=[KNOWN_FAILURES[t]] if t in KNOWN_FAILURES else []
    )
    for t, r in EXIT_FILINGS
]


@pytest.mark.parametrize(("ticker", "record"), EXIT_PARAMS)
def test_phase1_exit_criterion(ticker, record):
    report = filing_report(_STORE, record, ticker, _SPLITS[ticker])
    detail = {
        s: (c.covered, c.total, c.missed[:5]) if c is not None else None
        for s, c in report.coverage.items()
    }
    assert report.passed, (detail, report.leaks[:5])
