# E1 on validation: M0 and M1 (Phase 3 exit)

Blueprint 10.1, Phase 3 exit criterion "E1 on validation for M0 and M1". Runs `e1-0001` (M0) and
`e1-0002` (M1) in `eval/experiments.csv`; field results in `runs/e1/m{0,1}_v1_validation.jsonl`
(gitignored).

| Field | Value |
|---|---|
| Split | validation: ABT, AMZN, CL, CVX, MRK, MSFT, NKE, T (8 companies, 40 filings). Held-out not run. |
| Dataset | `rows_v1` (meta SHA-256 `e0665e956d7c`), as_of 2026-11-24 |
| Code | commit `2e8bcd7` with uncommitted Phase 3 changes (recorded as `2e8bcd7-dirty`) |
| Evidence manifest | `89ee416192001fdea1d563d721ed612ef1e2a373e0f145bfb2528358b0b99ab0` |
| M0 | `m0-rules`: `configs/label_synonyms.yaml` |
| M1 | `m1-tfidf-lr-v1`: train split only, C = 64 (validation macro-F1), T = 1.448 (validation NLL); model card `docs/model_cards/m1_v1.md` |
| Commands | `python -m finagent.training.m1 --version v1`; `python eval/e1_document.py --model m0 --record ...`; `python eval/e1_document.py --model m1 --compare m0 --record ...` |

**Caveat.** Validation chose C and fitted T for M1, so its validation numbers are optimistic; M0
was not tuned on validation in this phase. The held-out E1 run (Phase 8) is the unbiased number.

## Headline

| Metric | M0 | M1 |
|---|---|---|
| E1 field accuracy (941 fields) | **0.626** | **0.680** |
| 95% CI, bootstrap over companies (2,000 draws) | [0.349, 0.854] | [0.399, 0.900] |
| Coverage (field has a document value) | 0.633 | 0.691 |
| Errors: MISSED / SIGN / WRONG_ROW | 345 / 5 / 2 | 291 / 5 / 5 |
| Row accuracy (3,475 rows, OTHER included) | 0.914 | 0.916 |
| Row macro-F1 (33 items with validation rows, OTHER excluded) | 0.785 | 0.822 |
| OTHER precision / recall / F1 | 0.928 / 0.971 / 0.949 | 0.970 / 0.930 / 0.950 |
| Rows predicted as items with no validation row | operating_expenses 10 | operating_expenses 20 |
| ECE (15 bins) before / after temperature | — (rules give probability 1) | 0.0425 / 0.0238 |

**McNemar (exact, paired on the same 941 fields):** 29 fields right only under M0, 80 right
only under M1, p = 1.1 × 10⁻⁶. M1 is better than M0 on validation. Both are far below the
blueprint's starting targets (M0 ≥ 80%, best model ≥ 95% held-out). The main reason is not the
classifier (next section).

No SCALE or PERIOD errors under either model. LABEL_NOISE is not assigned automatically (it needs
the 9.5 hand audit); the candidates are listed below.

## Why so many MISSED: two companies with no unit line

| reading reason behind MISSED | M0 | M1 |
|---|---|---|
| NO_UNIT_LINE (CVX, T) | 151 | 188 |
| NOT_FOUND (no row predicted as the item) | 194 | 103 |

Every CVX and T row has `unit_text = None` (the row builder found no "(in millions)" line), so
the normaliser refuses to scale any value (`NO_UNIT_LINE`; correct, since it never guesses a
scale). Both companies score 0 under both models, and they drive the width of the company
bootstrap interval. The same gap affects 7 train companies (COP, HD, MCD, PFE, PG, TGT, UNP:
every row; DE about 60%; CAT CF). Not fixed here: the row builder is out of scope for this task.
Issue #42.

Field accuracy by company:

| company | fields | M0 | M1 |
|---|---|---|---|
| ABT | 101 | 0.495 | 0.752 |
| AMZN | 125 | 0.880 | 0.816 |
| CL | 142 | 0.915 | 1.000 |
| CVX | 117 | 0.000 | 0.000 |
| MRK | 117 | 0.658 | 0.821 |
| MSFT | 130 | 1.000 | 0.938 |
| NKE | 112 | 0.821 | 0.911 |
| T | 97 | 0.000 | 0.000 |

Sensitivity only, not a recorded result: without CVX and T (727 fields), M0 is 0.810
[0.653, 0.927] and M1 0.880 [0.809, 0.946].

## Field accuracy per item (correct of scored)

