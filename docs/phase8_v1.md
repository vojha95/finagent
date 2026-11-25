# Phase 8: M2, calibration and the M3 options (v1)

Blueprint section 14 (Phase 8, second tier: M2, M3, calibration) and 8.4 (model ladder). Dataset
`datasets/rows_v3.parquet` (train 13,637 rows from 31 companies; validation 3,475 rows from 8
companies). **Held-out companies were not run or opened**; E1 on held-out is the Phase 8 exit
run and waits for Vineet. Commit 0b303e8 with uncommitted changes, as_of 2026-11-24.

## 1. M2 (blueprint 8.4 model ladder)

| Part | What |
|---|---|
| Text | TF-IDF word 1-2-grams + character 3-5-grams of `[statement] parent header \| label` (the M1 text), reduced by truncated SVD |
| Structure | indent, total styling (is_total, is_bold), position and position relative to the statement's last row, statement (IS/BS/CF) |
| Magnitude | log10 of the row's displayed figure over the statement's largest; sign; has-figure; per-share and share-count rows exempt (NaN, never 0) |
| Subtotal detector | the row equals the sum of the k rows just above it; or the topmost of them minus the others; or an earlier row plus or minus the row just above it (gross profit, total assets); or the sum of the last m detected subtotals plus the rows after them (net change in cash). Must hold in every amount column shown, within half a displayed unit per figure (`finagent/document/structure.py`) |
| Classifier | LightGBM multiclass, class_weight balanced, learning rate 0.05, min_child_samples 20, deterministic, 4 threads |
| Hyperparameters | SVD components {64, 128, 256} x leaves {15, 31} x trees {100, 200, 400, 800}: **5-fold cross-validation grouped by company within train** (GroupKFold over issuers; each fold fits TF-IDF, SVD and LightGBM on its training companies only). Best out-of-fold macro-F1, ties to the smaller model. **Validation chose nothing.** |
| Chosen | 128 components, 15 leaves, 100 trees (out-of-fold macro-F1 0.797, accuracy 0.926). The grid is flat: all 24 points lie between 0.786 and 0.797 |
| Rule 7 | Only visible cells: labels, layout and the displayed figures of the same statement. No tag, concept or tagged value |

Model card: `docs/model_cards/m2_v3.md`. Code: `finagent/document/classify.py`
(`SvdBoostedTreeClassifier`), `finagent/document/structure.py`, `finagent/training/m2.py`.

## 2. E1 on validation: M2 against M1 v3 (e1-0007)

Both runs at the same commit (0b303e8, dirty), same evidence manifest (89ee41619200), same
as_of. M1 v3 was re-run at this commit for the paired test; its 971 field results are identical
to e1-0006.

| Measure | M1 v3 (e1-0006) | M2 (e1-0007) |
|---|---|---|
| Field accuracy [95% company bootstrap] | **0.905** [0.859, 0.950] | 0.865 [0.772, 0.944] |
| Fields | 971 | 971 |
| Coverage (field found) | 0.916 | 0.875 |
| Errors | MISSED 82, SIGN 5, WRONG_ROW 5 | MISSED 121, SIGN 5, WRONG_ROW 5 |
| Field accuracy, BC13 off (strict) | 0.897 [0.850, 0.944] | 0.853 [0.761, 0.933] |
| Row accuracy (OTHER included) | 0.928 | 0.932 |
| Row macro-F1 (33 items, OTHER excluded) | 0.845 | 0.848 |
| OTHER precision / recall | 0.973 / 0.941 | 0.945 / 0.973 |

**Paired comparison (same 971 fields):** M2 minus M1 v3 = **-0.040**, 95% paired company
bootstrap interval **[-0.092, +0.004]** (companies resampled with both models' fields moving
together, 2,000 draws). Exact McNemar: 77 fields right only under M1 v3, 38 only under M2,
p = 0.00035.

| Company | fields | M1 v3 | M2 |
|---|---|---|---|
| ABT | 106 | 0.811 | 0.604 |
| AMZN | 130 | 0.846 | 0.808 |
| CL | 142 | 1.000 | 1.000 |
| CVX | 122 | 0.918 | 0.877 |
| MRK | 117 | 0.821 | 0.744 |
| MSFT | 130 | 0.962 | 0.954 |
| NKE | 117 | 0.957 | 0.915 |
| T | 107 | 0.897 | 0.972 |

Fields right only under M1 v3, by item: cost_of_revenue 15, investing_cash_flow 10,
capital_expenditure 10, short_term_investments 6, equity_attributable_to_parent 6, revenue 5,
accounts_receivable 5, income_tax_expense 5, total_equity 5, financing_cash_flow 5, pretax_income
4, net_change_in_cash 1 (72 of the 77 are MISSED under M2). Right only under M2:
total_costs_and_expenses 10, operating_cash_flow 9, inventory 5, ppe_net 5, total_equity 5,
capital_expenditure 3, net_income_including_nci 1.

