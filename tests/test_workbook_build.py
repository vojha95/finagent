"""The Excel workbook (finagent.workbook; blueprint 8.9; Phase 4d) on the synthetic company."""

import io
import re

import pytest
from model_support import AS_OF, make_set, value_obs
from openpyxl import load_workbook
from statements_support import millions

from finagent.config import ConfigError, load_forecast_config, load_scenarios
from finagent.model.forecast import build_forecast
from finagent.statements.verify_set import NOT_REPORTED
from finagent.workbook.build import SHEETS, build_workbook, compare, evaluate_scenarios, manifest
from finagent.workbook.evaluate import Evaluator, tokenize
from finagent.workbook.grid import ALL, GAP_FILL
from finagent.workbook.layout import STATEMENT_SHEETS, load_workbook_layout
from finagent.workbook.model import BS, CF, FORECAST, IS
from finagent.workbook.ratios_sheet import RATIOS_SHEET
from finagent.workbook.statements import FIRST_ROW

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
        start = FIRST_ROW if name in (IS, BS, CF) else 5
        if name == FORECAST:
            rows = [c.row for c in ws["A"] if str(c.value).startswith("Schedules (")]
            if not rows:
                continue  # no schedules (forecast GAP)
            start = rows[0] + 1
        for y in cols.proj:
            for r in range(start, ws.max_row + 1):
                cell = ws.cell(row=r, column=cols.col(y))
                if cell.value is not None and not re.fullmatch(r"FY\d{4}E", str(cell.value)):
                    found.append(cell)  # every value except the year headers of a section
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
    assert set(layout.statements) == set(STATEMENT_SHEETS)
    assert "debt_principal_y1" in layout.schedule_inputs
    ids = [r.row_id for r in layout.statements[BS]]
    assert ids.index("total_assets") < ids.index("total_liabilities") < ids.index("total_le")
    assert layout.statements[BS][-1].kind == "check" and layout.statements[CF][-1].kind == "check"


