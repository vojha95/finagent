# Correlation Agent v1 (Phase 5a/5b)

Blueprint 8.8; decisions of 2026-11-25 (Vineet): BC22 (final check), BC23 (evaluation-mode NCI), CORRECTED recorded as a flag on a VERIFIED value, rescale / flip prior 0.5 [Guessing]. Config: `configs/correlation_agent.yaml`. Code: `finagent/verify/` (`edits.py`, `workspace.py`, `agent_tools.py`, `correlation_agent.py`, `reconcile.py`). Results per filing: `runs/statements-0006/agent.json` and `final_check.csv`; summary tables in `docs/statement_set_v1.md` section 9.

E2 (5c, evaluation mode with injected errors) is not run yet.

## 1. How it works

**Edits.** Every change is a `DocumentEdit`: read the item from a row of the same filing (its own cell for the period), times 1,000 or 1/1,000, sign reversed or not. It is applied in memory right after the document channel reads the filing (`apply_edits`). Agent corrections and E2 injections use the same hook. `evidence/` is never written; a blank cell or an unknown row raises. Every value the agent writes is therefore a displayed cell, rescaled or sign-reversed: no plug, and a missing value stays missing.

**Two modes.**
- *Product* (tags visible; used here). The agent sees the statement build's readings, tags, A.2 model inputs and NCI status. It may change only DISAGREE values and document-only values. A DISAGREE value enters the identities with its document value and has its own check `D:<item>` (document = tag within displayed precision, BC15 included).
- *Evaluation* (E2; tags hidden). The state is built from rows, predictions and the filing only; there is no tag argument, and a state with tags forced in gives the same trace (test). NCI follows BC23.

