# Held-out E1: results (e1-0009, e1-0010)

Run once on 2026-11-25, following docs/heldout_e1_prereg.md as written. Nothing was tuned
afterwards, and no held-out error was opened.

## 1. Provenance

- **Commit C:** 089962e, the commit that adds the pre-registration. The tree was clean before
  the run, with nothing untracked.
- **Model hashes:** checked before the run against section 3. They match:
  - M1 v3 joblib `88da60a9…4f63` and M3 safetensors `c0b0f564…ccd0`;
  - both meta files;
  - `datasets/rows_v3.meta.json` (`ebb3f32d…`);
  - the bge-small revision pin (`5c38ec7c…`).
- **Commands:** the two in section 4, word for word, back to back in one shell, M3 only if M1
  succeeded. M1 v3 ran 23:39:56-23:40:43 IST, M3 23:40:43-23:41:45. Both exited 0.

| | M1 v3 | M3 |
|---|---|---|
| Run id | e1-0009 | e1-0010 |
| model_version | m1-tfidf-lr-v3 | m3-bge-small-ft-v3 |
| model_sha256 (header) | 88da60a9dda239ddb74f6bb52e7610782271ca34c5c0b713bb322b408b8a4f63 | c0b0f564224ccd198d66f841af9dd7f6220b192e5a22e8e33276906f3a6cccd0 |
| git_commit / git_dirty | 089962e / false | 089962e / false |
| evidence_manifest_sha256 | 89ee4161…9ab0 | 89ee4161…9ab0 |
| as_of / dataset_version | 2026-11-24 / v3 | 2026-11-24 / v3 |
| rows_source | run | run |
| Results file | runs/e1/m1_v3_held_out.jsonl | runs/e1/m3_v3_held_out.jsonl |

Every value matches the pre-registration, so the run is valid.

## 2. Primary metrics

| Measure | M1 v3 | M3 |
|---|---|---|
| Field accuracy [95% company bootstrap] | 0.913 [0.846, 0.961] | **0.941** [0.887, 0.985] |
| Fields | 869 | 869 (the same keys) |

**Paired difference M3 − M1 v3: +0.029, 95% paired company bootstrap interval [0.000, +0.055].**
The lower bound is stored as exactly `0.0`; the upper bound is 0.0554. The bootstrap used
2,000 draws, seed 0, percentile method.

## 3. Wiring rule (pre-registration, section 6)

> **M3 replaces M1 v3 in the product only if the paired 95% company-bootstrap interval of
> M3 − M1 v3 on held-out lies entirely above zero.** That means its lower bound must be strictly
> greater than 0.
> Otherwise M1 v3 stays, whatever the point estimate, McNemar p, macro-F1 or coverage. A lower
> bound of exactly 0 also keeps M1 v3.

**Outcome: the lower bound is exactly 0, not strictly above 0. M1 v3 stays the document
channel's classifier, with its shipped temperature. M3 is not wired in.** Nothing was wired in
or changed in this step.

## 4. Secondary metrics (reported; they do not decide)

| Measure | M1 v3 | M3 |
|---|---|---|
| Coverage (field found) | 0.945 | 0.964 |
| Errors | MISSED 48, WRONG_ROW 18, SIGN 10, SCALE 0, PERIOD 0 | MISSED 31, SIGN 15, WRONG_ROW 5, SCALE 0, PERIOD 0 |
| Field accuracy, BC13 off (strict) [95% company bootstrap] | 0.900 [0.826, 0.954] | 0.937 [0.883, 0.979] |
| Strict errors | MISSED 59, WRONG_ROW 18, SIGN 10 | MISSED 35, SIGN 15, WRONG_ROW 5 |
| Fields decided by a BC13 equivalence | 11 | 4 |
| Row accuracy (OTHER included) | 0.942 | 0.972 |
| Row macro-F1 (34 items with held-out rows, OTHER excluded) | 0.843 | 0.904 |
| Rows (rows_source "run") | 2,969 | 2,969 |

**Exact McNemar (secondary):** 9 fields right only under M1 v3 and 34 right only under M3,
two-sided p = 0.00017. A company's row pattern repeats across its years, so these fields are
not independent. The company bootstrap in section 2 gives the honest interval.

