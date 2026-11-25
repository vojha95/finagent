# Held-out E1: pre-registration (D-HO)

Written 2026-11-25, before any held-out run. Vineet commits this file before the run, and the
run follows it exactly as written. Nothing here was chosen after looking at held-out data. No
held-out filing, row, tag or result has been opened. Two held-out facts were used: the tickers,
from `configs/universe.yaml`, and the number of pinned 10-K documents per company. The count was
read from the URLs in `evidence/manifest.json`, not from the documents.

## 1. Purpose and decision

- **D-M3:** M3 is not wired in now. This run decides whether it replaces M1 v3 (section 6).
- **D-HO:** one held-out E1 run covers M1 v3 and M3 together, at one commit, pre-registered here.
- Blueprint 10.8 allows held-out runs for validation winners only. Both models qualify:
  - M1 v3 is the shipped document-channel model, the Phase 3 winner.
  - M3 is the Phase 8 validation winner (e1-0008: 0.929 vs 0.905; paired +0.024 [-0.004,
    +0.056]).

## 2. Held-out companies

The eight companies with split `held_out` in `configs/universe.yaml` (blueprint 9.2):

**COST, NFLX, TXN, GIS, FDX, LLY, DHR, MAR**

- All of their pinned fiscal years are scored as of the as-of date. Filings are selected by
  `EvidenceStore.load_filings`, the same rule as for validation (up to five consecutive 10-Ks).
  The manifest lists five pinned 10-K documents for each company, 40 in all.
- If a company cannot be scored, the result reports it and its reason. Nothing is re-pinned,
  downloaded or fixed for it.

## 3. Models, as shipped

| model | version | file (gitignored) | sha256 of the file | probabilities |
|---|---|---|---|---|
| M1 v3 | `m1-tfidf-lr-v3` | `models/m1_v3.joblib` | `88da60a9dda239ddb74f6bb52e7610782271ca34c5c0b713bb322b408b8a4f63` | TF-IDF + logistic regression, C = 64, with the shipped temperature T = 1.2961 stored in the joblib (fitted on validation NLL in Phase 3) |
| M3 | `m3-bge-small-ft-v3` | `models/m3_v3.safetensors` | `c0b0f564224ccd198d66f841af9dd7f6220b192e5a22e8e33276906f3a6cccd0` | raw softmax: no temperature and no calibrator. The Platt calibrator fitted in Phase 8 is report-only and is not used |

- M3's base encoder is `bge-small-en-v1.5` at revision
  `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` (configs/models.yaml `embedder`), read offline.
- The meta files' sha256:
  - `models/m1_v3.meta.json`: `1583782725dafb0ba76246715f77907522bed78ce464831b3ef68fab026524f1`
  - `models/m3_v3.meta.json`: `0b74e170fa8dde48147d3935d3262d9f9370cb97eb708c10ab7ca307c652f244`
- Both models were trained on the train split of `datasets/rows_v3` (meta sha256
  `ebb3f32dcf55dbbbff2a1d3e2ebead0591ac98f8e4b46d64261314afd285c6da`).
  - Both loaders refuse another build of the dataset.
  - M3's loader also checks the weights' sha256 and the base model's pin.
- Each run header records `model_sha256`. The run is void if either model's hash differs from
  this table.

## 4. Commit, inputs and commands

- **Commit:** the commit that adds this file to `main`, called C here.
  - The run is made at C with a clean working tree. Nothing is committed between C and the run.
  - Both result headers must show `git_commit` = C (short form) and `git_dirty` false.
  - E1's dirty flag ignores `eval/experiments.csv`, the command's own record. Without that, M1's
    recorded row would make M3's run look dirty (#300).
- **Evidence:** `evidence/manifest.json` sha256
  `89ee416192001fdea1d563d721ed612ef1e2a373e0f145bfb2528358b0b99ab0`. Both headers must show it.
- **as_of:** 2026-11-24, the same as the e1-0008 validation runs and the rows_v3 build.
- **dataset_version:** v3.
- **Commands:** run these two, back to back, in this order, with nothing in between:

```
python eval/e1_document.py --model m1 --dataset-version v3 --split held_out --include-held-out \
    --as-of 2026-11-24 --record \
    --single-change "held-out E1, pre-registered (docs/heldout_e1_prereg.md): M1 v3 as shipped" \
    --conclusion "reported with the wiring decision in docs/heldout_e1_results.md"
python eval/e1_document.py --model m3 --dataset-version v3 --split held_out --include-held-out \
    --as-of 2026-11-24 --compare m1 --record \
    --single-change "held-out E1, pre-registered (docs/heldout_e1_prereg.md): M3 as shipped vs M1 v3 at the same commit" \
    --conclusion "reported with the wiring decision in docs/heldout_e1_results.md"
```

