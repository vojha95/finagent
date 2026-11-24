"""Cover, Verification and Sources sheets (blueprint 8.9; Phase 4d)."""

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from openpyxl.worksheet.worksheet import Worksheet

from finagent.records import AgentValue, Forecast, StatementsCheck
from finagent.statements.build import StatementSet
from finagent.workbook.grid import (BANNER_FILL, BLACK, BLUE, BOLD, GAP_FILL, GREY,
                                    HEADER_FILL, OK_FILL, TITLE, WRAP, Grid)


def _table_header(ws: Worksheet, row: int, names: Sequence[str]) -> None:
    for c, name in enumerate(names, start=1):
        cell = ws.cell(row=row, column=c, value=name)
        cell.font = BOLD
        cell.fill = HEADER_FILL


def _plain(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (int, float, str)):
        return value
    return str(value)


def _correction(o) -> str:
    """CORRECTED (Correlation Agent): the action and the original reading it replaced."""
    if not o.correction:
        return ""
    row = (o.corrected_from_row or "").rsplit(":", 2)
    where = f" (row {row[-2]}:{row[-1]})" if len(row) == 3 else ""
    original = "—" if o.corrected_from_value is None else f"{o.corrected_from_value:,.0f}"
    return f"{o.correction}; original reading {original}{where}"


def verification(ws: Worksheet, s: StatementSet, forecasts: Mapping[str, Forecast],
                 formats: Mapping[str, str], agent_values: Sequence[AgentValue] = (),
                 checks: Mapping[int, StatementsCheck] | None = None) -> None:
    ws.cell(row=1, column=1, value=f"{s.issuer}: verification").font = TITLE
    ws.cell(row=2, column=1, value="Every value of the validated statement set with its 8.7 "
                                   "result (CORRECTED: changed by the Correlation Agent, "
                                   "original reading kept); identity residuals I1-I12; the "
                                   "agent's values and the final check (BC22); GAP reasons; "
                                   "model checks per scenario. Amounts in dollars as "
                                   "reported.").font = GREY
    row = 4
    ws.cell(row=row, column=1, value="Values (channel agreement)").font = BOLD
    row += 1
    names = ["Fiscal year", "Metric", "Statement", "Row label", "Status", "Reason",
             "Classification", "Accepted", "Channels", "Value used", "Document value",
             "Tag value", "Flags", "Basis", "Detail", "Corrected from (Correlation Agent)"]
    _table_header(ws, row, names)
    ws.freeze_panes = ws.cell(row=row + 1, column=3)
    row += 1
    for (year, metric), o in sorted(s.observations.items()):
        values = [year, metric, o.statement, o.row_label, o.status, o.reason,
                  o.classification, "yes" if o.accepted else "no", ", ".join(o.channels),
                  _number(o.value), _number(o.document_value), _number(o.tag_value),
                  ", ".join(o.flags), o.basis, (o.detail or "")[:300], _correction(o)]
        for c, v in enumerate(values, start=1):
            cell = ws.cell(row=row, column=c, value=_plain(v))
            if c in (10, 11, 12) and isinstance(v, float):
                cell.number_format = "#,##0"
        if o.status in ("GAP", "DISAGREE", "CONFLICT", "INVALID") or not o.accepted:
            ws.cell(row=row, column=5).fill = GAP_FILL
        row += 1
    row += 1
    if checks is not None:
        row = agent_block(ws, row, agent_values, checks)
    ws.cell(row=row, column=1, value="Identities (Appendix B I1-I12)").font = BOLD
    row += 1
    _table_header(ws, row, ["Fiscal year", "Identity", "Subject", "Status", "Residual",
                            "Tolerance", "Missing", "Omitted", "Provisional", "Detail"])
    row += 1
    order = {f"I{n}": n for n in range(1, 13)}
    for r in sorted(s.identities, key=lambda r: (r.fiscal_year, order.get(r.identity_id, 99),
                                                 r.subject or "")):
        values = [r.fiscal_year, r.identity_id, r.subject, r.status, _number(r.residual),
                  _number(r.tolerance), ", ".join(r.missing), ", ".join(r.omitted),
                  ", ".join(r.provisional), (r.detail or "")[:300]]
        for c, v in enumerate(values, start=1):
            ws.cell(row=row, column=c, value=_plain(v))
        ws.cell(row=row, column=4).fill = OK_FILL if r.status == "PASS" else GAP_FILL
        row += 1
    row += 1
    ws.cell(row=row, column=1, value="GAP reasons and notes").font = BOLD
    row += 1
    for year, reason in sorted(s.gap_reasons.items()):
        if reason:
            ws.cell(row=row, column=1, value=year)
            ws.cell(row=row, column=2, value=f"statement location: {reason}")
            row += 1
    for note in s.notes:
        ws.cell(row=row, column=2, value=note)
        row += 1
    row += 1
    ws.cell(row=row, column=1, value="Integrated forecast and model checks (Python run, per "
                                     "scenario)").font = BOLD
    row += 1
    _table_header(ws, row, ["Scenario", "Status", "Check", "Fiscal year", "Check status",
                            "Residual", "Tolerance", "Detail / reasons"])
    row += 1
    for name, f in forecasts.items():
        if not f.checks:
            ws.cell(row=row, column=1, value=name)
            ws.cell(row=row, column=2, value=f.status)
            ws.cell(row=row, column=8, value="; ".join(f.reasons)[:500])
            row += 1
        for c in f.checks:
            for col, v in enumerate([name, f.status, c.check_id, c.fiscal_year, c.status,
                                     c.residual, c.tolerance, c.detail], start=1):
                ws.cell(row=row, column=col, value=_plain(v))
            row += 1
    for col, width in zip("ABCDEFGHIJKLMNOP", (11, 34, 10, 40, 15, 22, 16, 9, 16, 18, 18, 18,
                                               30, 30, 80, 60)):
        ws.column_dimensions[col].width = width