| item | fields | M0 | M1 |
|---|---|---|---|
| accounts_payable | 30 | 25 | 25 |
| accounts_receivable | 30 | 20 | 25 |
| capital_expenditure | 40 | 30 | 27 |
| cash_and_cash_equivalents | 32 | 25 | 25 |
| cost_of_revenue | 35 | 25 | 25 |
| depreciation_and_amortisation | 22 | 12 | 7 |
| equity_attributable_to_parent | 35 | 15 | 25 |
| financing_cash_flow | 34 | 17 | 27 |
| gross_profit | 15 | 15 | 15 |
| income_tax_expense | 40 | 15 | 17 |
| interest_expense | 17 | 12 | 12 |
| inventory | 25 | 15 | 15 |
| investing_cash_flow | 34 | 17 | 27 |
| long_term_debt | 30 | 30 | 30 |
| net_change_in_cash | 36 | 27 | 30 |
| net_income | 40 | 28 | 27 |
| net_income_including_nci | 18 | 5 | 8 |
| operating_cash_flow | 34 | 22 | 12 |
| operating_income | 25 | 15 | 15 |
| ppe_net | 20 | 15 | 12 |
| pretax_income | 30 | 15 | 25 |
| pretax_income_before_equity_method | 10 | 0 | 0 |
| research_and_development | 15 | 15 | 15 |
| revenue | 35 | 30 | 30 |
| selling_general_and_administrative | 30 | 15 | 20 |
| short_term_investments | 19 | 14 | 14 |
| total_assets | 35 | 20 | 20 |
| total_costs_and_expenses | 20 | 0 | 5 |
| total_current_assets | 35 | 25 | 25 |
| total_current_liabilities | 40 | 30 | 30 |
| total_equity | 25 | 10 | 15 |
| total_liabilities | 15 | 10 | 10 |
| total_liabilities_and_equity | 40 | 20 | 25 |

M1 is worse than M0 on operating cash flow (12 vs 22), D&A (7 vs 12), capex (27 vs 30) and
PP&E (12 vs 15); see the failure modes below.

## Row-level F1 per item (validation rows)

| item | support | M0 P | M0 R | M0 F1 | M1 P | M1 R | M1 F1 |
|---|---|---|---|---|---|---|---|
| OTHER | 2477 | 0.928 | 0.971 | 0.949 | 0.970 | 0.930 | 0.950 |
| accounts_payable | 30 | 0.857 | 1.000 | 0.923 | 0.857 | 1.000 | 0.923 |
| accounts_receivable | 35 | 1.000 | 0.714 | 0.833 | 0.897 | 1.000 | 0.946 |
| capital_expenditure | 40 | 1.000 | 1.000 | 1.000 | 1.000 | 0.925 | 0.961 |
| cash_and_cash_equivalents | 32 | 0.914 | 1.000 | 0.955 | 0.914 | 1.000 | 0.955 |
| cost_of_revenue | 35 | 1.000 | 0.714 | 0.833 | 0.658 | 0.714 | 0.685 |
| depreciation_and_amortisation | 32 | 0.815 | 0.688 | 0.746 | 0.828 | 0.750 | 0.787 |
| equity_attributable_to_parent | 35 | 0.750 | 0.429 | 0.545 | 0.750 | 0.857 | 0.800 |
| financing_cash_flow | 34 | 0.864 | 0.559 | 0.679 | 0.919 | 1.000 | 0.958 |
| gross_profit | 15 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| income_tax_expense | 40 | 1.000 | 0.675 | 0.806 | 1.000 | 0.800 | 0.889 |
| interest_expense | 17 | 1.000 | 1.000 | 1.000 | 0.654 | 1.000 | 0.791 |
| inventory | 25 | 0.600 | 0.600 | 0.600 | 0.571 | 0.800 | 0.667 |
| investing_cash_flow | 34 | 0.889 | 0.706 | 0.787 | 0.919 | 1.000 | 0.958 |
| long_term_debt | 30 | 0.857 | 1.000 | 0.923 | 0.750 | 1.000 | 0.857 |
| net_change_in_cash | 36 | 0.805 | 0.917 | 0.857 | 0.837 | 1.000 | 0.911 |
| net_income | 59 | 0.615 | 1.000 | 0.761 | 0.647 | 0.746 | 0.693 |
| net_income_including_nci | 31 | 1.000 | 0.323 | 0.488 | 0.737 | 0.903 | 0.812 |
| operating_cash_flow | 34 | 0.906 | 0.853 | 0.879 | 0.760 | 0.559 | 0.644 |
| operating_income | 25 | 1.000 | 0.800 | 0.889 | 0.769 | 0.800 | 0.784 |
| ppe_net | 20 | 0.750 | 0.750 | 0.750 | 0.362 | 0.850 | 0.507 |
| pretax_income | 30 | 0.800 | 0.667 | 0.727 | 0.750 | 1.000 | 0.857 |
| pretax_income_before_equity_method | 10 | — | 0.000 | 0.000 | — | 0.000 | 0.000 |
| research_and_development | 15 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| revenue | 45 | 1.000 | 0.667 | 0.800 | 0.900 | 1.000 | 0.947 |
| selling_general_and_administrative | 30 | 1.000 | 0.833 | 0.909 | 1.000 | 1.000 | 1.000 |
| short_term_investments | 19 | 0.792 | 1.000 | 0.884 | 0.792 | 1.000 | 0.884 |
| total_assets | 35 | 1.000 | 0.857 | 0.923 | 1.000 | 0.857 | 0.923 |
| total_costs_and_expenses | 20 | — | 0.000 | 0.000 | 1.000 | 0.250 | 0.400 |
| total_current_assets | 35 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| total_current_liabilities | 40 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| total_equity | 25 | 1.000 | 0.600 | 0.750 | 0.800 | 0.800 | 0.800 |
| total_liabilities | 15 | 1.000 | 0.667 | 0.800 | 1.000 | 1.000 | 1.000 |
| total_liabilities_and_equity | 40 | 1.000 | 0.750 | 0.857 | 0.729 | 0.875 | 0.795 |
| operating_expenses | 0 | 0.000 (10 predicted) | — | — | 0.000 (20 predicted) | — | — |
| total_receivables | 0 | — | — | — | — | — | — |