**Winner on validation: M1 v3. M2 does not beat it.** The interval of the difference includes
zero and lies almost entirely below it, and the paired test favours M1 v3. M1 v3 stays the
document channel's classifier. M2's slightly higher row accuracy does not carry over to fields:
it sends clear key rows to OTHER at companies laid out unlike the train companies, for example
MRK "Cost of sales" and ABT "Acquisitions of property and equipment", in every year. Its
structure and magnitude features carry a company's layout into the trees. The answer to "does
structure help?" (8.4) is **no, not on validation, in this form**.

Caveat, in M2's favour: M1 v3's C and temperature were chosen on validation in Phase 3, while M2
chose nothing there, so the comparison is tilted towards M1 v3. The tilt cannot be measured
without refitting M1 by grouped CV. It does not change the decision, because M2 would need to
be better, not level.

## 3. Calibration (both models, fitted on train out-of-fold predictions)

Each model's out-of-fold probabilities come from the 5 company-grouped folds on train: each row
is predicted by a model fitted without its company. M2 uses the chosen grid point's out-of-fold
predictions; M1 is refitted per fold at its shipped C = 64. The calibrators are fitted on those
13,637 rows, and validation (3,475 rows) is used only to report. Platt and isotonic are one sigmoid or one isotonic map per class
(one-vs-rest), then renormalised; temperature is the blueprint's method (8.4). The method kept is
chosen between Platt and isotonic by cross-validation on the out-of-fold predictions (fit on
four folds, ECE on the fifth): **Platt** for both (train CV ECE: M2 Platt 0.0276 vs isotonic
0.0310; M1 0.0224 vs 0.0259). ECE: top label, 15 bins.

| Model | Probabilities | ECE | NLL | Brier | Row accuracy | Top class changed (of 3,475) |
|---|---|---|---|---|---|---|
| M2 | raw | 0.0399 | 0.328 | 0.114 | 0.932 | 0 |
| M2 | temperature (T = 1.328) | **0.0170** | 0.304 | 0.110 | 0.932 | 0 |
| M2 | Platt (chosen by train CV) | 0.0179 | 0.308 | 0.111 | 0.928 | 69 |
| M2 | isotonic | 0.0181 | 0.472 | 0.110 | 0.928 | 82 |
| M1 v3 | raw (T = 1) | 0.0294 | 0.255 | 0.117 | 0.928 | 0 |
| M1 v3 | shipped T = 1.296 (fitted on validation, Phase 3) | 0.0145 | 0.239 | 0.115 | 0.928 | 0 |
| M1 v3 | temperature (T = 1.320, train out-of-fold) | **0.0119** | 0.239 | 0.116 | 0.928 | 0 |
| M1 v3 | Platt (chosen by train CV) | 0.0224 | 0.200 | 0.099 | 0.932 | 125 |
| M1 v3 | isotonic | 0.0151 | 0.512 | 0.107 | 0.928 | 101 |

Findings:

1. Every method roughly halves ECE: M2 from 0.040 to about 0.017-0.018; M1 v3 from 0.029 to
   0.012-0.022.
2. **Temperature from train out-of-fold (T = 1.320) corroborates the shipped M1 v3 temperature
   (1.296, fitted on validation)** and gives the lowest validation ECE. The shipped value's
   validation ECE (0.0145) was optimistic in principle; the train-only fit says it was not
   materially so.
3. Platt and isotonic change the top class of 69-125 validation rows. They are not drop-in
   calibrators for the Correlation Agent, which ranks M1's alternatives by probability (swap
   guard p >= 0.10, 8.8). Platt lowers M1's NLL and Brier the most, so it is the better
   probability model, but its ECE is higher than temperature's.
4. **Nothing is wired in.** The calibrators are saved beside the models
   (`models/calibration_m{1,2}_v3.joblib`, `models/calibration_v3.meta.json`). The default path
   keeps M1 v3 with its shipped temperature (section 6). Replacing the shipped temperature with
   the train out-of-fold one (1.296 -> 1.320) would change Correlation Agent inputs slightly; it
   is not done without Vineet's decision.

Code: `finagent/training/calibration.py` (`python -m finagent.training.calibration --version v3`).

## 4. M3 options (not downloaded, not trained)

Blueprint 8.4 names deberta-v3-small, trained on a Colab GPU with fp16 and early stopping on
validation macro-F1. D3 says "general small encoder first; switch only on validation
evidence". Cut order 15.1 item 2 drops M3 first and ships the best classical model. **Nothing
was downloaded or trained.**

**Protocol for any option** (as for M2): serialised rows as in 8.4 (`[STMT] … [PARENT] …
[LABEL] … [INDENT] … [TOTAL] …`). Learning rate and epochs are chosen by 5-fold cross-validation
grouped by company within train, with the epochs read from per-epoch checkpoints. This replaces
the blueprint's early stopping on validation, which would make validation a choice. Then one
final fit on all of train, and E1 on validation against M1 v3 with the paired interval and
McNemar. Cost: grid of 2 learning rates x 5 folds x 4 epochs on about 80% of train, plus a final
4 epochs = **about 36 training epochs over the 13,637 train rows**, then one E1 run (about 5
minutes, as for M1 and M2).

