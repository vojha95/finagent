# Phase 8: M2, calibration, the M3 options and M3 option B (v1)

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

**Known limitation (#252; the reviewer accepted #251, #252 and #255 as implemented, #267-#269):**
the #231 issuer finder can make a few false matches. "targets" can be read as Target, because a
possessive without an apostrophe is accepted on a common-word name. "ups" can be read as UPS and
"pg" as PG, because they are lower-case tickers that are not in the bundled word list. None
occurs in the E5 set.

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

## 7. M3 option B: bge-small-en-v1.5 fine-tuned (stopped at the time cap)

Reviewer decision (#271; Vineet to confirm): option B, the already-pinned bge-small-en-v1.5 (no
download; D3). Protocol (BC30): the blueprint's serialised rows, train only, settings and early
stopping on a company-grouped split within train, validation only for the final E1 run, total
wall time about 90 minutes. Commit ef9fce8 with uncommitted changes. **Held-out not run or
opened.**

**Status: stopped before the out-of-fold stage finished, because the estimate went over the
90-minute cap (#277).** No M3 model is saved, there is no M3 E1 run, and there is no e1-0008
row. Selection is done and checkpointed. Of the five out-of-fold folds needed for calibration,
two are done. Waiting for Vineet's decision.

**Update (2026-11-25):** Vineet chose option 2 (#277). M3 was completed one fold per fresh
process, then calibrated and run on E1 validation (e1-0008). See **section 9**; the tables below
are kept as they were at the stop.

| Part | What |
|---|---|
| Input | `[STMT] cash flow [PARENT] investing activities [LABEL] purchases of property and equipment [INDENT] 1 [TOTAL] 0`: visible fields only (rule 7). The markers are plain text for the pinned tokenizer (30 word pieces for this example, no [UNK]). Median 32 tokens, p95 46, max 144; max_length 128, padding to the longest row of each batch |
| Model | bge-small-en-v1.5 (33.4M parameters; revision 5c38ec7c; read offline after the pin check) + dropout 0.1 + a linear layer on [CLS] (36 classes: 35 items + OTHER) |
| Training | AdamW (weight decay 0.01), batch 32, warm-up 6% then linear decay over 5 epochs, gradient clip 1.0; MPS, **fp32** (fp16 autocast: the same 229 ms per step, #272); seed 0 (Python, NumPy, torch, data order); MPS is not bitwise deterministic (#275) |
| Settings | Class weighting **balanced / square root / none** at learning rate 1e-4 (#274: OTHER is 69.5% of train rows, and "balanced" gives it about 1/1000 of the rarest item's weight) |
| Early stopping and choice | Fold 0 of the 5 company folds: train on 24 train companies, score macro-F1 (OTHER excluded) on the other 7 (3,010 rows) after every epoch; patience 2; best (setting, epoch), ties to fewer epochs |
| Code | `finagent/document/encoder.py` (`EncoderRowClassifier`), `finagent/training/m3.py`; calibration `--model m3`; E1 `--model m3` |

**Selection (company-grouped split within train; macro-F1 / accuracy on the 7 held-out train
companies):**

| Epoch | balanced | square root | none |
|---|---|---|---|
| 1 | 0.790 / 0.914 | 0.819 / 0.939 | 0.789 / 0.950 |
| 2 | 0.791 / 0.912 | 0.806 / 0.935 | 0.772 / 0.929 |
| 3 | 0.826 / 0.938 | 0.842 / 0.950 | 0.796 / 0.942 |
| 4 | 0.843 / 0.941 | **0.849 / 0.952** | 0.832 / 0.948 |
| 5 | 0.839 / 0.950 | 0.846 / 0.953 | 0.817 / 0.948 |

**Chosen: square root of balanced, 4 epochs.** Validation chose nothing. For scale only (other
rows, other companies), M2's 5-fold out-of-fold macro-F1 on train was 0.797.

**Time (#272, #276, #277):**

| Step | Wall time |
|---|---|
| Benchmarks and smoke tests (train only) | about 3 min |
| Run 1: selection, 3 settings x 5 epochs | 29.2 min |
| Run 1: out-of-fold fold 1 (587 s), then an **MPS out-of-memory crash** in fold 2 | about 11 min (lost) |
| Run 2 (memory released between fits, checkpoints, selection read from run 1's log): fold 0 369 s, fold 1 712 s, **then stopped by the budget guard** | about 18 min |
| **Spent** | **about 58 min of training** |
| Left: folds 2-4 and the final fit (4 epochs on all of train) | about 25 min at fold 0's rate, about 50 min at fold 1's |

The fold time doubled between fold 0 and fold 1. Padding does not explain it: fold 1's batches
carry 7% more padded tokens. The cause is not established; thermal throttling, or MPS allocator
state across fits in one process, are the candidates. The total would be about 85-108 min
against the cap, so the run was stopped and reported, per the brief.

**Options for Vineet (#277):**
1. Continue from the checkpoints with the same protocol: `python -m finagent.training.m3
   --version v3 --budget-minutes 60`. Then `python -m finagent.training.calibration --version
   v3 --model m3`, `python eval/e1_document.py --model m3 --dataset-version v3 --split
   validation --as-of 2026-11-24 --compare m1 --record ...`, and the four-model table below.
2. The same, one fold per fresh process, in case the slowdown is process state.
3. Stop M3 here and ship M1 v3 (cut order 15.1 item 2).

### E1 on validation, all models at the same commit (ef9fce8)

M0, M1 v3 and M2 were re-run at ef9fce8 for the paired tests. Their field results are identical
to e1-0005, e1-0006 and e1-0007 (#278). The M3 column is empty: no M3 model exists yet.

| Measure | M0 rules | M1 v3 | M2 | M3 |
|---|---|---|---|---|
| Field accuracy [95% company bootstrap] | 0.773 [0.660, 0.876] | **0.905** [0.859, 0.950] | 0.865 [0.772, 0.944] | not run |
| Fields | 971 | 971 | 971 | — |
| Coverage (field found) | 0.791 | 0.916 | 0.875 | — |
| Errors | MISSED 203, SIGN 5, WRONG_ROW 12 | MISSED 82, SIGN 5, WRONG_ROW 5 | MISSED 121, SIGN 5, WRONG_ROW 5 | — |
| Field accuracy, BC13 off (strict) | 0.772 [0.657, 0.876] | 0.897 [0.850, 0.944] | 0.853 [0.761, 0.933] | — |
| Row accuracy (OTHER included) | 0.917 | 0.928 | 0.932 | — |
| Row macro-F1 (33 items, OTHER excluded) | 0.791 | 0.845 | 0.848 | — |
| OTHER precision / recall | 0.924 / 0.979 | 0.973 / 0.941 | 0.945 / 0.973 | — |
| ECE, validation rows (15 bins, top label) | — (probability always 1) | 0.0145 (shipped T 1.296) | 0.0399 (raw) | — |

| Against M1 v3 | Difference [95% paired company bootstrap] | Right only under M1 v3 | Right only under the model | McNemar p |
|---|---|---|---|---|
| M0 rules | -0.132 [-0.222, -0.044] | 146 | 18 | 4.3e-26 |
| M2 | -0.040 [-0.092, +0.004] | 77 | 38 | 0.00035 |
| M3 | not run | | | |

| Company | Fields | M0 rules | M1 v3 | M2 |
|---|---|---|---|---|
| ABT | 106 | 0.528 | 0.811 | 0.604 |
| AMZN | 130 | 0.885 | 0.846 | 0.808 |
| CL | 142 | 0.915 | 1.000 | 1.000 |
| CVX | 122 | 0.590 | 0.918 | 0.877 |
| MRK | 117 | 0.658 | 0.821 | 0.744 |
| MSFT | 130 | 1.000 | 0.962 | 0.954 |
| NKE | 117 | 0.829 | 0.957 | 0.915 |
| T | 107 | 0.692 | 0.897 | 0.972 |

**Validation winner so far: M1 v3**, among the three models evaluated. **M3 is undecided**
until its run is finished. Nothing is wired in, and the default path keeps M1 v3 (section 8).
If M3 later wins, wiring it would change the document channel's predictions and the
probabilities the Correlation Agent ranks (swap guard p >= 0.10). Statement and forecast
differences against statements-0009 and forecast-0007 would then be expected, and that step
waits for Vineet.

## 8. Default path after the M3 work

`python -m finagent.evaluation.default_path --out runs/phase8/default_path_check_m3.txt` (validation and train; held-out never run): **identical, line for line, to section 6's check**. Statements: 16,837 observations, 0 key differences, only the 84 known #196 wording fields; forecast: 9,486 values, 0 differences; checks: 1,152, 0 differences. The exit code is 1 ("CHANGED") because of the 84 known wording fields, as before. M1 v3 with its shipped temperature stays the document channel's classifier; M2, M3 and the calibrators are not wired in.

## 9. M3 completed (option 2), calibration and E1 on validation (e1-0008)

Vineet's decision on #277: option 2. The remaining out-of-fold folds and the final fit ran
**one per fresh process** from the checkpoints in models/m3_v3.work/, under a **60-minute cap**
checked before every launch (`python -m finagent.training.m3 --version v3 --stage driver
--budget-minutes 60`; #292). The selection was not re-run (its saved signature matched), and
validation chose nothing. **Held-out not run or opened**; no `--include-held-out`.

**Time:**

| Step | Wall time |
|---|---|
| Fold 2 (fresh process) | 485 s |
| Fold 3 (fresh process) | 501 s |
| Fold 4 (fresh process) | 498 s |
| Final fit, 4 epochs on all of train, then validation scoring and saving (fresh process) | 9.1 min fit |
| **Option 2, total (driver)** | **34.3 min of the 60-minute cap** (first projection 51.4 min, at fold 1's 712 s) |
| Protocol stages, all runs (model card) | selection 29.2 + out-of-fold 42.8 + final 9.1 = 81.1 min |
| Spent on M3 training overall | about 58 min before (#277) + 34.3 min = about 92 min, plus about 3 min of benchmarks |

In fresh processes the folds took 485-501 s (121-125 s per epoch). Fold 1 took 712 s as the
second fit in one process; fold 0 took 369 s as the first. That is consistent with process
state mattering, but the cause is still not established (#277).

**Calibration (report only; train out-of-fold probabilities, 13,637 rows; validation 3,475
rows only to report):**

| Probabilities | ECE | NLL | Brier | Row accuracy | Top class changed |
|---|---|---|---|---|---|
| raw | 0.0296 | 0.252 | 0.079 | 0.953 | 0 |
| temperature (T = 1.183) | 0.0199 | 0.235 | 0.078 | 0.953 | 0 |
| **Platt (chosen by train CV: 0.0194 vs isotonic 0.0285)** | **0.0133** | 0.217 | 0.078 | 0.957 | 23 |
| isotonic | 0.0221 | 0.387 | 0.080 | 0.957 | 34 |

Nothing is wired in (`models/calibration_m3_v3.joblib`, `models/calibration_m3_v3.meta.json`).

### E1 on validation, all four models at the same commit (e7bac6f, dirty)

M0, M1 v3 and M2 were re-run at e7bac6f. Their 971 field results are identical to the ef9fce8
runs (#295). The ef9fce8 files are kept as runs/e1/m{0,1,2}_v3_validation_ef9fce8.jsonl. M3:
`python eval/e1_document.py --model m3 --dataset-version v3 --split validation --as-of
2026-11-24 --compare m1`, recorded as **e1-0008**.

| Measure | M0 rules | M1 v3 | M2 | M3 |
|---|---|---|---|---|
| Field accuracy [95% company bootstrap] | 0.773 [0.660, 0.876] | 0.905 [0.859, 0.950] | 0.865 [0.772, 0.944] | **0.929** [0.893, 0.966] |
| Fields | 971 | 971 | 971 | 971 |
| Coverage (field found) | 0.791 | 0.916 | 0.875 | 0.934 |
| Errors | MISSED 203, SIGN 5, WRONG_ROW 12 | MISSED 82, SIGN 5, WRONG_ROW 5 | MISSED 121, SIGN 5, WRONG_ROW 5 | MISSED 64, SIGN 5 |
| Field accuracy, BC13 off (strict) | 0.772 [0.657, 0.876] | 0.897 [0.850, 0.944] | 0.853 [0.761, 0.933] | 0.923 [0.890, 0.956] |
| Row accuracy (OTHER included) | 0.917 | 0.928 | 0.932 | 0.953 |
| Row macro-F1 (33 items, OTHER excluded) | 0.791 | 0.845 | 0.848 | 0.877 |
| OTHER precision / recall | 0.924 / 0.979 | 0.973 / 0.941 | 0.945 / 0.973 | 0.978 / 0.974 |
| ECE, validation rows (15 bins, top label) | — (probability always 1) | 0.0145 (shipped T 1.296) | 0.0399 (raw) | 0.0296 (raw); 0.0133 (Platt) |

| Against M1 v3 | Difference [95% paired company bootstrap] | Right only under M1 v3 | Right only under the model | McNemar p |
|---|---|---|---|---|
| M0 rules | -0.132 [-0.222, -0.044] | 146 | 18 | 4.3e-26 |
| M2 | -0.040 [-0.092, +0.004] | 77 | 38 | 0.00035 |
| **M3** | **+0.024 [-0.004, +0.056]** | 16 | 39 | 0.0027 |

| Company | Fields | M0 rules | M1 v3 | M2 | M3 |
|---|---|---|---|---|---|
| ABT | 106 | 0.528 | 0.811 | 0.604 | **0.934** |
| AMZN | 130 | 0.885 | 0.846 | 0.808 | 0.885 |
| CL | 142 | 0.915 | 1.000 | 1.000 | 1.000 |
| CVX | 122 | 0.590 | **0.918** | 0.877 | 0.877 |
| MRK | 117 | 0.658 | 0.821 | 0.744 | **0.863** |
| MSFT | 130 | 1.000 | 0.962 | 0.954 | 0.969 |
| NKE | 117 | 0.829 | 0.957 | 0.915 | **1.000** |
| T | 107 | 0.692 | 0.897 | **0.972** | 0.888 |

**Where M3 and M1 v3 differ:** 13 (company, item) row patterns repeated across years.
- **In M3's favour (9 patterns, 39 fields):** ABT operating_cash_flow, operating_income,
  income_tax_expense; AMZN depreciation_and_amortisation; MRK inventory, total_assets; NKE
  operating_cash_flow; T total_costs_and_expenses; MSFT operating_cash_flow (one year).
- **In M1 v3's favour (4 patterns, 16 fields):** CVX short_term_investments; MRK
  cost_of_revenue; T income_tax_expense, net_income_including_nci.
- Every one of these fields is MISSED under the losing model; none is a wrong row.

**Validation winner: M3.** It has the highest field accuracy, coverage, row accuracy and
macro-F1. It is better than or level with M1 v3 at 6 of 8 companies, has no WRONG_ROW errors,
and the paired test favours it.

**Caveats:**
1. The 95% paired company-bootstrap interval of the difference, +0.024 [-0.004, +0.056],
   **includes zero**, though it lies almost entirely above it. McNemar counts each year of a
   repeated row pattern as independent, so its p = 0.0027 overstates the evidence. There are
   13 patterns, 9 to 4.
2. The tilt noted in section 2 runs **against** M3: M1 v3's C and temperature were chosen on
   validation, while M3 chose nothing there.

**Nothing is wired in.** The document channel keeps M1 v3 with its shipped temperature.

**Next steps, for Vineet:**
- Wiring M3 would change the document channel's predictions and the probabilities the
  Correlation Agent ranks (swap guard p >= 0.10), and with them statements-0009 and
  forecast-0007.
- The held-out E1 run is for validation winners only (blueprint 10.8). It is not run here.

## 10. Default path after M3

`python -m finagent.evaluation.default_path --out runs/phase8/default_path_check_m3_final.txt`
(validation and train; held-out never run): **byte-identical to section 6's check.**
- Statements: 16,837 observations; the only differences are the 84 known #196 wording fields
  (exit code 1, "CHANGED").
- Forecast: 9,486 values, 0 differences.
- Checks: 1,152, 0 differences.

Nothing is wired in (#296).

## 11. Held-out E1 (e1-0009, e1-0010): results and the wiring decision

The pre-registered run (docs/heldout_e1_prereg.md, commit C = 089962e) was made once on
2026-11-25, with a clean tree and the two commands exactly as written. Both headers show
git_commit 089962e, git_dirty false, the pre-registered evidence manifest, and model sha256
values. Full report: **docs/heldout_e1_results.md**.

| Measure | M1 v3 (e1-0009) | M3 (e1-0010) |
|---|---|---|
| Field accuracy [95% company bootstrap] | 0.913 [0.846, 0.961] | 0.941 [0.887, 0.985] |
| Fields | 869 | 869 |
| Coverage | 0.945 | 0.964 |
| Row macro-F1 (34 items, rows_source "run") | 0.843 | 0.904 |
| Strict (BC13 off) | 0.900 [0.826, 0.954] | 0.937 [0.883, 0.979] |

**Paired M3 − M1 v3: +0.029 [0.000, +0.055]**, and the lower bound is exactly 0.0. McNemar
(secondary): 9 vs 34, p = 0.00017.

The pre-registered rule (section 6):

> **M3 replaces M1 v3 in the product only if the paired 95% company-bootstrap interval of
> M3 − M1 v3 on held-out lies entirely above zero.** That means its lower bound must be strictly
> greater than 0. Otherwise M1 v3 stays, whatever the point estimate, McNemar p, macro-F1 or
> coverage. A lower bound of exactly 0 also keeps M1 v3.

**Outcome: M1 v3 stays the document channel's classifier, with its shipped temperature. M3 is
not wired in.**
- The blueprint 10.1 target (95% or more on held-out) is not met by either model.
- GIS was scored on one fiscal year only (27 fields). It was reported and not investigated
  (#309).
- Nothing is tuned afterwards (pre-registration section 7).
