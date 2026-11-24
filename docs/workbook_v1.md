# Excel workbook v1 (Phase 4d)

Current run: `workbooks-0004`, as of 2026-11-24, with the Correlation Agent (Phase 5, product mode). The statement sets are the ones after the agent, so AMZN (income tax, flip_sign) and MRK (PP&E, swap to the net row) now have forecasts. The Verification sheet shows the CORRECTED values and the final check (BC22). Layout as workbooks-0003. Git HEAD cf18d18 with uncommitted changes (#106). M1 model `m1-tfidf-lr-v3`. Evidence manifest SHA-256 `89ee4161…99ab0`.

Outputs:
- One workbook per validation company at `runs/workbooks-0004/<TICKER>.xlsx` (gitignored).
- Next to each one, `<TICKER>.manifest.json`: the expected value of every formula cell per scenario, plus the GAP cells.
- `summary.json` and `recalc.json` for the run.

`runs/workbooks-0001` (the first, two-block layout, d0fea8c), `runs/workbooks-0002` (one table per statement, before #95) and `runs/workbooks-0003` (before the agent) are kept unchanged. For MSFT, CVX, NKE, T, CL and ABT, workbooks-0004 has the same formula and GAP cell counts as workbooks-0003; the agent changed no value of theirs.

Commands:
- Build: `python -m finagent.workbook.build --run-id workbooks-0004 [--splits validation,train] [--tickers ...] [--no-agent]`. Held-out companies are refused. `--no-agent` reproduces the Phase 4d workbooks.
- LibreOffice recheck: `python -m finagent.workbook.recalc --run-id workbooks-0004`.

## 1. Results (workbooks-0004)

| company | Base / Moderate / Severe | Correlation Agent | final check at origin (BC22) | formula cells | GAP cells | independent evaluator (B / M / S) | LibreOffice recalculation (B / M / S) |
|---|---|---|---|---|---|---|---|
| AMZN | VERIFIED / VERIFIED / VERIFIED | income tax CORRECTED (flip_sign) FY2021-FY2025 | VERIFIED (FY2025) | 605 | 14 | 0 / 0 / 0 mismatches | PASS 0 / 0 / 0 |
| MSFT | VERIFIED / VERIFIED / VERIFIED | no change | VERIFIED (FY2026) | 609 | 9 | 0 / 0 / 0 | PASS 0 / 0 / 0 |
| CVX | VERIFIED / VERIFIED / VERIFIED | no change | VERIFIED (FY2025; I11 REVIEW 35M) | 619 | 6 | 0 / 0 / 0 | PASS 0 / 0 / 0 |
| NKE | VERIFIED / VERIFIED / VERIFIED | no change | VERIFIED (FY2026) | 590 | 22 | 0 / 0 / 0 | PASS 0 / 0 / 0 |
| MRK | VERIFIED / VERIFIED / VERIFIED | ppe_net CORRECTED (swap to the net row) FY2021-FY2025 | VERIFIED (FY2025) | 587 | 18 | 0 / 0 / 0 | PASS 0 / 0 / 0 |
| T | VERIFIED / VERIFIED / VERIFIED | FY2022 CFO, CFI, CFF REVIEW (no change, #103) | VERIFIED (FY2025; I11 REVIEW 104M) | 539 | 40 | 0 / 0 / 0 | PASS 0 / 0 / 0 |
| CL | VERIFIED / VERIFIED / VERIFIED | no change | VERIFIED (FY2025; I11 REVIEW 1M) | 581 | 16 | 0 / 0 / 0 | PASS 0 / 0 / 0 |
| ABT | VERIFIED / VERIFIED / VERIFIED | no change | VERIFIED (FY2025; I11 REVIEW -185M) | 590 | 14 | 0 / 0 / 0 | PASS 0 / 0 / 0 |

The AMZN and MRK workbooks are now full models: 235 → 605 and 226 → 587 formula cells. The other six are unchanged from workbooks-0003.

How the two checks work:
- **Independent evaluator.** `finagent/workbook/evaluate.py` reads the saved formulas, sets the selector to each scenario and computes every cell. It compares each value with the Python model (`build_forecast` per scenario; `finagent.model.ratios` for ratios). Tolerance is $0.01 for dollars, 1e-9 for ratios, and exact for text.
- **LibreOffice recalculation.** For each scenario it saves a copy with the selector set, recalculates headless, reads the cached values back and compares them with the same tolerances. A cell with no cached value counts as a failure.

Structure checks on all eight files:
- Every non-empty projected cell on the statement and Ratios sheets is a formula.
- The only numeric literals in projected formulas are 0, 1 and 2: `1 + growth`, the average `(a + b) / 2`, and `MAX(x, 0)`.
- Every GAP cell is empty.
- The only cell comments are the GAP notes.
- No CALCULATION fell back to a static value (no build problems).
- The FY0 cells the projections start from equal the Python model's opening position (`ModelWriter.check_opening`; difference ≤ $0.01, otherwise a build problem).

## 2. Layout (BC21)

### Statement sheets: one table per statement

Each statement sheet is a single table. Each line item is one row across FY-4 … FY0 (A) | FY+1 … FY+3 (E). Row 5 is the year header, and a vertical line separates actuals from estimates.

The rows come from `configs/workbook.yaml`:
- **Income statement:** Revenue, Cost of revenue, Gross profit, the operating cost lines, EBIT, Interest expense, Other non-operating, Pre-tax income, Income tax, Net income.
- **Balance sheet:** Assets, Liabilities and Equity sections.
- **Cash flow:** operating, investing and financing sections, then net change, opening and closing cash.

How a row's history is filled:
- **Reported line:** the accepted value in blue. A CALCULATION is a formula, and a face-block total is the sum of its "of which" rows.
- **Subtotal with components** (gross profit, total liabilities and equity, net change in cash, closing cash): a formula over the rows above when they add up exactly to the reported value. Otherwise it is the reported value.
- **Model line with no single reported row** (other cash operating costs, other assets, other liabilities, other equity, working capital, and other investing / financing / non-operating): a formula over the reported rows. It is anchored on the reported totals (total assets, total liabilities, EBIT, pre-tax income, CFO, CFI, CFF). A row the filer never reports is left out of the formula, never counted as 0.
- **Debt:** follows the model's opening rule. It is funded debt, net of finance leases when the debt concept includes them.
- **Total equity:** the I9 equity operands.

How a row's projection is filled:
- The projected cells are the integrated model's formulas. FY+1 starts from the FY0 cell of the same row.
- Reported lines the model does not project individually sit as indented rows under the line or subtotal they belong to: R&D, SG&A, total current assets / liabilities, debt components, lease split, equity components. They show history only, and their projected cells are blank. The same applies to other non-operating, other items below tax, other investing / financing, and FX.

Formatting:
- Subtotals and totals are bold with a thin top border.
- Italic percentage rows sit under key lines: % revenue growth, % gross margin, % EBIT margin and % tax rate. They show "n.m." when the denominator is ≤ 0.

The bottom check rows show 0:
- **Balance sheet:** total assets − total liabilities − total equity. In history this uses the rows; in projections it is F1, with each line summed afresh.
- **Cash flow, history:** opening cash + net change − closing cash. This is the statement roll-forward, which includes restricted cash (I10).
- **Cash flow, projections:** closing cash − balance sheet cash.

FY+1 opening cash is balance-sheet cash and cash equivalents at FY0, which is the cash the model carries. Statement closing cash (I10) includes restricted cash, so the cash flow has a reconciling row in the FY0 column only (#95): **Less: restricted cash (included in statement cash)** = statement closing cash - balance-sheet cash and equivalents, a formula over the two reported cells. FY+1 opening cash is written as FY0 closing cash less that row, which is the same amount as balance-sheet cash, so the cash flow reads across from FY0 to FY+1. If either reported value is missing, the row is a GAP cell and FY+1 opening cash links balance-sheet cash directly. workbooks-0003 values: AMZN 3,296M, CVX 992M, MRK 125M, T 293M; MSFT, NKE, CL and ABT 0 (shown as "–").

The Class and Note columns are removed. Statuses, reasons and sources are on the Verification and Sources sheets. The only cell comment is a short "GAP:" note on a GAP cell.

### Other sheets

- **Cover.** Company, CIK, as-of date, run ID, commit, M1 version, evidence hash and units. A status banner: forecast VERIFIED, or GAP / INVALID with the reasons and the BC16 note. Scenario statuses, the selected scenario and "all model checks" (both formulas), filings, a verification summary and the legend.
- **Ratios.** C.1 credit ratios and operating ratios as formulas, historical and projected, with "n.m." for a non-positive denominator. Helper rows hold period days, funded debt and equity.
- **Forecast.** Same logic as workbooks-0001, laid out in titled sections. Per-year rows use the statement sheets' year columns.
  1. **Scenario:** the selector (drop-down), the scenario status and "all model checks".
  2. **Scenario table** from `configs/scenarios.yaml`, with a "Selected" column built from `INDEX/MATCH`.
  3. **Switches and tolerance:** the refinance switch (live), the working-capital basis and the check tolerance.
  4. **Drivers:** base value (blue), scenario value (formula), status, and a combined basis / flags / note text.
  5. **Maturity schedules:** the 10-K maturity schedules, as blue inputs in the projected year columns (schedule year 1 = FY+1).
  6. **Schedules:** working capital, PP&E, debt, finance lease, operating lease with the ROU asset, and equity. FY0 links to the balance-sheet cell.
  7. **Liquidity (F5):** headroom, shortfall and the conditional securities reserve.
  8. **Model checks:** F1 and cash tie-out status (the statement check rows), then F2–F5 with OK/FAIL status cells.
- **Verification.** Every value with its 8.7 status, reason, channels, document value and tag value. A value changed by the Correlation Agent is VERIFIED with the flag CORRECTED, and the last column, "Corrected from", gives the action and the original reading with its row. Then two blocks: "Correlation Agent (8.8)", listing each CORRECTED or REVIEW value with its actions, original and final reading and rows, and the checks it enters; and "Final check (BC22)", giving each fiscal year's status with the I9, I10 and I11 status and residual. After those come identities I1–I12 with residuals, statement-location GAP reasons and notes, and the Python model checks per scenario.
- **Sources.** Accession, filed date, URL, statement, row label, row id and concepts for every reported fact and every face-block component.

Formatting:
- Amounts are stored in full dollars and shown in $ millions with no decimals, and brackets for negatives (zero shows "–" except on check rows).
- Blue marks an input (REPORTED_FACT or MODEL_ASSUMPTION); black marks a formula.
- A shaded empty cell with a "GAP:" note is a missing value.
- Panes are frozen below the year header and right of the labels.
- The workbook asks for a full recalculation on load.

## 3. Points to confirm (#92, #95)

1. Ratio equity: I9 equity operands. In projections this is retained earnings + other equity.
2. The projected current ratio, quick ratio and CFO / current liabilities are not modelled.
3. DSO / DIO / DPO for the first historical year are GAP (no opening balance).
4. Projected funded debt includes finance leases only when the filer's debt concept does.
5. If a shock takes a share below zero, the scenario status cell shows GAP (no validation company has one).
6. The working-capital basis is static text; the refinance switch is live.
7. The historical check rows replace the separate I9 / I10 rows of workbooks-0001.
   - The balance-sheet check uses the rows shown (assets − liabilities − equity).
   - The cash-flow check is the statement roll-forward.
   - The I9 / I10 results with their operands stay on the Verification sheet.
8. FY+1 opening cash is balance-sheet cash and equivalents, as in the model. The FY0 restricted-cash row reconciles it to statement closing cash (#95, decided by Vineet).

## Notes (hand-written)
