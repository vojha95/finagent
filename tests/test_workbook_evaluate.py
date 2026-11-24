"""The independent formula evaluator (finagent.workbook.evaluate; Phase 4d)."""

import pytest
from openpyxl import Workbook

from finagent.workbook.evaluate import Evaluator, FormulaError, tokenize


def _wb(cells: dict[str, object], sheet: str = "S") -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    for coord, value in cells.items():
        ws[coord] = value
    return wb


def test_arithmetic_functions_and_cross_sheet_references():
    wb = _wb({"A1": 2.0, "A2": 3.0, "A3": "=A1*(1+A2)-ABS(-1)", "A4": "=SUM(A1:A2)/2",
              "A5": '=IF(A1>1,"big","small")', "A6": "=MAX(0,-A3)+MIN(A1,A2)"})
    other = wb.create_sheet("Two Words")
    other["B2"] = "='S'!A3+S!A1"
    values = Evaluator(wb).evaluate_all()
    assert values[("S", "A3")] == 7.0 and values[("S", "A4")] == 2.5
    assert values[("S", "A5")] == "big" and values[("S", "A6")] == 2.0
    assert values[("Two Words", "B2")] == 9.0


def test_blank_or_text_in_arithmetic_raises():
    with pytest.raises(FormulaError, match="blank"):
        Evaluator(_wb({"A1": "=A2+1"})).evaluate_all()
    with pytest.raises(FormulaError, match="text"):
        Evaluator(_wb({"A1": "x", "A2": "=A1*2"})).evaluate_all()


def test_if_is_lazy_and_isnumber_and_and():
    wb = _wb({"A1": "n.m.", "A2": 1.0,
              "A3": '=IF(AND(ISNUMBER(A1),ISNUMBER(A2)),A1+A2,"n.m.")',
              "A4": "=IF(A2>0,A2,A9*2)"})
    values = Evaluator(wb).evaluate_all()
    assert values[("S", "A3")] == "n.m." and values[("S", "A4")] == 1.0


def test_index_match_and_overrides():
    wb = _wb({"B1": "Base", "C1": "Severe", "B2": 0.0, "C2": -0.12, "A1": "Base",
              "D2": "=INDEX($B$2:$C$2,MATCH($A$1,$B$1:$C$1,0))",
              "E2": '=COUNTIF(B1:C1,"severe")'})
    assert Evaluator(wb).evaluate_all()[("S", "D2")] == 0.0
    severe = Evaluator(wb, {("S", "A1"): "Severe"}).evaluate_all()
    assert severe[("S", "D2")] == -0.12 and severe[("S", "E2")] == 1.0


def test_unsupported_function_and_cycles_raise():
    with pytest.raises(FormulaError, match="unsupported"):
        Evaluator(_wb({"A1": "=VLOOKUP(1,B1:B2,1)"})).evaluate_all()
    with pytest.raises(FormulaError, match="circular"):
        Evaluator(_wb({"A1": "=A2", "A2": "=A1"})).evaluate_all()


def test_text_starting_with_equals_is_not_a_formula():
    wb = _wb({"A1": "= note"})
    wb.active["A1"].data_type = "s"
    assert Evaluator(wb).evaluate_all() == {}


def test_tokenize_reads_numbers_refs_and_strings():
    kinds = [t.kind for t in tokenize("'Balance Sheet'!$C$5*2.5+\"x\"")]
    assert kinds == ["ref", "op", "number", "op", "string"]