**Loop, per filing (own fiscal year).**
1. Checks: I1-I7 and I9-I12 (I8 not used), variants and I12 sub-checks separately, plus `D:<item>` in product mode. R = sum over FAIL checks of (|residual| - tolerance). I11 REVIEW never triggers.
2. Suspects: changeable values entering a FAIL check; more failing checks first, then lower M1 probability.
3. Actions per suspect: swap to each other row with the item in its top-3 predictions that shows a value; rescale x1,000 or x1/1,000 (one unit correction relative to the row's cell at most); flip the sign.
4. Score = probability x (R - R'): M1's p(item | row) for a swap; p(item | current row) x 0.5 for a rescale or flip.
5. Admissible only if R' < R, no passing check stops passing (I11 included), and in product mode a DISAGREE value then agrees with its tag. The tag is the target, never the value.
6. Stop when no check fails, nothing is admissible, or 5 actions were applied.
7. Settle: a changed value is CORRECTED only if every check it enters passes at the end (and agrees with its tag if it was DISAGREE); otherwise its original reading is kept and it is REVIEW, as are values left in failing checks.

**Rebuild and final check.** The statement set is rebuilt with the CORRECTED edits; 8.7 runs again, so a corrected value that now agrees with its tag is VERIFIED with flag CORRECTED and its original reading (`corrected_from_value`, `corrected_from_row`). The final check (BC22) recomputes I9 and I10 from the identity library on each fiscal year's final values: exactly zero, else INVALID (FAIL) or GAP (NOT_COMPUTABLE); I11 only when computable, REVIEW if non-zero.

**Downstream.** `python -m finagent.statements.report`, `finagent.model.scenarios` and `finagent.workbook.build` run the agent by default; `--no-agent` reproduces Phase 4. The workbook's Verification sheet shows the CORRECTED flag, a "Corrected from" column, the agent's values and the final check per year.

## 2. Results (statements-0006, product mode, as of 2026-11-24)

Run on HEAD cf18d18 with uncommitted changes (#106). The evidence files were hashed before and after (438 files) and are identical.

### Validation: actions per filing (8 companies, 40 filings)

| company | filings | failing checks before | actions | outcome |
|---|---|---|---|---|
| AMZN | FY2021-FY2025 | I5 and D:income_tax_expense, every year | 1 per year: flip_sign income_tax_expense | CORRECTED all 5 years: FY2025 -19,087M -> +19,087M; FY2022 +3,217M -> -3,217M (a tax benefit year, matching the tag). Stop NO_FAILING_CHECK. |
| MRK | FY2021-FY2025 | D:ppe_net, every year | 1 per year: swap ppe_net from BS:11 (gross, at cost) to BS:13 (net) | CORRECTED all 5 years: FY2025 47,230M -> 25,316M; FY2021 37,471M -> 19,279M. Stop NO_FAILING_CHECK. |
| T | FY2022 | I10 (-33,128M) | 2: rescale CFI x1/1,000, swap CFO -> CF:15 | Neither resolved I10, so both were reverted. CFO, CFI and CFF REVIEW with original readings kept (#103; the residual is the WarnerMedia discontinued operations, #66). Stop NO_ADMISSIBLE_ACTION. |
| T | FY2021, FY2023-FY2025 | none | none | NO_FAILING_CHECK |
| MSFT, CVX, NKE, CL, ABT | all 25 filings | none | none | NO_FAILING_CHECK |

In total: 11 filings with an action, 12 actions (5 flip_sign, 6 swap, 1 rescale), 10 CORRECTED and 3 REVIEW values. No filing used more than 2 of its 5 actions.

**Wrongly changed values.** A full comparison of statements-0005 and statements-0006 (16,637 observations on each side) finds:
- No value that was VERIFIED before changed.
- All 10 corrected validation values are now VERIFIED/AGREE with their tag.
- The other differences follow from the corrections. Derived lines whose inputs were DISAGREE now exist: AMZN `below_tax_net` in 5 years. AMZN `net_income_including_nci` in FY2022-FY2024 can now be tested by I5. Neither changes a reading.
- No flag other than CORRECTED changed; in particular there is no new RESTATED. Validation I8 failures go from 28 to 36. The new ones are AMZN tax and MRK PP&E in FY2022-FY2025: the next filing's prior-year column still holds the original misread (own-year values only; tag residual 0) (#107).

### Final check (BC22)

- Validation: 38 of 40 fiscal years VERIFIED, and all 8 origin years VERIFIED. I11 is REVIEW (non-blocking) at origin for CVX (35M), T (104M), CL (1M) and ABT (-185M), and not computable for AMZN.
- Two non-origin years are GAP: MRK FY2021 (CFI and CFF missing in both channels) and T FY2022 (CFO, CFI and CFF not accepted) (#104).
- Train, for information only: 124 VERIFIED, 14 GAP and 22 INVALID years. Origin years: 27 VERIFIED, 3 GAP, 2 INVALID (CSCO, MCD). Several INVALID years are ±1M rounding in the filer's own tagged totals under the exact-zero rule (#105).

### Train (in-sample for M1; information only)

160 filings. 37 had an action: 32 flip_sign (30 kept, all income tax; 2 reverted at settle) and 5 swaps (cost of revenue, all kept). That gave 35 CORRECTED values, all now VERIFIED/AGREE, and 20 REVIEW values: ORCL pre-tax and tax, CSCO D&A, QCOM receivables, LMT D&A and costs. The stop reason was NO_FAILING_CHECK for 118 filings and NO_ADMISSIBLE_ACTION for 42.

### Phase 4 re-check after the agent

- **forecast-0004** (`docs/scenarios_v1.md`): all 8 validation companies are VERIFIED in Base, Moderate and Severe, and the final check is VERIFIED at each origin year. Phase 5 exit **met**, with no BC16 exclusion. AMZN and MRK are new. The other six have projections identical to forecast-0003 in every line, scenario and year. Largest F1 residual: $0.000122.
- Train, for information: Base VERIFIED 12 → 16 of 32. CMCSA, CRM, UNP and VZ are now VERIFIED after their tax corrections, and no train company lost VERIFIED.
- **workbooks-0004** (`docs/workbook_v1.md`): 8 of 8 VERIFIED. The independent evaluator and the LibreOffice recalculation both show 0 mismatches for 8 companies × 3 scenarios. The AMZN and MRK workbooks are now full models (605 and 587 formula cells).

<!-- notes: text below this line is kept when the report is regenerated -->
