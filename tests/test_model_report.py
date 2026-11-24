"""Phase 4b report: BC16 exit check, outputs and markdown."""

import csv
import json

from model_support import AS_OF, make_set

from finagent.model.forecast import build_forecast
from finagent.model.report import (
    NOTES_MARKER,
    CompanyRun,
    exit_check,
    render_report,
    write_outputs,
    load_previous,
    write_report,
)

HEADER = {"run_id": "f-1", "git_commit": "abc", "git_dirty": False,
          "evidence_manifest_sha256": "f" * 64, "model_version": "m", "as_of": "2026-11-24",
          "switches": {"working_capital_basis": "latest", "refinance_maturities": False}}


def _runs():
    good = CompanyRun("SYNT", "validation", build_forecast(make_set(), AS_OF))
    blocked = CompanyRun("DISP", "validation",
                         build_forecast(make_set(gaps={(2025, "ppe_net"): "DISAGREE"}), AS_OF),
                         ["ppe_net FY2025"])
    gap = CompanyRun("GAPS", "validation", build_forecast(
        make_set(gaps={(2025, "capital_expenditure"): "NO_IDENTITY_CHECK"}), AS_OF))
    train = CompanyRun("TRN", "train", None, error="ValueError: boom")
    return [good, blocked, gap, train]


def test_exit_check_counts_companies_without_disagree():
    result = exit_check(_runs())
    assert result == {"counted": ["SYNT", "GAPS"], "passed": ["SYNT"],
                      "excluded_disagree": ["DISP"]}


def test_outputs_and_report(tmp_path):
    runs = _runs()
    write_outputs(tmp_path, runs, HEADER)
    document = json.loads((tmp_path / "forecast.json").read_text())
    assert document["exit_check"]["passed"] == ["SYNT"]
    drivers = list(csv.DictReader((tmp_path / "drivers.csv").open()))
    assert {r["driver"] for r in drivers if r["ticker"] == "SYNT"} >= {"revenue_growth",
                                                                         "tax_rate"}
    projections = list(csv.DictReader((tmp_path / "projections.csv").open()))
    assert {r["ticker"] for r in projections} == {"SYNT"}  # GAP shows no numbers
    text = render_report(runs, HEADER)
    assert "VERIFIED: 1 (SYNT)" in text and "Excluded as documented DISAGREE GAPs: DISP" in text
    assert "| TRN | ERROR |" in text and "Income statement ($ millions):" in text
    path = tmp_path / "r.md"
    write_report(path, text)
    path.write_text(path.read_text() + "\nnote\n")
    write_report(path, text)
    assert path.read_text().count(NOTES_MARKER) == 1 and "note" in path.read_text()


def test_validation_table_and_before_after(tmp_path):
    runs = _runs()
    text = render_report(runs, HEADER)
    row = next(line for line in text.splitlines() if line.startswith("| SYNT | VERIFIED"))
    assert "% (EFFECTIVE)" in row and "→" in row  # interest rate and liquidity path
    write_outputs(tmp_path, runs, HEADER)
    previous = load_previous(tmp_path / "forecast.json")
    previous["GAPS"] = ("validation", "VERIFIED")
    text = render_report(runs, dict(HEADER, compare_run="f-0"), previous)
    assert "## 4. Before vs after (f-0 -> this run)" in text
    assert "- validation: VERIFIED 2 -> 1 (of 3)" in text
    assert "| GAPS | validation | VERIFIED | GAP |" in text