def agent_block(ws: Worksheet, row: int, values: Sequence[AgentValue],
                checks: Mapping[int, StatementsCheck]) -> int:
    """The Correlation Agent's CORRECTED / REVIEW values and the final check per year."""
    ws.cell(row=row, column=1, value="Correlation Agent (8.8): values changed or escalated")\
        .font = BOLD
    row += 1
    _table_header(ws, row, ["Fiscal year", "Metric", "Agent status", "Actions",
                            "Original reading", "Original row", "Value after", "Row after",
                            "Checks the value enters (status, residual)"])
    row += 1
    if not values:
        ws.cell(row=row, column=2, value="none: no agent action")
        row += 1
    for v in sorted(values, key=lambda v: (v.fiscal_year, v.item)):
        cells = [v.fiscal_year, v.item, v.status, " + ".join(v.actions) or "—",
                 _number(v.original_value), v.original_row_id, _number(v.final_value),
                 v.final_row_id, "; ".join(f"{c} {st} {r}" for c, st, r in v.checks)[:300]]
        for c, value in enumerate(cells, start=1):
            cell = ws.cell(row=row, column=c, value=_plain(value))
            if c in (5, 7) and isinstance(value, float):
                cell.number_format = "#,##0"
        ws.cell(row=row, column=3).fill = OK_FILL if v.status == "CORRECTED" else GAP_FILL
        row += 1
    row += 1
    ws.cell(row=row, column=1, value="Final check (BC22, BC24): I9 and I10 on the final values, "
                                     "exactly zero (half a unit per rounded component for a "
                                     "face-block sum); I11 when computable (REVIEW, "
                                     "non-blocking)")\
        .font = BOLD
    row += 1
    _table_header(ws, row, ["Fiscal year", "Status", "I9", "I9 residual", "I10",
                            "I10 residual", "I11", "I11 residual", "Reason / review"])
    row += 1
    for year, c in sorted(checks.items()):
        ids = {r.identity_id: r for r in c.identities}
        cells: list[Any] = [year, c.status]
        for identity_id in ("I9", "I10", "I11"):
            r = ids.get(identity_id)
            cells += [r.status if r else "—", _number(r.residual) if r else None]
        cells.append(c.reason + (f"; review: {'; '.join(c.review)}" if c.review else ""))
        for col, value in enumerate(cells, start=1):
            ws.cell(row=row, column=col, value=_plain(value))
        ws.cell(row=row, column=2).fill = OK_FILL if c.status == "VERIFIED" else GAP_FILL
        row += 1
    return row + 1


