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

---

# v2: unit-line rules (#42, BC12) and concept map (#43)

Runs `e1-0003` (M0) and `e1-0004` (M1 v2) in `eval/experiments.csv`; field results in
`runs/e1/m{0,1}_v2_validation.jsonl` (gitignored).

| Field | Value |
|---|---|
| Split | validation, same 8 companies and 40 filings. Held-out not run. |
| Dataset | `rows_v2` (meta SHA-256 `6f6332faadbf`), as_of 2026-11-24; same 17,112 rows as v1 |
| Code | commit `d2b2f24` with uncommitted v2 changes (recorded as `d2b2f24-dirty`) |
| Evidence manifest | `89ee416192001fdea1d563d721ed612ef1e2a373e0f145bfb2528358b0b99ab0` (unchanged) |
| M0 | `m0-rules`, unchanged (`configs/label_synonyms.yaml`) |
| M1 | `m1-tfidf-lr-v2`: train split only, C = 64, T = 1.300; model card `docs/model_cards/m1_v2.md` |
| Commands | `python -m finagent.dataset.build --version v2 --as-of 2026-11-24`; `python -m finagent.training.m1 --version v2`; `python eval/e1_document.py --model m0 --dataset-version v2 --record ...`; `python eval/e1_document.py --model m1 --dataset-version v2 --compare m0 --record ...` |

**What changed (single change per dataset version: the row builder's unit line and the concept
map; the classifier recipe is unchanged).**