**NCI status (the same for both models):**
- equity_attributable_to_parent | total_equity: ABSENT 24, REPORTED 15, ZERO 1;
- net_income | net_income_including_nci: ABSENT 32, REPORTED 6, ZERO 2.

## 5. Per company, and the 95% target

These figures are grouped by issuer from the two saved results files, using
`finagent.evaluation.e1.read_results`. Only aggregates were computed; no error row was opened.

| Company | Fields | Fiscal years | M1 v3 | M3 | Right only under M1 v3 | Right only under M3 |
|---|---|---|---|---|---|---|
| COST | 138 | 5 | 0.928 | **1.000** | 0 | 10 |
| DHR | 130 | 5 | 0.862 | **0.908** | 2 | 8 |
| FDX | 93 | 5 | 0.946 | **1.000** | 0 | 5 |
| GIS | 27 | **1** | 0.889 | **0.926** | 1 | 2 |
| LLY | 123 | 5 | **0.951** | 0.919 | 4 | 0 |
| MAR | 100 | 5 | 0.710 | **0.780** | 2 | 9 |
| NFLX | 123 | 5 | 0.959 | 0.959 | 0 | 0 |
| TXN | 135 | 5 | 1.000 | 1.000 | 0 | 0 |
| **All** | **869** | 36 | **0.913** | **0.941** | **9** | **34** |

- **Blueprint 10.1 starting target (95% or more on held-out): not met.** The best model, M3, has
  0.941, and M1 v3 has 0.913.
- At 95% or more: M3 at 4 of 8 companies (COST, FDX, NFLX, TXN), M1 v3 at 3 (LLY, NFLX, TXN).
- M3 is better at 6 companies and level at 2 (NFLX, TXN). M1 v3 is better at LLY, which is the
  only company where every differing field favours it.

## 6. Observations (reported, not investigated)

1. **GIS was scored on one fiscal year, 27 fields.** The other seven companies were scored on
   five years each.
   - All 40 pinned 10-Ks were read: the NCI status counts sum to 40 filings per pair.
   - The cause was not investigated. Finding it would mean opening held-out results or filings
     (blueprint 9.2; pre-registration section 7).
   - The results stand as written: an invocation that wrote results is never repeated.
   - GIS carries 27 of the 869 pooled fields and is one of the eight companies the bootstrap
     resamples. Open for Vineet (#309).
2. **Why the lower bound can be exactly zero.** A draw's difference is a count of fields divided
   by the draw's fields. It is exactly 0 when the fields right only under M3 balance the fields
   right only under M1 v3. One example is a draw made only of NFLX and TXN, where the two models
   agree on every field. Draws weighted towards LLY fall below 0. The 2.5th percentile, the 51st
   of 2,000 sorted draws, is one of these exact zeros.
3. **M3 makes more SIGN errors than M1 v3** (15 against 10), while it makes fewer MISSED (31
   against 48) and fewer WRONG_ROW (5 against 18) errors. This is reported only; the errors were
   not opened.

## 7. Held-out against validation

| Measure | Validation (docs/phase8_v1.md section 9) | Held-out (this run) |
|---|---|---|
| M1 v3 field accuracy | 0.905 [0.859, 0.950] | 0.913 [0.846, 0.961] |
| M3 field accuracy | 0.929 [0.893, 0.966] | 0.941 [0.887, 0.985] |
| M3 − M1 v3, paired | +0.024 [-0.004, +0.056] | +0.029 [0.000, +0.055] |
| McNemar (only M1 v3 / only M3, p) | 16 / 39, 0.0027 | 9 / 34, 0.00017 |
| Row macro-F1, M1 v3 / M3 | 0.845 / 0.877 (33 items) | 0.843 / 0.904 (34 items) |

On both splits M3 has the higher point estimate. On neither split does the paired interval lie
strictly above zero.

## 8. What follows

- M1 v3 stays the document channel's classifier with its shipped temperature. M3 and its Platt
  calibrator stay unwired.
- No code, config or model was changed, so the default path was not re-run in this step.
- **Pre-registration section 7 applies.** Nothing is tuned because of these results: not the
  models, thresholds, label rules, concept map, configs or the wiring rule. Any later document
  model needs a new version chosen on validation. A held-out score for it needs a new,
  pre-registered run.