def _number(value: Any) -> float | None:
    return None if value is None else float(value)


def sources(ws: Worksheet, s: StatementSet) -> None:
    ws.cell(row=1, column=1, value=f"{s.issuer}: sources").font = TITLE
    ws.cell(row=2, column=1, value="Accession, statement, row label and concept for every "
                                   "reported fact (REPORTED_FACT values and face-block "
                                   "components).").font = GREY
    row = 4
    _table_header(ws, row, ["Fiscal year", "Metric", "Status", "Statement", "Row label",
                            "Row id", "Concepts", "Accession", "Filed", "Filing URL",
                            "Value"])
    ws.freeze_panes = ws.cell(row=row + 1, column=3)
    row += 1
    for (year, metric), o in sorted(s.observations.items()):
        rows = []
        if o.classification == "REPORTED_FACT" and o.value is not None:
            rows.append((metric, o.row_label, o.row_id, ", ".join(o.concepts), o.value))
        for row_id, label, value in o.components:
            rows.append((f"{metric} (component)", label, row_id, "", value))
        for m, label, row_id, concepts, value in rows:
            values = [year, m, o.status, o.statement, label, row_id, concepts, o.accession,
                      o.filed.isoformat(), o.filing_url, _number(value)]
            for c, v in enumerate(values, start=1):
                ws.cell(row=row, column=c, value=_plain(v))
            ws.cell(row=row, column=11).number_format = "#,##0"
            row += 1
    for col, width in zip("ABCDEFGHIJK", (11, 36, 15, 10, 50, 30, 50, 24, 12, 70, 18)):
        ws.column_dimensions[col].width = width