- They write `runs/e1/m1_v3_held_out.jsonl` and `runs/e1/m3_v3_held_out.jsonl`, and two rows
  in `eval/experiments.csv`. These are expected to be e1-0009 and e1-0010, if no other E1 row is
  added first.

**Code check.** The code was read, not run, on 2026-11-25. Validation was used for the one
equivalence check below.

- `pinned_tickers` and `run_e1` refuse held-out companies (HeldOutError) unless
  `--include-held-out` is passed.
- `--compare m1` reads `runs/e1/m1_v3_held_out.jsonl`. It refuses (CompareError) unless that file
  comes from the same commit.
- **Row-level metrics needed a change, made in this step (#299).**
  - `datasets/rows_v3` has no held-out rows: it was built without `--include-held-out`, and its
    meta shows held_out `companies_read` 0 and `rows` 0. So E1 would have reported no macro-F1
    on held-out.
  - The run now collects the rows it already reads, labels them with the dataset build's own
    function (`labelled_records`), and computes row accuracy and macro-F1 on them (header
    `rows_source: "run"`).
  - Checked on validation only: the collected rows equal rows_v3's 3,475 validation rows, field
    by field, and M1 v3's row metrics are identical (macro-F1 0.8453).
- Held-out data is read only by these two invocations.
  - There is no dataset build with `--include-held-out`.
  - Such a build would also change rows_v3's meta, and then both models' loaders would refuse.

## 5. Metrics

Every metric comes from the E1 command's output; nothing is computed afterwards.

**Primary**

1. Field accuracy for each model, with a 95% interval from a company bootstrap: percentile
   method, 2,000 draws, seed 0, resampling companies.
2. **The paired difference M3 − M1 v3** in field accuracy, on the fields both models scored. Its
   95% interval comes from a paired company bootstrap with the same settings, so both models'
   fields for a drawn company move together. This decides the wiring (section 6).

**Secondary (reported; they do not decide)**

3. Coverage: the share of fields where the model gave a value (not MISSED).
4. Row-level macro-F1 over the items that have held-out rows (OTHER excluded), and row accuracy,
   with `rows_source: "run"`.
5. The error taxonomy: counts of MISSED, WRONG_ROW, SIGN, SCALE and PERIOD.
6. Exact McNemar: fields right under only one model, with a two-sided p. This is secondary
   because a company's row pattern repeats across its five years, so fields are not independent.
   The company bootstrap gives the honest interval, as in e1-0008.
7. The BC13 strict variant: strict field accuracy with its interval, and strict errors, as for
   validation.

**Descriptive:** field accuracy per company for both models. Whether the best model meets the
blueprint 10.1 starting target (95% or more on held-out) is stated either way.

## 6. Wiring rule

- **M3 replaces M1 v3 in the product only if the paired 95% company-bootstrap interval of
  M3 − M1 v3 on held-out lies entirely above zero.** That means its lower bound must be strictly
  greater than 0.
- Otherwise M1 v3 stays, whatever the point estimate, McNemar p, macro-F1 or coverage. A lower
  bound of exactly 0 also keeps M1 v3.
- If M3 wins, wiring it in is a separate task. It uses M3's shipped probabilities (raw softmax,
  no calibrator), and the default path and statements/forecast are re-run. A calibrator would be
  a further single change with its own run.

## 7. Once, reported whatever it shows, nothing tuned afterwards

- **The run happens once.** The results go into `docs/heldout_e1_results.md` and the two
  experiments.csv rows, whatever they show. That includes M3 doing worse and both models falling
  short of 95%.
- **Nothing is tuned afterwards** because of held-out results. That covers models, thresholds,
  label rules, the concept map, configs and the wiring rule.
  - Held-out errors are not opened for debugging (blueprint 9.2).
  - A later change needs a new model version, chosen on validation. A held-out score for it
    would need a new, pre-registered run.
- **Technical failure:**
  - If an invocation crashes before writing its results file, it may be repeated at the same
    commit with no change.
  - If a code change is needed, the run stops, the problem is logged, and Vineet decides. Any
    retry follows an addendum to this file, committed first.
  - An invocation that wrote results is never repeated. If M1's results are written and M3's
    invocation then fails, only M3's is retried, under the same rule.