**Measured on this Mac** (Apple silicon, PyTorch MPS; forward pass only, nothing trained or
saved): rows are short (median 15 tokens, p95 28, max 127). The pinned bge-small-en-v1.5
(33.4M parameters) takes 34 ms per batch of 32 on MPS (85 ms on CPU), so one forward pass over
train takes 15 s on MPS (36 s on CPU). A training step costs about 3 forward passes (forward
plus backward), so **one epoch is about 45 s on MPS** (about 110 s on CPU).

| Option | Download | Estimated time on this Mac | Notes |
|---|---|---|---|
| **A. deberta-v3-small** (blueprint 8.4) | Yes: about 0.3 GB of weights plus the SentencePiece vocabulary, pinned by revision and sha256 like D8 (needs Vineet's approval) | Not measured (needs the download). Estimated **2.5-4x bge-small per step**: twice the layer FLOPs (6 x 768-wide vs 12 x 384-wide), relative-position attention, and a 98M-parameter embedding updated every step; fp32 on MPS (fp16 autocast less mature than on CUDA). That is about 2-3 min per epoch, **about 1.5-3 hours** for the 36 epochs on MPS; **4-8 hours on CPU**. A Colab GPU (the blueprint's plan) would take minutes but sends the rows and the run off this machine | 141M parameters; about 2.5 GB in training (fp32 weights, gradients, AdamW states), fits in unified memory. Known MPS gaps for some DeBERTa ops may fall back to CPU |
| **B. fine-tune bge-small-en-v1.5** (already pinned for retrieval, D8; "general small encoder first", D3) | **No** (pinned weights, read offline; the fine-tuned weights go to models/, gitignored) | 36 epochs x about 45 s = **about 30 min, 30-60 min with overheads** on MPS; about 1-2 hours on CPU | Measured base speed. The structure flags in the serialised text add no cost. Encoder, not generative: closed label set and calibrated probabilities (8.4 viva point) |
| **C. skip M3** (cut order 15.1 item 2) | No | 0 | Ship the best classical model: **M1 v3** (M2 lost on validation). The E1 held-out run (Phase 8 exit) then compares M0 and M1 v3 only |

**Builder's view (Vineet decides):** if M3 is run, B first. It needs no download, takes under
an hour, and follows D3. M2's misses are label wording a pretrained encoder may generalise
better, such as ABT "Cost of products sold, excluding amortization of intangible assets" and
"Acquisitions of property and equipment". A would follow only if B beats M1 v3 on validation
and Vineet approves the download. C costs nothing and is the blueprint's first cut.

## 5. Planner post-hoc fixes (E5 e5-0003, E8 e8-0004)

**e5-0003 is post-hoc, not blind (#234); e5-0002 stays the E5 headline.** The compiler was
changed after reading e5-0002's errors, by general rules and new training families with no E5
wording (#245, #248, #253-#260). Gold files unchanged. Full report: docs/e5_v1.md section 9.

| Measure | e5-0002 (headline) | e5-0003 (post-hoc) |
|---|---|---|
| Goal accuracy | 68/92 | 78/92 |
| Coverage | 73/92 | 78/92 |
| **Precision (correct when compiled)** | 68/73 = 93.2% | **78/78 = 100%** |
| Compiled correctly (all 120) | 94/120 | 105/120 |
| Answer correct (end to end) | 67/90 | 77/90 |
| Vineet: goals / precision | 29/52 / 29/34 | 39/52 / 39/39 |
| Canonical: goals / precision | 39/40 / 39/39 | 39/40 / 39/39 |
| Values checked / unverified | 274 / 0 | 288 / 0 |

11 questions went wrong -> right and none right -> wrong (McNemar p = 0.00098). No silent wrong
goal is left: Q043 and Q055 are right; Q047, Q059 and Q086 now clarify (LOW_CONFIDENCE under
intent v3's threshold 0.925, #260).

**e8-0004** (docs/e8_replays_0004.md; pinned statements-0009 and forecast-0007): 1,536 replays.
Degraded views 1,150/1,152 correct, 1,152/1,152 safe; **0 fabricated, 0 stale**, 0 leaks, 0 wrong
flags, 0 new values. Complete view: 374 values, 0 problems; 232 pinned values checked, 0
differences. Every number equals e8-0003's. The only per-case differences are the 114
NIL_UNREAD_YEAR flags (9 with new basis text) that Phase 7 Part A added (BC29, #152). There are
0 value differences.

## 6. Default path

`python -m finagent.evaluation.default_path` (validation and train; held-out never run) against
statements-0009 and forecast-0007, output runs/phase8/default_path_check.txt:

- statements: 16,837 observations, 0 key differences; only the 84 known #196 wording fields
  differ;
- forecast: 9,486 projected values, 0 differences, 0 values not produced, 0 new lines;
- checks: 1,152 compared, 0 status or residual differences.

The output is identical, line for line, to the previous check (runs/e3-sheet). M1 v3 stays the
document channel's classifier with its shipped temperature. M2 and the calibrators are not
wired in.
