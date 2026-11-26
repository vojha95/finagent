# E6: pre-registration (the validation run)

Written 2026-11-26, before any E6 run on validation, and finalised the same day after the
reviewer's check of the train GAPs (#345, #348). Vineet commits this file before the run, and the
run follows it exactly as written.

**What has and has not been seen:**
- **No validation E6 output exists.** No validation forecast from a past origin has been built,
  and no E6 point, error or metric has been computed for a validation company.
- **The harness was built and debugged on train companies only** (e6-train-0001 to
  e6-train-0003). Those runs report coverage only. No train error, MAE or MAPE was computed, and
  no driver was tuned (section 10).
- **The train GAPs were reviewed before freezing** (#345). None is a harness artifact, so there
  is no harness fix, and the product stays frozen for E6: no product change before the run.
- **Validation facts used here:** the tickers (`configs/universe.yaml`) and each pinned 10-K's
  fiscal year, accession and filing date, which fix the origins. Also that all 8 validation
  forecasts are VERIFIED at the latest year (forecast-0007), used only for the coverage guide in
  section 10.
- **Seen in earlier phases:** the validation statement sets as of 2026-11-24 (statements-0009,
  forecast-0007). They contain the reported FY t+1 values that E6 compares against, but no
  forecast made from a past origin. Nothing in the harness was chosen from them.
- **Held-out companies are never used**, not even their metadata (#308, #331).

## 1. Question, scope and decision

- **Question** (blueprint 10.5; docs/phase9_plan.md section 8; BC7, BC34, BC35): is the shipped
  Base forecast, one year ahead, closer to the outcome than the naive last value?
- **Scope, stated in advance: every result applies only where the shipped product issued a
  VERIFIED forecast at the origin.** Where it gave GAP or INVALID, the origin is excluded and
  counted. Coverage (scored origins / qualifying origins) is printed in the same row as every
  result (section 6). A claim says nothing about the origins the product declined.
- **Decision:** for each metric, either "the model beats naive" or "no accuracy claim" (section
  7). The forecast stays as shipped either way. E6 measures it; it does not change it.

## 2. Companies

The eight companies with split `validation` in `configs/universe.yaml`:

**AMZN, MSFT, CVX, NKE, MRK, T, CL, ABT**

- **Held-out (COST, NFLX, TXN, GIS, FDX, LLY, DHR, MAR): never.** The command offers no held-out
  split, and the tools refuse held-out issuers.
- **Train:** harness debugging only. It is not part of the result.

## 3. Origins

**Rule:**
- The pinned 10-Ks are those visible as of 2026-11-24 (`EvidenceStore.load_filings`: up to
  five consecutive). Each FY t 10-K whose FY t+1 10-K is also pinned gives one origin at its
  filing date d(t).
- **An origin qualifies** when the fiscal years t-2, t-1 and t each have an accepted revenue as
  of d(t). The value may come from the statement set or the BC7 history. An origin that does not
  qualify is excluded and counted (FEWER_THAN_MIN_READABLE_YEARS).
- An origin whose statement set as of d(t) cannot be built cannot be tested, so it does not
  qualify either. It is counted apart (ORIGIN_ERROR, not qualifying).

**The 32 company-origins** (origin FY t, filed d(t) → target FY t+1, filed d(t+1)):

| company | origin 1 | origin 2 | origin 3 | origin 4 |
|---|---|---|---|---|
| AMZN | FY2021 2022-02-04 → FY2022 2023-02-03 | FY2022 2023-02-03 → FY2023 2024-02-02 | FY2023 2024-02-02 → FY2024 2025-02-07 | FY2024 2025-02-07 → FY2025 2026-02-06 |
| MSFT | FY2022 2022-07-28 → FY2023 2023-07-27 | FY2023 2023-07-27 → FY2024 2024-07-30 | FY2024 2024-07-30 → FY2025 2025-07-30 | FY2025 2025-07-30 → FY2026 2026-07-29 |
| CVX | FY2021 2022-02-24 → FY2022 2023-02-23 | FY2022 2023-02-23 → FY2023 2024-02-26 | FY2023 2024-02-26 → FY2024 2025-02-21 | FY2024 2025-02-21 → FY2025 2026-02-24 |
| NKE | FY2022 2022-07-21 → FY2023 2023-07-20 | FY2023 2023-07-20 → FY2024 2024-07-25 | FY2024 2024-07-25 → FY2025 2025-07-17 | FY2025 2025-07-17 → FY2026 2026-07-15 |
| MRK | FY2021 2022-02-25 → FY2022 2023-02-24 | FY2022 2023-02-24 → FY2023 2024-02-26 | FY2023 2024-02-26 → FY2024 2025-02-25 | FY2024 2025-02-25 → FY2025 2026-02-24 |
| T | FY2021 2022-02-16 → FY2022 2023-02-13 | FY2022 2023-02-13 → FY2023 2024-02-23 | FY2023 2024-02-23 → FY2024 2025-02-12 | FY2024 2025-02-12 → FY2025 2026-02-09 |
| CL | FY2021 2022-02-17 → FY2022 2023-02-16 | FY2022 2023-02-16 → FY2023 2024-02-15 | FY2023 2024-02-15 → FY2024 2025-02-13 | FY2024 2025-02-13 → FY2025 2026-02-23 |
| ABT | FY2021 2022-02-18 → FY2022 2023-02-17 | FY2022 2023-02-17 → FY2023 2024-02-16 | FY2023 2024-02-16 → FY2024 2025-02-21 | FY2024 2025-02-21 → FY2025 2026-02-20 |

If the run lists a different set of origins, the run is void (section 9).

## 4. The system, as shipped, and point in time

**At each origin, every read is as of d(t):**
1. **The statement set:** `FinAgentTools.default(dataset_version="v3").statement_set(ticker,
   d(t))`. That is the M1 v3 document channel (`m1-tfidf-lr-v3`), the tag channel and the
   Correlation Agent, over the pinned filings filed by d(t).
2. **The BC7 history** (`finagent/evaluation/e6_history.py`; BC35 (1)) adds the earlier fiscal
   years the drivers read: four years ending at the origin with configs/forecast.yaml.
   - It covers only years before the earliest pinned 10-K of that set.
   - Values come from Company Facts, from the latest 10-K listed as filed by d(t) that tags the
     year. They are SINGLE_CHANNEL (tag), flagged BC7_HISTORY.
   - When a source tags several values, the concept carrying the set's accepted value in its
     pinned years decides (CONCEPT_FROM_PINNED_YEARS, #340).
3. **The forecast:** `build_forecast` as shipped (configs/forecast.yaml, Base scenario). Its
   first projected year (FY t+1) is the model's forecast.

**Naive:** the accepted FY t value of the same metric, from the same set, as of d(t).

**Actual:** the FY t+1 value from the statement set as of d(t+1), FY t+1's own filing date.
- No later filing is visible then, so the value is the one first reported. Restatement
  lookahead is impossible.
- VERIFIED only (both channels agree; a calculation whose inputs are all VERIFIED).

**What is not point in time, stated for completeness.** The shipped models and configs are
used as shipped, at every origin:
- M1 v3 was trained on train companies' filings up to the snapshot. Its temperature was fitted
  on validation (Phase 3).
- The Correlation Agent and the forecast config were built in Phases 4-5.

These choices decide how a table is read and how the forecast is built. None of them carries a
validation company's later reported values into an earlier forecast. The point-in-time rule
covers the data each origin reads.

**Leakage guards** (tested in tests/test_evaluation_e6.py and test_evaluation_e6_history.py).
Either guard stops the run; neither is ever an exclusion.
- Every input the forecast and the naive read must be filed by d(t), or the run raises
  PointInTimeError.
- Every actual must come from FY t+1's own filing, with no RESTATED or FROM_LATER_FILING flag,
  or the run raises RestatedActualError.

## 5. Metrics

| row | naive and actual read from | model line |
|---|---|---|
| revenue | `revenue` | revenue |
| operating_income (pooled) | `ebit_basis`: the reported operating income, else the flagged EBIT proxy (#68) | ebit |
| operating_income:reported | the points whose actual is the reported operating income | ebit |
| operating_income:ebit_proxy | the points whose actual is the EBIT proxy | ebit |
| cfo | `operating_cash_flow` | cfo |
| capex | `capex_basis` (the forecast's own capex) | capex |
| fcf | CFO − capex (VERIFIED only if both are) | cfo − capex |

**Per point (company-origin, metric):**
- the absolute error in USD, AE = |forecast − actual|, for the model and for naive;
- APE = AE / |actual|.

**Per row:**
- **coverage: scored origins / qualifying origins (%), and companies scored / qualifying**
  (section 6), in the same row as every result;
- MAE for the model and for naive, and the MAE ratio (model / naive);
- MAPE for the model and for naive;
- the mean of (naive APE − model APE), with its interval (section 7);
- point wins, losses and ties (model APE <, > or = naive APE);
- company wins and losses (a company's mean APE difference above or below 0).

The MAE ratio is reported because FCF near zero makes MAPE unstable.

**Selection check (descriptive; no claim; BC35 (4)).** Per row, naive APE on two sets of
points:
- **scored:** the scored points with an actual other than 0;
- **GAP-excluded:** the qualifying points excluded for MODEL_GAP alone, with an actual other than
  0. Such a point has what a scored point has: a naive value, a VERIFIED first-reported actual
  and the same basis in both years. Only the forecast was GAP. A point with any other reason as
  well is left out, and so is a MODEL_INVALID point.

For each set: points, companies, mean naive APE and median naive APE. The number of GAP points
the second set comes from is printed beside it. There is no interval, test or claim. The check
shows whether the origins the product declines look harder for naive than those it forecasts, so
a reader can judge how far the result may carry beyond the covered origins. It changes no
decision.

All numbers come from `finagent/evaluation/e6.py` (`metrics`, `row_coverage`,
`selection_check`). Nothing is computed afterwards.

## 6. Exclusions and coverage

Every exclusion is counted and reported with its reason, per row. A point may carry several
reasons. Missing is never zero.

| reason | meaning |
|---|---|
| FEWER_THAN_MIN_READABLE_YEARS | the origin does not qualify (section 3) |
| ORIGIN_ERROR | the statement set as of d(t) could not be built (the origin does not qualify), or the origin qualifies but its forecast could not be built (it stays in the denominator). The error is kept |
| ACTUAL_ERROR | the statement set as of d(t+1) could not be built |
| FISCAL_YEAR_LABEL_MISMATCH | as of d(t+1), the target filing is not labelled t + 1 |
| MODEL_GAP / MODEL_INVALID | the shipped forecast is GAP or INVALID at the origin. Results therefore apply only where the product issued a VERIFIED forecast (section 1) |
| MODEL_LINE_MISSING | the forecast has no line for the metric |
| NAIVE_MISSING | no accepted FY t value as of d(t) |
| ACTUAL_MISSING | no accepted FY t+1 value as of d(t+1) |
| ACTUAL_NOT_VERIFIED | the FY t+1 value is accepted but not VERIFIED (e.g. SINGLE_CHANNEL) |
| BASIS_CHANGED | the basis chosen for FY t differs from FY t+1's (e.g. operating income, then the EBIT proxy), so naive and actual measure different lines (BC35 (3)) |
| ACTUAL_ZERO | the actual is 0. The point counts for MAE but has no APE, so it is left out of MAPE, the difference and its interval |

**Coverage (BC35 (4)), printed in the same row as every result, in every table of
docs/e6_results.md:**
- **coverage = scored origins / qualifying origins (%)**, and companies scored / qualifying;
- **the denominator is the qualifying origins** (section 3), per metric and per
  operating-income group. A group row's denominator is the qualifying origins whose actual's
  basis is that group. An origin with no actual has no basis, so it counts in the pooled row
  only; their number is printed under the results table;
- **primary reason:** each excluded qualifying point also gets one primary reason, the first it
  carries in this fixed order: ORIGIN_ERROR, MODEL_GAP, MODEL_INVALID, MODEL_LINE_MISSING,
  NAIVE_MISSING, ACTUAL_ERROR, FISCAL_YEAR_LABEL_MISMATCH, ACTUAL_MISSING, ACTUAL_NOT_VERIFIED,
  BASIS_CHANGED (the product, then the naive, then the actual, then the basis). The
  primary-reason table adds up to qualifying − scored;
- the all-reasons table (overlapping counts) and the not-qualifying counts are printed as well.

## 7. Decision rule

- **For each row, the model beats naive only if the 95% paired company-bootstrap interval of
  mean(naive APE − model APE) lies entirely above 0.** That means the lower bound must be
  strictly greater than 0.
- **The interval:** percentile method, 2,000 draws, seed 0. Each draw resamples companies with
  replacement, and a drawn company's points move together. This is the E1 convention
  (`company_bootstrap`).
- **Otherwise no accuracy claim is made for that row** (blueprint 10.5), whatever the point
  estimate, MAE ratio or win counts show. A lower bound of exactly 0 is no claim.
- **A row with APE points from fewer than 3 companies** gets no interval and no claim
  (BC35 (5)).
- **Stated in advance: the EBIT-proxy row may well end in "no claim"** because it has fewer than
  3 companies. On train, the EBIT-proxy group had 12 qualifying origins at 4 companies, and none
  was scored. Among the train companies whose latest-year forecast is VERIFIED, as all 8
  validation companies' are, it had 3 origins at 1 company (section 10). Such an outcome follows
  from the rule; it is not a failure of the run.
- **A claim holds only where the product issued a VERIFIED forecast** (section 1). It is read
  with its coverage, printed in the same row.
- **Win counts, coverage, the selection check and every other number are reported, whatever
  they show.** The selection check decides nothing.
- The operating-income subsets (reported, EBIT proxy) follow the same rule. With seven rows, a
  claim on one row is read alongside the others; nothing is pooled across metrics.

## 8. Commit, inputs and command

- **Commit:** the commit that adds this file to `main`, called C here.
  - **The run is made at C with a clean working tree, before any other change to the
    repository.** The UI work comes after the run. The command refuses a dirty tree, except for
    eval/experiments.csv, the record step's own file.
  - The header must show `git_commit` = C (short form) and `git_dirty` false.
- **as_of:** 2026-11-24 (the run's as_of, which picks the pinned filings); dataset_version v3.
- **Pinned files.** The header records each sha256 (`HASHED` in eval/e6_backtest.py), and the run
  is void if any differs. The E6 code is listed too; the rest of the code is pinned by C.

| file | sha256 |
|---|---|
| `configs/e6.yaml` | `0f10cce1bafe142c24620f497966b18fb096d58f9417048246fdfb86bdf2a3b1` |
| `configs/forecast.yaml` | `f913c609c9d095a50e45a30f6734bfc3d3b40128ebca7af44a94248e2a64c178` |
| `configs/scenarios.yaml` | `bc300453d4bc3a00daee46e70580d9e147d60336af226d321fe5596d51bda45b` |
| `configs/derivations.yaml` | `a481c166d686334e0ecbac9f4396d7607286705aeefd3b6e14f1eb427a12777c` |
| `configs/concept_map.yaml` | `9fa20e4104618b274d682233e433af5cec3c70dda3fbd479b317993f43b1e656` |
| `configs/model_inputs.yaml` | `3911c845c971c82152512ef8b59314ab480883aaaf349dadf6f8e20207c721e3` |
| `configs/identities.yaml` | `c4796432622b14011bdaf265521430dd760addeb8f4a8f6e485f78943595be09` |
| `configs/correlation_agent.yaml` | `e6f56fe961bf0c53e1075930eead741cfbbcbdff8368f46f03da6e8d1624e550` |
| `models/m1_v3.joblib` | `88da60a9dda239ddb74f6bb52e7610782271ca34c5c0b713bb322b408b8a4f63` |
| `models/m1_v3.meta.json` | `1583782725dafb0ba76246715f77907522bed78ce464831b3ef68fab026524f1` |
| `evidence/manifest.json` | `89ee416192001fdea1d563d721ed612ef1e2a373e0f145bfb2528358b0b99ab0` |
| `finagent/evaluation/e6.py` | `2e19724396a0c5636fc45a9c5b8c643cbfcba9ee869db95dc61ce372b3f16570` |
| `finagent/evaluation/e6_history.py` | `69187209de042e9b8f97fab3dd4f03f690f6da5c308a6f7d53adc24d9cd02214` |
| `eval/e6_backtest.py` | `0a19d15d24a12469cab6ed5d70db871edaa2c72793ccdb5ec3ff4e13d80b5446` |

- **The command:** run once, at C.

```
python eval/e6_backtest.py run --split validation --run-id e6-0001 --as-of 2026-11-24
```

- **It refuses:**
  - an existing run directory;
  - an earlier E6 validation run (any `runs/*/header.json` with eval_id E6 and split validation);
  - a docs/e6_prereg.md that is not committed, or has changed;
  - a dirty tree.
- **It writes:**
  - `runs/e6-0001/`: header.json, origins.jsonl, points.jsonl, coverage.json, metrics.json;
  - `docs/e6_results.md`: every row, whatever it shows. Coverage sits in the same row as every
    result in every table. The file also holds the primary-reason table, the all-reasons table
    and the selection check.
- After the result is seen, the row is recorded (the coverage fields go into it too):

```
python eval/e6_backtest.py record --run-id e6-0001 \
    --single-change "E6 validation run, pre-registered (docs/e6_prereg.md): shipped Base forecast vs naive last value" \
    --conclusion "reported in docs/e6_results.md, by the pre-registered rule"
```

## 9. Once, reported whatever it shows, nothing tuned afterwards

- **The run happens once.** Its results go into docs/e6_results.md and one experiments.csv
  row, whatever they show. That includes the model losing to naive on every metric, low
  coverage, and few points surviving the exclusions.
- **Nothing is tuned afterwards** because of E6 results: drivers, caps, bounds, identity
  tolerances, the forecast config, the BC7 history rule, the exclusions and this rule. A later
  change needs its own single change, chosen without E6 validation results. A new E6 score for
  it needs a new, pre-registered run. That includes the two product findings logged before the
  run (#346, #347).
- **The run is void if** the origins differ from section 3, any pinned sha256 differs from
  section 8, or the header shows a commit other than C or a dirty tree.
- **Technical failure:**
  - The command writes nothing until every company has run. If it crashes before writing, it
    may be repeated at the same commit with no change.
  - A leakage guard firing (section 4) is a technical failure. So is any code change being
    needed. In either case the run stops, the problem is logged, and Vineet decides. Any retry
    follows an addendum to this file, committed first.
  - A run that wrote results is never repeated.

## 10. Train sanity check (harness debugging; not a result)

The harness ran on the 32 train companies: `python eval/e6_backtest.py run --split train
--run-id e6-train-0003` (14 min). This is a check that the harness works, **not a result**.
No error, MAE or MAPE was computed on train, and nothing was tuned. e6-train-0003 repeats
e6-train-0002 with the coverage fields (#348). Its origins and its 640 points are identical,
apart from the new `qualifying` field.

**One harness fault was found and fixed** (e6-train-0001 → e6-train-0002, #340):
- Where Company Facts tags two revenue values for a year (total revenues and net sales), the
  history had no way to choose. Four origins of two companies lost their history: three no
  longer qualified, and one was GAP.
- The fix is general: keep the concept that carries the set's own accepted value
  (CONCEPT_FROM_PINNED_YEARS).

**Origins:**
- 128 company-origins; 122 qualify.
- **Not qualifying (6):**
  - 5 have fewer than 3 readable years: IBM (4; statements incorporated by reference, BC10)
    and COP FY2021 (a document with no tables, #31).
  - 1 ORIGIN_ERROR before the test: HON FY2022 (#341).
- **Forecast at the qualifying origins:** 47 VERIFIED, 75 GAP, 0 INVALID.
- **BC7 history:** used at 95 origins, giving 282 forecast inputs.
- **Point in time:** at every origin, the latest filing date of any input is on or before d(t).

**The 75 train GAPs, reviewed before freezing (#345):**
- **48** come from the rule that also makes the company's latest-year shipped forecast GAP
  (forecast-0007).
- **26** come from a rule specific to that year's filing. The shipped statement set as of
  2026-11-24, cut back to FY t, gives GAP too, so the product would decline those years as well.
- **1** differs only by a comparative filed a year after d(t), which is not usable point in time.
- **None is a harness artifact,** so there is no harness fix. The product stays frozen for E6.
- No GAP rests on the BC7 history: no GAP reason names a year before t-2. The GAP share does not
  fall at later origins (60%, 57%, 61%, 68% at positions 1-4).
- Two product findings are logged for after the run: a misread closing cash (#346), and ±$1m
  I9/I10 residuals under BC22's exact-zero rule (#347).

**Coverage per row** (e6-train-0003 coverage.json). Each pooled row also counts the 6
non-qualifying origins apart (FEWER_THAN_MIN_READABLE_YEARS 5, ORIGIN_ERROR 1):

| row | coverage: scored / qualifying | companies: scored / qualifying | primary reasons (add up to qualifying − scored) | all reasons at qualifying origins (overlapping) |
|---|---|---|---|---|
| revenue | 47 / 122 (39%) | 14 / 31 | MODEL_GAP 75 | MODEL_GAP 75, ACTUAL_ERROR 1 |
| operating_income (pooled) | 47 / 122 (39%) | 14 / 31 | MODEL_GAP 75 | MODEL_GAP 75, ACTUAL_NOT_VERIFIED 12, BASIS_CHANGED 2, ACTUAL_ERROR 1 |
| operating_income:reported | 47 / 109 (43%) | 14 / 28 | MODEL_GAP 62 | MODEL_GAP 62, ACTUAL_NOT_VERIFIED 5, BASIS_CHANGED 1 |
| operating_income:ebit_proxy | 0 / 12 (0%) | 0 / 4 | MODEL_GAP 12 | MODEL_GAP 12, ACTUAL_NOT_VERIFIED 7, BASIS_CHANGED 1 |
| cfo | 47 / 122 (39%) | 14 / 31 | MODEL_GAP 75 | MODEL_GAP 75, ACTUAL_NOT_VERIFIED 3, ACTUAL_ERROR 1 |
| capex | 40 / 122 (33%) | 12 / 31 | MODEL_GAP 75, ACTUAL_NOT_VERIFIED 7 | MODEL_GAP 75, ACTUAL_NOT_VERIFIED 15, ACTUAL_ERROR 1 |
| fcf | 40 / 122 (33%) | 12 / 31 | MODEL_GAP 75, ACTUAL_NOT_VERIFIED 7 | MODEL_GAP 75, ACTUAL_NOT_VERIFIED 15, ACTUAL_ERROR 1 |

One qualifying origin has no actual (ACTUAL_ERROR), so it is in the pooled operating-income row
only (109 + 12 + 1 = 122). No actual was 0. The selection check is not computed on train
(coverage only).

**Expected coverage on validation, from train (a guide, not a target; nothing depends on it):**
- All 8 validation companies' latest-year forecasts are VERIFIED (forecast-0007). On train, the
  16 companies like that (latest-year forecast VERIFIED) give:

  | row | coverage: scored / qualifying | companies |
  |---|---|---|
  | revenue, operating_income (pooled), cfo | 46 / 63 (73%) | 13 / 16 |
  | operating_income:reported | 46 / 60 (77%) | 13 / 15 |
  | operating_income:ebit_proxy | 0 / 3 (0%) | 0 / 1 |
  | capex, fcf | 39 / 63 (62%) | 11 / 16 |

- The other 15 train companies with a qualifying origin, whose latest-year forecast is GAP, give
  1 / 59 (IBM, also GAP at the latest year, has no qualifying origin).
- At the first rate, about 23 of the 32 validation origins would be scored for revenue, and
  about 20 for capex and FCF. With 8 companies the spread is wide, and an origin's GAP depends
  on that year's filing (#345), so the run may differ either way.
