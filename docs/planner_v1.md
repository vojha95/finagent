# Planner v1 (Phase 6)

Status: steps 6.1 (records, configs, issuer resolution, clause splitting, slot filler, constraints,
M0 grammar) and 6.2 (training data, intent classifier, threshold, model card, M1 compiler with
fallback) are done, together with the E5 preparation. The tools, gate, loop, trace (6.3, 6.4) and
the E5 and E8 runs (6.5, 6.6) are not started. Decisions and problems: docs/issues_log.md section
11 (#115-#136); approved change BC25.

This document contains no question text from the training templates and no compiled examples, so
an E5 author can read sections 1 and 5 without unblinding the set.

## 1. E5 gold set: blindness and limitations

- **Blind although written after training.** The E5 paraphrase, trap and most clarification
  questions (70 of 120) are written by Vineet from `docs/e5_question_brief.md`, which describes
  each cell by request kind, company, slots and trap type and shows no training template text.
  He writes them without opening the template, vocabulary or planner files listed in the brief.
  The set is therefore blind to the training phrasing even though the classifier already exists.
- **Limitation:** the 50 canonical questions (close to the Appendix D wording) and every gold-goal
  label are written afterwards by the coding assistant, which also wrote the training templates.
  Canonical questions are reported separately from Vineet's, so the blind subset can be read on
  its own.
- **Limitation:** the training templates and paraphrases were hand-written by the coding
  assistant (no LLM in `finagent/`, no external service). Their diversity is bounded by one
  author.
- **No LLM fallback** (Qwen) in Phase 6: blueprint 15 places it in Phase 8 (#121). E5's pass^3 is
  N/A; a 3-run determinism check is reported separately.

## 2. Goal compiler

`finagent.planner.compile.compile_question(question, parser, resources, selected_issuer)` returns
typed goals (blueprint 13 `Goal`: issuer, period, kind, metric, scenario, target, conditional) or
one CLARIFICATION with a code and category (configs/intents.yaml):

1. advice patterns → ADVICE;
2. issuer resolution (`planner/issuers.py`, #124): universe names and tickers, CIKs, companies
   outside the universe; a second company only with a comparison cue;
3. quarterly or part-year periods → UNSUPPORTED_PERIOD;
4. clause splitting and conditions (`planner/clauses.py`); fiscal years per clause, two or more →
   MULTIPLE_PERIODS;
5. one parser per clause: **M1** (intent classifier; at or above the confidence threshold the slot
   filler reads metric, period, scenario and target; below it the M0 grammar; else
   LOW_CONFIDENCE) or **M0** (the grammar alone, else OUT_OF_GRAMMAR);
6. assembly (#125, #126): one goal per issuer × metric; Base when a forecast, liquidity or debt
   goal names no scenario; a conditional debt goal takes the liquidity goal's scenario and target.

M0 is the prototype's exact-match grammar (`planner/grammar.py`) on the shared front end. Its
metric alternation is each metric's canonical name plus the prototype's own phrases, so it is not
penalised for the prototype's two hard-coded companies (#120 e). Nothing in the compiler reads a
filing; it needs configs and the pinned SEC ticker map only.

## 3. Training data and classifier

- **Data** (`planner/intent_data.py`, `configs/intent_templates.yaml`): 230 template families
  over the eight goal kinds plus advice and unsupported; 13 questions per family with slots
  filled from configs/intents.yaml and **train-split issuers only**; seed 20261125. The
  train/validation split is **by family** (20% of each intent's families), so no validation
  sentence structure is seen in training. 36 two-clause composites (validation only) score the
  whole compiler. `datasets/intents_v1.parquet`: 2,990 questions (train 2,392, validation 598).
- **Classifier** (`planner/intents.py`): vocabulary phrases replaced by their type (a reported
  item, a ratio, a liquidity measure, a scenario, a projected year, a latest-period phrase), then
  TF-IDF word 1-2-grams and char_wb 2-5-grams, class-weighted logistic regression. C chosen on
  validation macro-F1 (0.125; three values tie, #129), softmax temperature fitted on validation
  NLL (0.247; ECE 0.433 → 0.072).
- **Threshold:** the lowest grid value whose accepted validation clauses reach precision 0.98
  (configs/planner.yaml `target_precision`): **0.875** (#130).

## 4. Validation results (template families unseen in training)

Validation chooses C, the temperature and the threshold, and its questions use the same synonym
tables as the slot filler, so these numbers are optimistic. One family is 13 clauses, so per-class
figures move in steps of a family. E5 is the test.

Clause level: accuracy 0.872, macro-F1 0.867; fallback rate (clauses below the threshold) 0.349.

| intent | precision | recall | F1 | clauses |
|---|---|---|---|---|
| fact | 0.730 | 1.000 | 0.844 | 65 |
| statements | 1.000 | 1.000 | 1.000 | 65 |
| forecast | 0.829 | 0.808 | 0.818 | 78 |
| credit | 0.833 | 1.000 | 0.909 | 65 |
| liquidity | 0.812 | 1.000 | 0.897 | 65 |
| debt | 1.000 | 0.750 | 0.857 | 52 |
| explain | 1.000 | 1.000 | 1.000 | 52 |
| risk | 0.759 | 0.788 | 0.774 | 52 |
| advice | 1.000 | 1.000 | 1.000 | 52 |
| unsupported | 1.000 | 0.400 | 0.571 | 65 |

Compiled questions (exact goal set, or a clarification of the expected category): M1 0.744, M0
0.273 over 634 validation questions. False-clarification rate on supported questions: M1 0.313,
M0 0.867. Every M1 error is a clarification (mostly LOW_CONFIDENCE); no validation question
compiled to wrong goals. Per kind and style: `docs/model_cards/intent_v1.md`.

## 5. E5 preparation

- `configs/e5.yaml`: the coverage spec (goal groups by author and style, traps, clarification
  variants with their expected outcome, metric rotations and their plain-word names).
- `gold/e5_coverage_grid.csv` (120 cells: 50 canonical, 70 Vineet; fact 24, statements 8,
  forecast 16, credit 12, liquidity 12, debt 8, explain 8, risk 8, and 6 each of advice, two
  periods, wrong company, unsupported). Validation companies only, rotated so each appears 14-15
  times; fiscal years from the pinned 10-Ks visible at as_of 2026-11-24.
- `docs/e5_question_brief.md`: Vineet's 70 cells with an empty question column.
- `finagent/evaluation/e5_gold.py`: `import` copies the filled brief into
  `gold/intents_test.jsonl` (fields: id, cell, question, selected_issuer, author, style,
  trap_type, expected, clarification_category, goals, end_to_end, notes); `validate` checks every
  record against its cell (author, style, trap, selected company, the company named, no held-out
  or non-validation company) and, at the labelled stage, the labels; `freeze` writes the sha256 of
  the gold file and the grid once the set is complete and valid, and refuses a second freeze;
  `check` / `require_frozen` guard the E5 runs.

## 6. Open items

#123 (ratio inputs move, 6.3), #130 (threshold target precision: decide before E5), #131
(scenario phrase overlap), #132 (generator artefacts), #134 (forecast FCF derivation, 6.3).
