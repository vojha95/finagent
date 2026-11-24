"""LibreOffice recalculation check (finagent.workbook.recalc; Phase 4d)."""

import json

from openpyxl import Workbook

from finagent.workbook.recalc import PENDING, check_workbook, compare_cells, find_soffice


def _manifest():
    return {"selector": ["Forecast", "C4"], "scenarios": ["Base", "Severe"],
            "cells": {"S!A1": {"expected": {"*": 10.0}, "tolerance": "dollar"},
                      "S!A2": {"expected": {"Severe": "OK"}, "tolerance": "exact"},
                      "S!A3": {"expected": {"Base": 0.5}, "tolerance": "ratio"}}}


def test_compare_cells_uses_tolerances_and_flags_missing_values():
    wb = Workbook()
    ws = wb.active
    ws.title = "S"
    ws["A1"], ws["A3"] = 10.004, 0.5 + 1e-6
    problems = compare_cells(wb, _manifest(), "Base")
    assert len(problems) == 1 and "S!A3" in problems[0]  # ratio outside 1e-9; A2 not Base
    severe = compare_cells(wb, _manifest(), "Severe")
    assert any("not recalculated" in p for p in severe)


def test_without_libreoffice_the_check_is_pending(tmp_path):
    path = tmp_path / "X.xlsx"
    Workbook().save(path)
    path.with_suffix(".manifest.json").write_text(json.dumps(_manifest()))
    assert check_workbook(path, None)["status"] == PENDING


def test_find_soffice_honours_the_environment(monkeypatch):
    monkeypatch.setenv("FINAGENT_SOFFICE", "/x/soffice")
    assert find_soffice() == "/x/soffice"
