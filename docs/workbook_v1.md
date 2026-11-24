# Excel workbook v1 (Phase 4d)

Run `workbooks-0001`, as of 2026-11-24. Git HEAD fea04a5 with uncommitted 4d changes (#93). M1 model `m1-tfidf-lr-v3`. Evidence manifest SHA-256 `89ee4161…99ab0`. One workbook per validation company at `runs/workbooks-0001/<TICKER>.xlsx` (gitignored). Next to each one: `<TICKER>.manifest.json` (the expected value of every formula cell per scenario, plus the GAP cells), `summary.json` and `recalc.json`.

Build: `python -m finagent.workbook.build --run-id workbooks-0001 [--splits validation,train] [--tickers ...]`. Held-out companies are refused.
LibreOffice recheck: `python -m finagent.workbook.recalc --run-id workbooks-0001`.

## 1. Results

| company | Base forecast | formula cells | GAP cells | independent evaluator (Base / Moderate / Severe) | LibreOffice recalculation (Base / Moderate / Severe) |
|---|---|---|---|---|---|
| AMZN | GAP (DISAGREE: income_tax_expense FY2025; BC16) | 187 | 40 | 0 / 0 / 0 mismatches | PASS 0 / 0 / 0 |
| MSFT | VERIFIED | 549 | 9 | 0 / 0 / 0 | PASS 0 / 0 / 0 |
| CVX | VERIFIED | 564 | 6 | 0 / 0 / 0 | PASS 0 / 0 / 0 |
| NKE | VERIFIED | 530 | 18 | 0 / 0 / 0 | PASS 0 / 0 / 0 |
| MRK | GAP (DISAGREE: ppe_net FY2025; BC16) | 165 | 18 | 0 / 0 / 0 | PASS 0 / 0 / 0 |
| T | VERIFIED | 507 | 62 | 0 / 0 / 0 | PASS 0 / 0 / 0 |
| CL | VERIFIED | 529 | 15 | 0 / 0 / 0 | PASS 0 / 0 / 0 |
| ABT | VERIFIED | 536 | 14 | 0 / 0 / 0 | PASS 0 / 0 / 0 |

How the two checks work:
- **Independent evaluator.** `finagent/workbook/evaluate.py` reads the saved formulas, sets the selector to each scenario and computes every cell. It compares each value with the Python model (`build_forecast` per scenario; `finagent.model.ratios` for ratios). Tolerance is $0.01 for dollars, 1e-9 for ratios, and exact for text.
- **LibreOffice recalculation.** LibreOffice was installed during the run, so the check ran rather than staying pending. For each scenario it saves a copy with the selector set, recalculates headless, reads the cached values back and compares them with the same tolerances. A cell with no cached value counts as a failure.

Structure checks on all eight files:
- Every non-empty projected cell on the statement, Ratios and Forecast-schedule sheets is a formula (MSFT/CVX 336 cells, NKE/CL/ABT 312, T 309).
- The only numeric literals in projected formulas are 0, 1 and 2: `1 + growth`, the average `(a + b) / 2`, and `MAX(x, 0)`.
- Every GAP cell is empty.
- No CALCULATION fell back to a static value.

## 2. Layout (BC21)

- **Cover.** Company, CIK, as-of date, run ID, commit, M1 version, evidence hash and units. A status banner: forecast VERIFIED, or GAP / INVALID with the reasons and the BC16 note. Scenario statuses, the selected scenario and "all model checks" (both formulas), filings, a verification summary and the legend.
- **Income Statement, Balance Sheet, Cash Flow.** Columns FY-4 … FY0 (A) | FY+1 … FY+3 (E). Block A is the reported detail (history only). Block B is the integrated model. The checks are:
  - I9 balance check and I10 cash tie-out per historical year, as formulas over the identity's own operands;
  - F1 per model year;
  - the projected cash tie-out (balance sheet cash = cash flow closing cash).
- **Ratios.** C.1 credit ratios and operating ratios as formulas, historical and projected, with "n.m." for a non-positive denominator. Helper rows hold period days, funded debt and equity.
- **Forecast.**
  - The scenario selector (drop-down) and the scenario table from `configs/scenarios.yaml`, with a "Selected" column built from `INDEX/MATCH`.
  - Switches: the refinance switch is live. The working-capital basis and the check tolerance are also shown here.
  - Drivers: base value (blue), scenario value (formula), basis, status, flags and note.
  - The 10-K maturity schedules (blue inputs).
  - Schedules: working capital, PP&E, debt, finance lease, operating lease with the ROU asset, equity, and liquidity (headroom, shortfall, conditional securities reserve).
  - Checks F2–F5 with OK/FAIL status cells.
- **Verification.** Every value with its 8.7 status, reason, channels, document value and tag value. Identities I1–I12 with residuals. Statement-location GAP reasons and notes. Python model checks per scenario.
- **Sources.** Accession, filed date, URL, statement, row label, row id and concepts for every reported fact and every face-block component.

Formatting:
- Amounts are full dollars, displayed in $ millions.
- Blue marks an input (REPORTED_FACT or MODEL_ASSUMPTION); black marks a formula.
- A shaded empty cell with a "GAP:" note is a missing value. An unshaded empty cell with a note is a line that does not exist there (not projected, not modelled, not applicable).
- Panes are frozen below the year header and right of the labels.
- The workbook asks for a full recalculation on load.

## 3. Points to confirm (#92)

1. Ratio equity: I9 equity operands. In projections this is retained earnings + other equity.
2. The projected current ratio, quick ratio and CFO / current liabilities are not modelled.
3. DSO / DIO / DPO for the first historical year are GAP (no opening balance).
4. Projected funded debt includes finance leases only when the filer's debt concept does.
5. If a shock takes a share below zero, the scenario status cell shows GAP (no validation company has one).
6. The working-capital basis is static text; the refinance switch is live.
7. The model block's FY0 income-statement column carries revenue only.
8. The status cell of the historical I9 / I10 check uses the $0.01 tolerance; the residual cell itself is exact.

## Notes (hand-written)
