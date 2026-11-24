"""The Excel workbook (finagent.workbook; blueprint 8.9; Phase 4d) on the synthetic company."""

import io

import pytest
from model_support import AS_OF, make_set, value_obs
from openpyxl import load_workbook
from statements_support import millions

from finagent.config import load_forecast_config, load_scenarios, load_workbook_layout
from finagent.model.forecast import build_forecast
from finagent.statements.verify_set import NOT_REPORTED
from finagent.workbook.build import SHEETS, build_workbook, compare, evaluate_scenarios, manifest
from finagent.workbook.evaluate import Evaluator, tokenize
from finagent.workbook.grid import GAP_FILL
from finagent.workbook.model import BS, CF, FORECAST, IS
from finagent.workbook.ratios_sheet import RATIOS_SHEET

CONFIG = load_forecast_config()
SPECS = load_scenarios()


def build(statement_set):
    forecasts = {spec.name: build_forecast(statement_set, AS_OF, CONFIG, spec) for spec in SPECS}
    return build_workbook(statement_set, forecasts, SPECS, CONFIG, AS_OF,
                          {"run_id": "test", "as_of": AS_OF.isoformat()})


def face_block_set():
    """Total liabilities FY2025 as a face-block sum of two rows (a CALCULATION)."""
    tl = value_obs(2025, "total_liabilities", 600, classification="CALCULATION",
                   components=(("r1", "Total current liabilities", millions(400)),
                               ("r2", "Long-term debt", millions(200))))
    return make_set({(2025, "total_liabilities"): tl})


@pytest.fixture(scope="module")
def built():
    return build(face_block_set())


def reload(b):
    buffer = io.BytesIO()
    b.workbook.save(buffer)
    buffer.seek(0)
    return load_workbook(buffer)


def projected_cells(b):
    """Every non-empty cell in a projected column below the header, on the statement, Ratios
    and Forecast schedule sheets."""
    wb, cols = b.workbook, b.grid.cols
    found = []
    for name in (IS, BS, CF, RATIOS_SHEET, FORECAST):
        ws = wb[name]
        start = 5
        if name == FORECAST:
            rows = [c.row for c in ws["A"] if c.value == "Schedules"]
            if not rows:
                continue  # no schedules (forecast GAP)
            start = rows[0] + 1
        for y in cols.proj:
            for r in range(start, ws.max_row + 1):
                cell = ws.cell(row=r, column=cols.col(y))
                if cell.value is not None:
                    found.append(cell)
    return found


def test_sheets_in_blueprint_order(built):
    assert tuple(built.workbook.sheetnames) == SHEETS
    assert built.status == "VERIFIED" and built.selector is not None


def test_every_projected_cell_is_a_formula(built):
    cells = projected_cells(built)
    assert len(cells) > 100
    assert all(c.data_type == "f" for c in cells), [c.coordinate for c in cells
                                                     if c.data_type != "f"][:5]


def test_no_hard_coded_numbers_in_projections(built):
    literals = {float(t.text) for c in projected_cells(built) for t in tokenize(c.value[1:])
                if t.kind == "number"}
    assert literals <= {0.0, 1.0, 2.0}  # 1 + growth, average (a + b) / 2, max(x, 0)


def test_formulas_match_the_model_in_every_scenario(built):
    results = evaluate_scenarios(built, reload(built))
    assert set(results) == {"Base", "Moderate", "Severe"}
    assert all(problems == [] for problems in results.values()), results
    assert len(built.scenarios) == 3


def test_selector_drives_the_projection_and_the_balance_sheet_still_balances(built):
    wb, g = reload(built), built.grid
    sheet, coord = g.refs[("m:revenue", 2026)]
    f1_sheet, f1 = g.refs[("chk:F1", 2028)]
    values = {n: Evaluator(wb, {built.selector: n}).evaluate_all() for n in ("Base", "Severe")}
    assert values["Severe"][(sheet, coord)] < values["Base"][(sheet, coord)]
    for v in values.values():
        assert abs(v[(f1_sheet, f1)]) <= 0.01
        all_checks = g.refs[("d:all_checks", None)]
        assert v[all_checks] == "OK"


def test_reported_facts_blue_and_calculations_are_formulas(built):
    ws, g = built.workbook[IS], built.grid
    revenue = ws[g.refs[("r:revenue", 2025)][1]]
    assert revenue.font.color.rgb.endswith("0000FF") and revenue.data_type == "n"
    tl = built.workbook[BS][g.refs[("r:total_liabilities", 2025)][1]]
    assert tl.data_type == "f" and "+" in tl.value  # face-block sum of its component rows


def test_gap_cells_are_empty_with_a_note_and_never_referenced():
    statement_set = make_set(gaps={(2024, "net_income"): "DISAGREE"})
    b = build(statement_set)
    gaps = [(s, c) for s, c, _ in b.grid.gaps]
    sheet, coord = next((s, c) for s, c, r in b.grid.gaps if s == IS and "FY2024" in r
                        and "DISAGREE" in r)
    cell = b.workbook[sheet][coord]
    assert cell.value is None and cell.fill.fgColor.rgb.endswith(GAP_FILL.fgColor.rgb[-6:])
    assert cell.comment.text.startswith("GAP:")
    referenced = {(s, c) for (s, c) in b.grid.expected for t in tokenize(
        b.workbook[s][c].value[1:]) if t.kind == "ref"
        and _target(t.text, s) in gaps}
    assert referenced == set()
    assert all(p == [] for p in evaluate_scenarios(b).values())  # strict: no blank used


def _target(text: str, sheet: str) -> tuple[str, str]:
    if "!" in text:
        name, cell = text.rsplit("!", 1)
        return name.strip("'"), cell.replace("$", "")
    return sheet, text.replace("$", "")


def test_no_cogs_filer_uses_one_cash_cost_share():
    statement_set = make_set({(2025, "operating_income"): 150},
                             {(2025, "cost_of_revenue"): NOT_REPORTED,
                              (2025, "operating_expense_after_cost_of_sales"): NOT_REPORTED})
    b = build(statement_set)
    assert b.status == "VERIFIED"
    assert b.grid.has("m:cash_operating_costs", 2026) and not b.grid.has("m:cogs", 2026)
    assert all(p == [] for p in evaluate_scenarios(b).values())


def test_gap_forecast_keeps_history_with_empty_projections_and_the_reason():
    statement_set = make_set(gaps={(2025, "income_tax_expense"): "DISAGREE"})
    b = build(statement_set)
    assert b.status == "GAP" and "income_tax_expense" in b.banner
    cells = projected_cells(b)
    assert all(c.value is None or c.data_type != "n" for c in cells)
    assert not any(k[0].startswith("m:") for k in b.grid.refs)
    ws = b.workbook[IS]
    assert any("Projection FY+1..FY+3" in str(c.value) for c in ws["A"])
    assert all(p == [] for p in evaluate_scenarios(b).values())


def test_manifest_lists_expected_values_and_gaps(built):
    m = manifest(built)
    assert m["selector"] == list(built.selector) and m["status"] == "VERIFIED"
    assert all({"expected", "tolerance"} <= set(c) for c in m["cells"].values())


def test_compare_reports_mismatches(built):
    key = next(iter(built.grid.expected))
    problems = compare({key: 1e30}, built.grid, "Base")
    assert any(key[1] in p for p in problems)


def test_workbook_layout_config_loads():
    layout = load_workbook_layout()
    assert set(layout.reported_rows) == {IS, BS, CF}
    assert "debt_principal_y1" in layout.schedule_inputs