## Confusion pairs that matter (rows, true -> predicted)

| pair | M0 | M1 | note |
|---|---|---|---|
| total_costs_and_expenses -> operating_expenses | 10 | 10 | AMZN and T "Total operating expenses"; the same wording is `operating_expenses` for 6 train filers and `total_costs_and_expenses` for 5 (issue #28). |
| total_costs_and_expenses -> OTHER | 10 | — | M0 has no synonym for the AMZN/T wording outside `operating_expenses`. |
| pretax_income_before_equity_method -> pretax_income | — | 10 | AMZN, CVX "Income before income taxes"; text cannot separate them (issue #28). M0 leaves them OTHER. |
| net_income_including_nci -> net_income | 21 | — | M0's net income patterns take the consolidated line. |
| net_income -> net_income_including_nci | — | 10 | M1 errs the other way (AMZN "Net income (loss)" on IS and CF, ABT CF "Net earnings"). |
| OTHER -> net_income | 16 | 16 | CVX per-share rows under "Net income attributable to ..."; T "Net income attributable to common stock". |
| OTHER -> ppe_net | — | 30 | 15 are AMZN, CVX, T "Property and equipment, net" labelled OTHER because their concept is not in the concept map (issue #43); 5 are CVX PP&E at cost. |
| operating_cash_flow -> OTHER | — | 15 | MSFT "Net cash from operations", NKE "Cash provided (used) by operations" (label equals header), ABT "Net Cash From Operating Activities"; 5 rows each. |
| OTHER -> inventory | 10 | 15 | NKE inventory concept unmapped (#43); T "Inventories" tagged `PrepaidExpenseCurrent` (label noise candidate); CVX "Chemicals". |
| OTHER -> long_term_debt | — | 10 | CVX, T "Long-term debt" tagged `LongTermDebtAndCapitalLeaseObligations`, not in the concept map (#43). |
| equity_attributable_to_parent -> OTHER | 20 | — | M0 synonym gaps; M1 fixes them. |

## Error taxonomy: non-MISSED errors

| type | M0 | M1 | fields |
|---|---|---|---|
| SIGN | 5 | 5 | AMZN income tax, all 5 years: shown as "(provision)" with the opposite sign (issue #27; Correlation Agent sign flip, Phase 5). |
| WRONG_ROW | 2 | 5 | M0: MRK net income FY2024–25 read from a row 16–9 million above the tag (consolidated vs parent). M1: MRK PP&E, all 5 years: the unlabelled gross PP&E row ranked above the net row. |

Label-noise candidates for the 9.5 audit (not counted as LABEL_NOISE automatically): T BS
"Inventories" tagged `PrepaidExpenseCurrent`. Concept-map gaps that label visible item rows OTHER
(issue #43): PP&E including finance-lease ROU assets, `InventoryFinishedGoodsNetOfReserves`,
`LongTermDebtAndCapitalLeaseObligations`.

## Conclusion

- **Keep M1 over M0** (McNemar p ≈ 1e-6; row macro-F1 0.785 → 0.822). Recorded as e1-0002.
- **Investigate before Phase 8:** the missing unit line (#42) costs 214 of 941 validation fields
  under both models and is the largest single error source; the concept-map gaps (#43) mislabel
  correct rows and add noise to M1's training labels. Both need decisions; neither was changed
  here (labels, concept map and row builder are out of scope).
- Items that text cannot separate (#28) need structure or magnitude features (M2) or the
  Correlation Agent's identities.