def cover(ws: Worksheet, grid: Grid, s: StatementSet, forecasts: Mapping[str, Forecast],
          header: Mapping[str, Any], disagree: Sequence[str], banner: str) -> None:
    ws.cell(row=1, column=1, value=f"{s.issuer}: FinAgent model workbook").font = TITLE
    rows: list[tuple[str, Any]] = [
        ("Company (ticker)", s.issuer), ("CIK", s.cik),
        ("As-of date", header.get("as_of")), ("Run ID", header.get("run_id")),
        ("Git commit", f"{header.get('git_commit')}"
                       + (" (working tree dirty)" if header.get("git_dirty") else "")),
        ("Document model (M1) version", header.get("model_version") or s.model_version),
        ("Evidence manifest SHA-256", header.get("evidence_manifest_sha256")),
        ("Units", "US$; amounts stored in full dollars and displayed in $ millions"),
    ]
    row = 3
    for label, value in rows:
        ws.cell(row=row, column=1, value=label).font = BOLD
        ws.cell(row=row, column=2, value=_plain(value))
        row += 1
    row += 1
    for c in range(1, 8):
        ws.cell(row=row, column=c).fill = BANNER_FILL if not banner.startswith("Forecast "
                                                                               "VERIFIED") \
            else OK_FILL
    ws.cell(row=row, column=1, value=banner).font = BOLD
    ws.cell(row=row, column=1).alignment = WRAP
    ws.row_dimensions[row].height = 48
    row += 2
    ws.cell(row=row, column=1, value="Scenario status (Python run)").font = BOLD
    row += 1
    for name, f in forecasts.items():
        ws.cell(row=row, column=1, value=name)
        ws.cell(row=row, column=2, value=f.status)
        ws.cell(row=row, column=3, value="; ".join(f.reasons)[:300] or ", ".join(f.flags))
        row += 1
    if grid.has("d:selector"):
        ws.cell(row=row, column=1, value="Selected scenario (Forecast sheet)").font = BOLD
        grid.formula(ws, row, 2, grid.ref("d:selector", None, ws.title),
                     {n: n for n in grid.scenarios}, fmt="text", tolerance="exact")
        row += 1
    if grid.has("d:all_checks"):
        ws.cell(row=row, column=1, value="All model checks in the workbook").font = BOLD
        sheet, coord = grid.refs[("d:all_checks", None)]
        grid.formula(ws, row, 2, grid.ref("d:all_checks", None, ws.title),
                     dict(grid.expected[(sheet, coord)]), fmt="text", tolerance="exact")
        row += 1
    if disagree:
        ws.cell(row=row, column=1, value="Forecast inputs read as DISAGREE (BC16)").font = BOLD
        ws.cell(row=row, column=2, value=", ".join(disagree))
        row += 1
    row += 1
    ws.cell(row=row, column=1, value="Filings").font = BOLD
    row += 1
    for c, name in enumerate(["Fiscal year", "Accession", "Period start", "Period end",
                              "Filed", "Filing URL"], start=1):
        ws.cell(row=row, column=c, value=name).font = BOLD
    row += 1
    for f in s.filings:
        for c, v in enumerate([f.fiscal_year, f.accession, f.start, f.end, f.filed,
                               f.filing_url], start=1):
            ws.cell(row=row, column=c, value=_plain(v))
        row += 1
    row += 1
    ws.cell(row=row, column=1, value="Verification summary (values per status)").font = BOLD
    row += 1
    counts = Counter(o.status for o in s.observations.values())
    for status, n in sorted(counts.items()):
        ws.cell(row=row, column=1, value=status)
        ws.cell(row=row, column=2, value=n)
        row += 1
    identities = Counter((r.identity_id, r.status) for r in s.identities
                         if r.identity_id in ("I9", "I10", "I11"))
    for (identity_id, status), n in sorted(identities.items()):
        ws.cell(row=row, column=1, value=f"{identity_id} {status}")
        ws.cell(row=row, column=2, value=n)
        row += 1
    row += 1
    ws.cell(row=row, column=1, value="Legend").font = BOLD
    row += 1
    sample = ws.cell(row=row, column=1, value="Blue number: REPORTED_FACT or MODEL_ASSUMPTION "
                                              "input (hard-coded)")
    sample.font = BLUE
    row += 1
    ws.cell(row=row, column=1, value="Black number: CALCULATION, a live formula").font = BLACK
    row += 1
    gap = ws.cell(row=row, column=1, value="Shaded empty cell: GAP (no accepted value; the "
                                           "note says why). Never 0.")
    gap.fill = GAP_FILL
    row += 1
    ws.cell(row=row, column=1, value="Empty cell with a note, no shading: the line does not "
                                     "exist there (not projected / not modelled / not "
                                     "applicable)")
    row += 1
    ws.cell(row=row, column=1, value="Columns FY..A are actuals, FY..E projections; projected "
                                     "cells are formulas driven by the Forecast sheet and its "
                                     "scenario selector").font = GREY
    row += 1
    ws.cell(row=row, column=1, value="Class column: RF reported fact, CALC calculation, LINK "
                                     "link to another sheet, MODEL forecast line, MA model "
                                     "assumption, CHECK check").font = GREY
    ws.column_dimensions["A"].width = 44
    ws.column_dimensions["B"].width = 40
    ws.column_dimensions["C"].width = 60