1. Unit line (issue #42). The row builder now searches, in order: the column-header cells
   (a bracketed unit line anywhere, or an unbracketed one opening a short cell: "Millions of
   Dollars", "in millions, except per share data", "(MILLIONS, ...)"); the statement's title
   block (short text blocks and figure-less tables just before the table: "AT&T Inc. | Dollars
   in millions except per share amounts", "Millions of dollars, except per-share amounts"); the
   nearest bracketed unit line in the text before the table; and, only for a table with none of
   these, the filing-wide sentence at or after Item 8 (BC12, Deere FY2023–25). A continuation
   table without a unit takes its statement's. No unit found stays a gap.
2. Concept map (issue #43): finance-lease-inclusive PP&E -> `ppe_net`,
   `LongTermDebtAndCapitalLeaseObligations` -> `long_term_debt` (approved by Vineet). 105 rows
   OTHER -> item, 0 AMBIGUOUS. Tag channel: 107 item-filings GAP -> VERIFIED; 3 new comparable
   CONFLICTs (UPS FY2021–22 `long_term_debt`, COP FY2021 `ppe_net`) plus 3 IBM (not comparable).

**Optimistic, and more so than v1.** Pattern B (title-block tables and text blocks) was
identified on validation companies (CVX, T); no train filer needed it, so it was not derived on
train first. Together with C and T being chosen on validation, the v2 validation numbers are
optimistic. The held-out run in Phase 8 is the unbiased measure.

## Unit-line coverage (share of rows with a unit line)

| split | company | rows | v1 | v2 |
|---|---|---|---|---|
| train | CAT | 508 | 0.593 (CF none) | 1.000 |
| train | COP | 359 | 0.000 | 1.000 |
| train | DE | 471 | 0.397 (FY2023–25 none) | 1.000 (FY2023–25 via BC12) |
| train | HD | 404 | 0.000 | 1.000 |
| train | MCD | 459 | 0.000 | 1.000 |
| train | PFE | 531 | 0.000 | 1.000 |
| train | PG | 392 | 0.000 | 1.000 |
| train | TGT | 373 | 0.000 | 1.000 |
| train | UNP | 376 | 0.000 | 1.000 |
| train | other 22 companies | 9,764 | 1.000 | 1.000 |
| validation | CVX | 456 | 0.000 | 1.000 |
| validation | T | 546 | 0.000 | 1.000 |
| validation | ABT, AMZN, CL, MRK, MSFT, NKE | 2,473 | 1.000 | 1.000 |
| all | | 17,112 | 0.744 (4,387 rows none) | 1.000 |

Every unit line in v2 reads as millions. No row that had a unit in v1 changed scale. 4,736 rows
changed `unit_text` without changing scale: v1 took the first bracketed line in the 600
characters before a table, often the previous statement's title; v2 takes the nearest one, the
statement's own (issue #46).

## Headline, v1 vs v2

| Metric | M0 v1 | M0 v2 | M1 v1 | M1 v2 |
|---|---|---|---|---|
| E1 field accuracy | 0.626 | **0.770** | 0.680 | **0.889** |
| 95% CI, bootstrap over companies (2,000 draws) | [0.349, 0.854] | [0.654, 0.876] | [0.399, 0.900] | [0.838, 0.939] |
| Fields scored | 941 | 961 | 941 | 961 |
| Coverage (field has a document value) | 0.633 | 0.788 | 0.691 | 0.899 |
| Errors: MISSED / SIGN / WRONG_ROW | 345 / 5 / 2 | 204 / 5 / 12 | 291 / 5 / 5 | 97 / 5 / 5 |
| Row accuracy (3,475 rows, OTHER included) | 0.914 | 0.914 | 0.916 | 0.922 |
| Row macro-F1 (33 items, OTHER excluded) | 0.785 | 0.787 | 0.822 | 0.834 |

**McNemar (exact, paired on the same 961 v2 fields):** 28 fields right only under M0, 142 right
only under M1, p = 1.5 × 10⁻¹⁹. M1 stays better than M0.

The 20 extra fields are items now "on face" by label (BC8): `ppe_net` +15 (AMZN, CVX, T) and
`long_term_debt` +5 (T). NO_UNIT_LINE is gone (0 fields, was 151 under M0 and 188 under M1).
M0's WRONG_ROW rises from 2 to 12 because CVX and T are now readable: M0 reads their
consolidated net income (including NCI) where the tag is the parent figure (10 fields), the same
pattern as MRK.

Against the blueprint's starting targets (M0 ≥ 80%, best model ≥ 95% held-out): M0 is below 80%
on validation; M1 at 0.889 is below 95%, and its validation number is optimistic.

Field accuracy by company:

| company | fields v1 / v2 | M0 v1 | M0 v2 | M1 v1 | M1 v2 |
|---|---|---|---|---|---|
| ABT | 101 / 101 | 0.495 | 0.495 | 0.752 | 0.802 |
| AMZN | 125 / 130 | 0.880 | 0.885 | 0.816 | 0.846 |
| CL | 142 / 142 | 0.915 | 0.915 | 1.000 | 1.000 |
| CVX | 117 / 122 | 0.000 | 0.590 | 0.000 | 0.877 |
| MRK | 117 / 117 | 0.658 | 0.658 | 0.821 | 0.778 |
| MSFT | 130 / 130 | 1.000 | 1.000 | 0.938 | 0.962 |
| NKE | 112 / 112 | 0.821 | 0.821 | 0.911 | 0.911 |
| T | 97 / 107 | 0.000 | 0.692 | 0.000 | 0.897 |

MRK M1 falls 0.821 -> 0.778: its IS "Sales" row (no parent header) moves from P(revenue) 0.79 under
v1 to 0.41 under v2, so all 5 revenue fields are MISSED. No revenue label changed; the shift comes from
retraining on the 85 relabelled train rows, so this borderline row is fragile.

## Field accuracy per item, v1 vs v2 (correct of scored)

| item | M0 v1 | M0 v2 | M1 v1 | M1 v2 |
|---|---|---|---|---|
| accounts_payable | 25/30 | 30/30 | 25/30 | 30/30 |
| accounts_receivable | 20/30 | 25/30 | 25/30 | 30/30 |
| capital_expenditure | 30/40 | 40/40 | 27/40 | 37/40 |
| cash_and_cash_equivalents | 25/32 | 32/32 | 25/32 | 32/32 |
| cost_of_revenue | 25/35 | 25/35 | 25/35 | 30/35 |
| depreciation_and_amortisation | 12/22 | 22/22 | 7/22 | 20/22 |
| equity_attributable_to_parent | 15/35 | 15/35 | 25/35 | 30/35 |
| financing_cash_flow | 17/34 | 19/34 | 27/34 | 34/34 |
| gross_profit | 15/15 | 15/15 | 15/15 | 15/15 |
| income_tax_expense | 15/40 | 22/40 | 17/40 | 27/40 |
| interest_expense | 12/17 | 17/17 | 12/17 | 17/17 |
| inventory | 15/25 | 15/25 | 15/25 | 20/25 |
| investing_cash_flow | 17/34 | 24/34 | 27/34 | 34/34 |
| long_term_debt | 30/30 | 35/35 | 30/30 | 35/35 |
| net_change_in_cash | 27/36 | 33/36 | 30/36 | 36/36 |
| net_income | 28/40 | 28/40 | 27/40 | 37/40 |
| net_income_including_nci | 5/18 | 5/18 | 8/18 | 17/18 |
| operating_cash_flow | 22/34 | 29/34 | 12/34 | 19/34 |
| operating_income | 15/25 | 20/25 | 15/25 | 20/25 |
| ppe_net | 15/20 | 20/35 | 12/20 | 30/35 |
| pretax_income | 15/30 | 20/30 | 25/30 | 30/30 |
| pretax_income_before_equity_method | 0/10 | 0/10 | 0/10 | 0/10 |
| research_and_development | 15/15 | 15/15 | 15/15 | 15/15 |
| revenue | 30/35 | 30/35 | 30/35 | 30/35 |
| selling_general_and_administrative | 15/30 | 25/30 | 20/30 | 30/30 |
| short_term_investments | 14/19 | 19/19 | 14/19 | 19/19 |
| total_assets | 20/35 | 30/35 | 20/35 | 30/35 |
| total_costs_and_expenses | 0/20 | 0/20 | 5/20 | 5/20 |
| total_current_assets | 25/35 | 35/35 | 25/35 | 35/35 |
| total_current_liabilities | 30/40 | 40/40 | 30/40 | 40/40 |
| total_equity | 10/25 | 15/25 | 15/25 | 20/25 |
| total_liabilities | 10/15 | 10/15 | 10/15 | 15/15 |
| total_liabilities_and_equity | 20/40 | 30/40 | 25/40 | 35/40 |

## Remaining errors under M1 v2 (107 fields), largest categories

| category | fields | companies | cause |
|---|---|---|---|
| Same wording, different concept by filer (#28) | 25 | AMZN, CVX, T (`total_costs_and_expenses` read as `operating_expenses` or OTHER, 15); AMZN, CVX (`pretax_income_before_equity_method` read as `pretax_income`, 10) | Text cannot separate them; needs structure/magnitude (M2) or the Correlation Agent's identities. |
| Operating cash flow total called OTHER | 15 | ABT, MSFT, NKE | Unusual wording ("Net cash from operations"; label repeats header). |
| Other rows missed (one company each) | 43 | income tax (ABT 5, MRK 3), operating income (ABT 5, read as `net_income`), revenue (MRK 5), total assets (MRK 5), inventory (MRK 5), cost of revenue (CVX 5), total liabilities and equity (ABT 5), capex (MRK 3), D&A (AMZN 2) | Per-filer wording; candidates for the 9.5 audit and M2 features. |
| Parent vs consolidated | 14 | T total equity (5), NKE parent equity (5), AMZN net income (3), T NCI net income (1) | `total_equity` vs `equity_attributable_to_parent`, `net_income` vs `net_income_including_nci`. |
| WRONG_ROW | 5 | MRK PP&E | Unlabelled gross PP&E row ranked above the net row. |
| SIGN | 5 | AMZN income tax | "(provision)" shown with the opposite sign (#27, Phase 5). |

No SCALE or PERIOD errors under either model; NO_UNIT_LINE is 0.

Rows that M1 correctly reads as the item but whose label is OTHER (false positives, no field
cost): NKE "Inventories" (`InventoryFinishedGoodsNetOfReserves`, left unmapped by decision;
NKE FY2022–26 is the only filer showing it as the face inventory line; PG uses it for a "Finished
goods" sub-row under INVENTORIES); T "Inventories" (`PrepaidExpenseCurrent`, 9.5 label audit);
CVX "Long-term debt" (`LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities`, not
in the approved mapping, issue #47).

## Conclusion (v2)

- **Keep M1 (now `m1-tfidf-lr-v2`) over M0**: 0.889 vs 0.770 field accuracy, McNemar p ≈ 1e-19.
  Recorded as e1-0004.
- The unit-line fix removed the largest error source: CVX and T go from 0 to 0.877 and 0.897
  under M1. The company bootstrap interval narrows from [0.399, 0.900] to [0.838, 0.939].
- The largest remaining source is wording that text alone cannot separate (#28, 25 fields), then
  operating cash flow wording (15). Both point to M2 (structure features) and the Correlation
  Agent (identities), not to more rules.
- v2 validation numbers are optimistic (pattern B found on validation; C and T tuned on it).
