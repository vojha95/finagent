"""Phase 4a report: CSV outputs, markdown report and the kept notes section."""

import csv
import json
from dataclasses import replace
from datetime import date

from statements_support import FILING, obs, tag_only

from finagent.config import ForecastNeed
from finagent.records import IdentityResult
from finagent.statements.build import StatementSet
from finagent.statements.coverage import slots
from finagent.statements.report import (
    NOTES_MARKER,
    RunResult,
    compare_runs,
    coverage_rows,
    ebit_bases,
    identity_counts,
    render_report,
    set_record,
    write_coverage_csv,
    write_identities_csv,
    write_report,
)

NEEDS = [ForecastNeed("revenue", "required", 1, ""), ForecastNeed("depreciation", "required", 1,
                                                                  "")]
HEADER = {"run_id": "t-1", "git_commit": "abc", "git_dirty": False,
          "evidence_manifest_sha256": "f" * 64, "model_version": "m", "as_of": "2026-11-24"}


def _set():
    depreciation = replace(tag_only("depreciation", 7), basis="D&A",
                           flags=("TAG_ONLY", "SUBSTITUTE:depreciation_and_amortisation"))
    observations = {(2025, "revenue"): obs("revenue", 10), (2025, "depreciation"): depreciation}
    identities = [IdentityResult("SYNT", 2025, "I9", 0, 0, "PASS", FILING.accession),
                  IdentityResult("SYNT", 2025, "I11", 5, 0, "REVIEW", FILING.accession)]
    return StatementSet("SYNT", FILING.cik, date(2026, 11, 24), (FILING,), observations,
                        identities, {2025: {"pretax_basis": None, "ebit_basis": "ebit_proxy"}},
                        "m")


def _runs():
    return [RunResult("validation", {"SYNT": _set()}), RunResult("train", {}, {"BAD": "Boom"})]


def test_identity_counts_and_coverage_rows():
    counts = identity_counts([_set()])
    assert counts["I9"]["PASS"] == 1 and counts["I11"]["REVIEW"] == 1 and not counts["I1"]
    found = {"SYNT": ("validation", slots(_set(), NEEDS))}
    assert coverage_rows(found, "required")[0][:4] == ["SYNT", 2, 2, "1.000"]


def test_csv_outputs(tmp_path):
    write_identities_csv(tmp_path / "i.csv", _runs())
    rows = list(csv.DictReader((tmp_path / "i.csv").open()))
    assert {r["identity_id"] for r in rows} == {"I9", "I11"}
    write_coverage_csv(tmp_path / "c.csv", {"SYNT": ("validation", slots(_set(), NEEDS))})
    assert len(list(csv.DictReader((tmp_path / "c.csv").open()))) == 2


def test_set_record_is_json_ready():
    record = set_record(_set(), "validation")
    json.dumps(record)
    assert record["observations"][0]["metric"] in ("depreciation", "revenue")


def test_render_and_keep_notes(tmp_path):
    found = {"SYNT": ("validation", slots(_set(), NEEDS))}
    text = render_report(_runs(), HEADER, NEEDS, found)
    assert "| validation | 1/1 | — |" in text and "| train | 0/1 | BAD |" in text
    assert "SYNT (1 years)" in text  # depreciation basis D&A listed by company
    assert "SYNT FY2025 ebit_proxy" in text  # EBIT proxy filers listed (#68)
    assert "| SYNT | 2025 | I11 | REVIEW |" in text  # REVIEW shown with its residual
    path = tmp_path / "report.md"
    write_report(path, text)
    path.write_text(path.read_text() + "\nhand-written note\n")
    write_report(path, text)
    assert path.read_text().count(NOTES_MARKER) == 1 and "hand-written note" in path.read_text()


def test_ebit_bases_lists_years_without_operating_income():
    assert ebit_bases([_set()]) == {"SYNT": [(2025, "ebit_proxy")]}
    presented = replace(_set(), bases={2025: {"ebit_basis": "operating_income"}})
    assert ebit_bases([presented]) == {}


def test_compare_runs_before_and_after(tmp_path):
    before = tmp_path / "before"
    before.mkdir()
    write_coverage_csv(before / "coverage.csv", {"SYNT": ("validation", slots(_set(), NEEDS))})
    old = replace(_set(), identities=[IdentityResult("SYNT", 2025, "I11", 5, 0, "FAIL", "a")])
    write_identities_csv(before / "identities.csv", [RunResult("validation", {"SYNT": old})])
    write_identities_csv(tmp_path / "i.csv", _runs())
    after_cov = list(csv.DictReader((before / "coverage.csv").open()))
    lines = compare_runs(before, after_cov, list(csv.DictReader((tmp_path / "i.csv").open())))
    text = "\n".join(lines)
    assert "| SYNT | 2/2 | 2/2 | 0 | 0 |" in text
    assert "| I11 | 0/1/0/0 | 0/0/1/0 |" in text and "| I9 | 0/0/0/0 | 1/0/0/0 |" in text