def test_workbook_layout_rejects_unknown_rows(tmp_path):
    path = tmp_path / "workbook.yaml"
    path.write_text("number_formats: {dollars: '0', check: '0', ratio: '0', percent: '0', "
                    "days: '0', rate: '0'}\nstatements:\n  Income Statement:\n"
                    "    - {id: a, kind: subtotal, components: [+missing]}\n"
                    "  Balance Sheet: []\n  Cash Flow: []\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_workbook_layout(path)


# ---------------------------------------------------------------- one table per statement

def _rows(ws):
    return {c.value: c.row for c in ws["A"] if isinstance(c.value, str) and c.row >= FIRST_ROW}


def test_one_row_per_line_item_with_history_and_projection_side_by_side(built):
    wb, cols = built.workbook, built.grid.cols
    for sheet, label in ((IS, "Revenue"), (IS, "Net income"), (BS, "Total assets"),
                         (CF, "Operating cash flow"), (CF, "Closing cash")):
        ws = wb[sheet]
        labels = [c.value for c in ws["A"]]
        assert labels.count(label) == 1, (sheet, label)
        row = _rows(ws)[label]
        fy0 = ws.cell(row=row, column=cols.col(cols.hist[-1]))
        gaps = {(s, c) for s, c, _ in built.grid.gaps}
        assert fy0.value is not None or (sheet, fy0.coordinate) in gaps
        assert all(ws.cell(row=row, column=cols.col(y)).data_type == "f" for y in cols.proj)
    for sheet in (IS, BS, CF):
        text = " ".join(str(c.value) for c in wb[sheet]["A"] if c.value)
        assert "Model (integrated" not in text and "Reported" not in text


def test_statement_sheets_have_no_class_or_note_columns_and_a_year_header(built):
    cols = built.grid.cols
    for sheet in (IS, BS, CF):
        ws = built.workbook[sheet]
        header = [ws.cell(row=FIRST_ROW - 1, column=cols.col(y)).value for y in cols.years]
        assert header == [f"FY{y}A" for y in cols.hist] + [f"FY{y}E" for y in cols.proj]
        assert ws.max_column <= cols.col(cols.years[-1])
        tags = {"MODEL", "CHECK", "RF", "MA", "CALC", "REPORTED_FACT", "CALCULATION"}
        assert not any(c.value in tags for row in ws.iter_rows() for c in row)
        first = cols.col(cols.proj[0])
        assert all(ws.cell(row=r, column=first).border.left.style == "medium"
                   for r in range(FIRST_ROW - 1, ws.max_row + 1))


def test_comments_only_on_gap_cells():
    b = build(make_set(gaps={(2024, "net_income"): "DISAGREE"}))
    gaps = {(s, c) for s, c, _ in b.grid.gaps}
    for ws in b.workbook.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.comment is not None:
                    assert (ws.title, cell.coordinate) in gaps
                    assert cell.comment.text.startswith("GAP:")
                    assert len(cell.comment.text) <= 150


def test_subtotals_bold_with_top_border_and_italic_percent_rows(built):
    cols = built.grid.cols
    ws = built.workbook[IS]
    rows = _rows(ws)
    for label in ("Gross profit", "Net income"):
        for y in cols.years:
            cell = ws.cell(row=rows[label], column=cols.col(y))
            assert cell.font.bold and cell.border.top.style == "thin", (label, y)
    for label in ("% revenue growth", "% gross margin", "% EBIT margin", "% tax rate"):
        assert label in rows
        cells = [ws.cell(row=rows[label], column=cols.col(y)) for y in cols.years[1:]]
        assert all(c.data_type == "f" for c in cells[-len(cols.proj):])
        assert all(c.font.italic and c.number_format == "0.0%;(0.0%)" for c in cells
                   if c.value is not None)
    revenue = ws.cell(row=rows["Revenue"], column=cols.col(cols.hist[-1]))
    assert revenue.number_format == '#,##0,,;(#,##0,,);"–"'


def test_check_rows_are_zero_in_every_column_and_scenario(built):
    wb, cols = reload(built), built.grid.cols
    for name in ("Base", "Severe"):
        values = Evaluator(wb, {built.selector: name}).evaluate_all()
        for sheet in (BS, CF):
            ws = built.workbook[sheet]
            row = max(c.row for c in ws["A"] if str(c.value).startswith("Check:"))
            for y in cols.years:
                coord = ws.cell(row=row, column=cols.col(y)).coordinate
                if (sheet, coord) in built.grid.expected:
                    assert abs(values[(sheet, coord)]) <= 0.01, (sheet, coord, name)


def test_projection_starts_from_the_historical_fy0_cell(built):
    g, origin = built.grid, built.grid.cols.hist[-1]
    assert not [p for p in g.problems if "opening" in p]  # FY0 cells = model opening
    for key in ("m:revenue", "m:cash", "m:total_equity"):
        hist_sheet, hist = g.refs[(key, origin)]
        proj_sheet, proj = g.refs[(key, origin + 1)]
        assert hist_sheet == proj_sheet and hist[1:] == proj[1:]  # same row


def test_detail_rows_are_history_only(built):
    cols = built.grid.cols
    ws = built.workbook[BS]
    details = [c.row for c in ws["A"] if c.alignment.indent and c.row >= FIRST_ROW]
    assert details
    for r in details:
        assert all(ws.cell(row=r, column=cols.col(y)).value is None for y in cols.proj)


def test_forecast_sheet_sections_and_schedule_columns(built):
    ws, g = built.workbook[FORECAST], built.grid
    text = [str(c.value) for c in ws["A"] if c.value]
    for title in ("Scenario", "Switches and tolerance", "Maturity schedules"):
        assert any(t.startswith(title) for t in text), title
    assert any(t.startswith("Drivers") for t in text)
    assert any(t.startswith("Schedules (") for t in text)
    assert any(t.startswith("Model checks") for t in text)
    for y in g.cols.proj:
        if g.has("s:debt_principal", y):
            assert g.refs[("s:debt_principal", y)][1].startswith(g.cols.letter(y))
        assert g.refs[("f:capex", y)][1].startswith(g.cols.letter(y))


# ---------------------------------------------------------------- 7.1 liquidity and DSCR

def test_liquidity_block_shows_both_headroom_measures(built):  # #154
    g = built.grid
    for key in ("f:cash_headroom", "f:cash_shortfall", "f:reserve_headroom",
                "f:reserve_shortfall", "f:headroom", "f:shortfall", "chk:F5"):
        assert all(g.has(key, y) for y in g.cols.proj), key
    sheet, coord = g.refs[("d:headroom_basis", None)]
    assert built.workbook[sheet][coord].value == "cash_plus_haircut_securities"


def test_headroom_basis_cell_switches_the_measure_and_f5_follows():  # #154
    b = build(make_set({(2025, "short_term_investments"): 30}))
    wb, g = reload(b), b.grid
    ref = lambda key: g.refs[(key, 2026)]  # noqa: E731
    basis = g.refs[("d:headroom_basis", None)]
    default = Evaluator(wb, {b.selector: "Severe"}).evaluate_all()
    assert default[ref("f:headroom")] == pytest.approx(default[ref("f:reserve_headroom")])
    assert default[ref("f:reserve_headroom")] > default[ref("f:cash_headroom")]
    switched = Evaluator(wb, {b.selector: "Severe", basis: "cash_only"}).evaluate_all()
    assert switched[ref("f:headroom")] == pytest.approx(switched[ref("f:cash_headroom")])
    assert abs(switched[ref("chk:F5")]) <= 0.01
    assert switched[g.refs[("d:all_checks", None)]] == "OK"


def test_cash_only_config_matches_the_model_in_every_scenario():  # #154
    config = load_forecast_config(headroom_basis="cash_only")
    s = make_set({(2025, "short_term_investments"): 30})
    forecasts = {spec.name: build_forecast(s, AS_OF, config, spec) for spec in SPECS}
    b = build_workbook(s, forecasts, SPECS, config, AS_OF, {"run_id": "t", "as_of": "x"})
    assert all(p == [] for p in evaluate_scenarios(b, reload(b)).values())


def test_scoped_dscr_block_and_its_recomputation(built):
    wb, g = reload(built), built.grid
    for key in ("f:ds_cfo_capex", "f:ds_debt_principal", "f:ds_debt_interest",
                "f:ds_fl_principal", "f:ds_fl_interest", "f:ds_cfads", "f:debt_service",
                "f:scoped_dscr", "chk:dscr_status"):
        assert g.has(key, 2026), key
    v = Evaluator(wb, {built.selector: "Base"}).evaluate_all()
    dscr, num, den, cfo_capex, interest, fl_interest = (
        v[g.refs[(k, 2027)]] for k in ("f:scoped_dscr", "f:ds_cfads", "f:debt_service",
                                       "f:ds_cfo_capex", "f:ds_debt_interest",
                                       "f:ds_fl_interest"))
    assert num == pytest.approx(cfo_capex + interest + fl_interest)  # BC27
    assert dscr == pytest.approx(num / den) and v[g.refs[("chk:dscr_status", 2027)]] == "OK"


def test_ratios_sheet_has_the_working_capital_change_in_dollars(built):  # #156
    ws = built.workbook[RATIOS_SHEET]
    labels = [str(c.value) for c in ws["A"] if c.value]
    assert any(t.startswith("Operating working-capital change") for t in labels)


# ---------------------------------------------------------------- #95 restricted cash at FY0

RESTRICTED = "Less: restricted cash (included in statement cash)"


def test_restricted_cash_row_reconciles_fy0_closing_cash_to_fy1_opening():
    # Statement cash 110 includes 10 of restricted cash; balance-sheet cash is 100.
    b = build(make_set({(2025, "statement_cash"): 110}))
    ws, cols, g = b.workbook[CF], b.grid.cols, b.grid
    row = _rows(ws)[RESTRICTED]
    fy0 = ws.cell(row=row, column=cols.col(2025))
    assert fy0.data_type == "f"
    closing = g.ref("row:Cash Flow:closing_cash", 2025, CF)
    bs_cash = g.ref("row:Balance Sheet:cash", 2025, CF)
    assert fy0.value == f"={closing}-{bs_cash}"
    assert g.expected[(CF, fy0.coordinate)][ALL] == pytest.approx(10e6)
    # FY0 column only: no other historical or projected cell on the row.
    assert all(ws.cell(row=row, column=cols.col(y)).value is None
               for y in cols.years if y != 2025)
    # FY+1 opening cash = FY0 closing cash - restricted cash (= balance-sheet cash, 100).
    opening = ws[g.refs[("m:cf_opening_cash", 2026)][1]]
    assert opening.value == f"={closing}-{fy0.coordinate}"
    assert all(p == [] for p in evaluate_scenarios(b).values())
    assert all(p == [] for p in evaluate_scenarios(b, reload(b)).values())


def test_restricted_cash_is_a_gap_when_statement_cash_is_missing(built):
    # The synthetic set reports no statement cash: the row is a GAP cell, never 0, and FY+1
    # opening cash links balance-sheet cash directly.
    ws, cols, g = built.workbook[CF], built.grid.cols, built.grid
    row = _rows(ws)[RESTRICTED]
    fy0 = ws.cell(row=row, column=cols.col(2025))
    assert fy0.value is None and (CF, fy0.coordinate) in {(s, c) for s, c, _ in g.gaps}
    assert fy0.comment.text.startswith("GAP:")
    opening = ws[g.refs[("m:cf_opening_cash", 2026)][1]]
    assert opening.value == "=" + g.ref("m:cash", 2025, CF)


@pytest.mark.parametrize("term, message", [("-Nowhere:cash", "unknown sheet"),
                                           ("-Balance Sheet:missing", "unknown rows")])
def test_workbook_layout_checks_rows_on_other_sheets(tmp_path, term, message):
    path = tmp_path / "workbook.yaml"
    path.write_text("number_formats: {dollars: '0', check: '0', ratio: '0', percent: '0', "
                    "days: '0', rate: '0'}\nstatements:\n  Income Statement: []\n"
                    "  Balance Sheet:\n    - {id: cash, kind: line, metric: x}\n"
                    f"  Cash Flow:\n    - {{id: a, kind: line, residual: ['{term}'], "
                    "at: origin}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_workbook_layout(path)


def test_workbook_layout_reads_sheet_terms_and_origin_rows():
    row = next(r for r in load_workbook_layout().statements[CF] if r.row_id == "restricted_cash")
    assert row.at == "origin"
    assert [(t.sign, t.sheet, t.row) for t in row.residual] == [(1, None, "closing_cash"),
                                                                 (-1, BS, "cash")]


# ---------------------------------------------------------------- Phase 5: Correlation Agent

def test_verification_sheet_shows_corrected_values_and_the_final_check():
    from dataclasses import replace
    from decimal import Decimal

    from finagent.records import AgentValue, IdentityResult, StatementsCheck
    from finagent.statements.verify_set import CORRECTED

    s = make_set()
    tax = s.observations[(2025, "income_tax_expense")]
    s.observations[(2025, "income_tax_expense")] = replace(
        tax, flags=tax.flags + (CORRECTED,), correction="agent:flip_sign",
        corrected_from_value=-tax.value, corrected_from_row="0000999999-26-000001:IS:9")
    value = AgentValue("SYNT", 2025, "income_tax_expense", "CORRECTED", -tax.value,
                       "0000999999-26-000001:IS:9", tax.value, "0000999999-26-000001:IS:9",
                       ("flip_sign",), (("I5", "PASS", Decimal(0)),))
    check = StatementsCheck("SYNT", 2025, "VERIFIED", "I9 and I10 exactly zero", (
        IdentityResult("SYNT", 2025, "I9", Decimal(0), Decimal(0), "PASS"),
        IdentityResult("SYNT", 2025, "I10", Decimal(0), Decimal(0), "PASS")))
    config = load_forecast_config()
    specs = load_scenarios()
    forecasts = {spec.name: build_forecast(s, AS_OF, config, spec) for spec in specs}

    class Rec:  # the two fields the workbook reads from a Reconciliation
        results = (type("R", (), {"values": (value,)})(),)
        checks = {2025: check}

    ws = build_workbook(s, forecasts, specs, config, AS_OF, reconciliation=Rec()) \
        .workbook["Verification"]
    rows = {tuple(c.value for c in r[:3]): [c.value for c in r] for r in ws.iter_rows()}
    tax_row = rows[(2025, "income_tax_expense", tax.statement)]
    assert CORRECTED in tax_row[12] and tax_row[15].startswith("agent:flip_sign; original")
    texts = [c.value for r in ws.iter_rows() for c in r if isinstance(c.value, str)]
    assert any(t.startswith("Correlation Agent (8.8)") for t in texts)
    agent_row = next(r for r in ws.iter_rows() if r[2].value == "CORRECTED")
    assert agent_row[1].value == "income_tax_expense" and agent_row[3].value == "flip_sign"
    final = next(r for r in ws.iter_rows() if r[1].value == "VERIFIED" and r[2].value == "PASS")
    assert final[0].value == 2025 and final[8].value == "I9 and I10 exactly zero"
