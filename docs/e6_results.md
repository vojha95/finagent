# E6 results: e6-0001 (validation, pre-registered)

Run 2026-11-26 at commit 698bb67; as_of 2026-11-24; model m1-tfidf-lr-v3; scenario Base. Pre-registration: docs/e6_prereg.md (sha256 375ad8695fec). Written by eval/e6_backtest.py; nothing here was chosen after the run.

**Every result here applies only where the shipped product issued a VERIFIED forecast at the origin.** Where it gave GAP or INVALID, the origin is excluded and counted. Coverage = scored origins / qualifying origins, per row, in the same row as each result.

Origins: 32 (32 qualifying; 0 with an error). Forecast status: {'GAP': 4, 'VERIFIED': 28}.

MAE in USD millions. APE difference = naive APE − model APE (positive: the model is closer). Claim: the model beats naive only if the 95% paired company-bootstrap interval lies entirely above 0.

| row | coverage: scored / qualifying (%) | companies: scored / qualifying | MAE model | MAE naive | MAE ratio | MAPE model | MAPE naive | APE difference [95% CI] | wins / losses / ties | company wins / losses | claim |
|---|---|---|---|---|---|---|---|---|---|---|---|
| revenue | 28 / 32 (88%) | 8 / 8 | 19,608 | 20,912 | 0.938 | 0.108 | 0.088 | -0.019 [-0.072, +0.027] | 12 / 16 / 0 | 3 / 5 | no accuracy claim |
| operating_income | 15 / 32 (47%) | 4 / 8 | 8,744 | 12,830 | 0.682 | 0.704 | 0.679 | -0.025 [-0.111, +0.058] | 9 / 6 / 0 | 1 / 3 | no accuracy claim |
| operating_income:reported | 15 / 20 (75%) | 4 / 5 | 8,744 | 12,830 | 0.682 | 0.704 | 0.679 | -0.025 [-0.111, +0.058] | 9 / 6 / 0 | 1 / 3 | no accuracy claim |
| operating_income:ebit_proxy | 0 / 12 (0%) | 0 / 3 | — | — | — | — | — | — | 0 / 0 / 0 | 0 / 0 | no accuracy claim |
| cfo | 14 / 32 (44%) | 5 / 8 | 15,913 | 10,220 | 1.557 | 0.312 | 0.212 | -0.100 [-0.166, -0.012] | 4 / 10 / 0 | 1 / 4 | no accuracy claim |
| capex | 28 / 32 (88%) | 8 / 8 | 6,838 | 7,237 | 0.945 | 0.217 | 0.207 | -0.009 [-0.040, +0.034] | 13 / 15 / 0 | 2 / 6 | no accuracy claim |
| fcf | 14 / 32 (44%) | 5 / 8 | 12,132 | 9,078 | 1.336 | 0.554 | 0.589 | +0.035 [-0.241, +0.310] | 5 / 9 / 0 | 2 / 3 | no accuracy claim |

## Exclusions

**Primary reason**, one per excluded qualifying point, the first it carries in this fixed order: ORIGIN_ERROR, MODEL_GAP, MODEL_INVALID, MODEL_LINE_MISSING, NAIVE_MISSING, ACTUAL_ERROR, FISCAL_YEAR_LABEL_MISMATCH, ACTUAL_MISSING, ACTUAL_NOT_VERIFIED, BASIS_CHANGED. It adds up to qualifying − scored.

| row | coverage: scored / qualifying (%) | qualifying − scored | primary reasons | not qualifying |
|---|---|---|---|---|
| revenue | 28 / 32 (88%) | 4 | MODEL_GAP 4 | none |
| operating_income | 15 / 32 (47%) | 17 | MODEL_GAP 4, ACTUAL_NOT_VERIFIED 13 | none |
| operating_income:reported | 15 / 20 (75%) | 5 | MODEL_GAP 1, ACTUAL_NOT_VERIFIED 4 | none |
| operating_income:ebit_proxy | 0 / 12 (0%) | 12 | MODEL_GAP 3, ACTUAL_NOT_VERIFIED 9 | none |
| cfo | 14 / 32 (44%) | 18 | MODEL_GAP 4, ACTUAL_MISSING 1, ACTUAL_NOT_VERIFIED 13 | none |
| capex | 28 / 32 (88%) | 4 | MODEL_GAP 4 | none |
| fcf | 14 / 32 (44%) | 18 | MODEL_GAP 4, ACTUAL_MISSING 1, ACTUAL_NOT_VERIFIED 13 | none |

**All reasons** (a point may carry several).

| row | coverage: scored / qualifying (%) | excluded points | reasons | actual 0 (no APE) |
|---|---|---|---|---|
| revenue | 28 / 32 (88%) | 4 | MODEL_GAP 4 | 0 |
| operating_income | 15 / 32 (47%) | 17 | ACTUAL_NOT_VERIFIED 16, MODEL_GAP 4 | 0 |
| operating_income:reported | 15 / 20 (75%) | 5 | ACTUAL_NOT_VERIFIED 4, MODEL_GAP 1 | 0 |
| operating_income:ebit_proxy | 0 / 12 (0%) | 12 | ACTUAL_NOT_VERIFIED 12, MODEL_GAP 3 | 0 |
| cfo | 14 / 32 (44%) | 18 | ACTUAL_MISSING 3, ACTUAL_NOT_VERIFIED 14, MODEL_GAP 4, NAIVE_MISSING 4 | 0 |
| capex | 28 / 32 (88%) | 4 | ACTUAL_NOT_VERIFIED 2, MODEL_GAP 4 | 0 |
| fcf | 14 / 32 (44%) | 18 | ACTUAL_MISSING 3, ACTUAL_NOT_VERIFIED 14, MODEL_GAP 4, NAIVE_MISSING 4 | 0 |

## Selection check (descriptive; no claim)

Naive APE where the product forecast (the scored points) and where it gave GAP (points excluded for MODEL_GAP alone: a naive value, a VERIFIED first-reported actual and the same basis). It shows whether the origins the product declines look harder for naive than those it forecasts. It decides nothing.

| row | coverage: scored / qualifying (%) | scored: points (companies) | scored: mean / median naive APE | GAP-excluded: points (companies), of GAP points | GAP-excluded: mean / median naive APE |
|---|---|---|---|---|---|
| revenue | 28 / 32 (88%) | 28 (8) | 0.088 / 0.070 | 4 (2), of 4 | 0.067 / 0.039 |
| operating_income | 15 / 32 (47%) | 15 (4) | 0.679 / 0.212 | 1 (1), of 4 | 1.196 / 1.196 |
| operating_income:reported | 15 / 20 (75%) | 15 (4) | 0.679 / 0.212 | 1 (1), of 1 | 1.196 / 1.196 |
| operating_income:ebit_proxy | 0 / 12 (0%) | 0 (0) | — / — | 0 (0), of 3 | — / — |
| cfo | 14 / 32 (44%) | 14 (5) | 0.212 / 0.218 | 0 (0), of 4 | — / — |
| capex | 28 / 32 (88%) | 28 (8) | 0.207 / 0.189 | 2 (2), of 4 | 0.122 / 0.122 |
| fcf | 14 / 32 (44%) | 14 (5) | 0.589 / 0.351 | 0 (0), of 4 | — / — |
