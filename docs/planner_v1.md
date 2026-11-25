# Planner v1 (Phase 6)

Status: steps 6.1 (records, configs, issuer resolution, clause splitting, slot filler, constraints,
M0 grammar), 6.2 (training data, intent classifier, threshold, model card, M1 compiler with
fallback), the E5 preparation, 6.3 (tool layer, replay hook, ratio inputs) and 6.4 (registry,
gate, loop, state, trace, rendering, `investigate`) are done. E8 (6.6): e8-0001 stopped for
review (5 fabricated values under the fully untagged view, #147, #148); after the reviewer's
decisions (#147 option 1, BC26, #151) it was re-run as e8-0002 and e8-0003 (#153): 0 fabricated,
0 stale (section 7). E5 (6.5) waits for
Vineet's questions. Decisions and problems: docs/issues_log.md section 11 (#115-#152); approved
changes BC25, BC26.

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
  **v2** (#132, the configured model): the same families, split and seed with the generator's
  surface artefacts removed (53 of 3,026 questions changed); `datasets/intents_v2.parquet`.
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

**v2 (configured, after #131 and #132):** clause accuracy 0.872, macro-F1 0.867, threshold 0.875
(precision 0.985), coverage 0.655, fallback rate 0.345, ECE after calibration 0.065; compiled
questions M1 0.752 (v1 with the #131 fix 0.748), M0 0.273. The M1 errors are 156 LOW_CONFIDENCE
and 1 false ADVICE; none compiled to wrong goals. `docs/model_cards/intent_v2.md`.

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

## 6. Tools, gate and loop (6.3, 6.4)

Entry point: `finagent.planner.investigate.investigate(question, as_of, *, tools, parser,
resources, selected_issuer, max_steps, trace_dir, run_id, compilation)` compiles the question,
plans and runs tool calls, gates every answer, renders it and (optionally) writes the trace. With
`compilation` given (E8, #145) the goals are used as given and the compiler is not called.

- **Tools** (`finagent/tools.py`, `FinAgentTools`): one method per registry action, each with a
  keyword `as_of` (a date; anything else raises ToolError). Filings come from the store as of that
  date and every statement set passes `check_point_in_time`; held-out companies are refused.
  Caches are plain dicts with explicit keys: reconciliations by (issuer, as_of, view), forecasts
  by (issuer, as_of, view, scenario). Fact actions read the post-agent statement set (#120 d);
  `reconcile` returns the I9 / I10 / I11 residuals of the final check (BC22); `project` and
  `liquidity` read the forecast at the target year (FCF = CFO - capex, #134); `credit_ratios`
  computes every ratio from `finagent/model/ratio_inputs.py` (#123) and recomputes it in Decimal.
  Phase 7 actions: `debt_service_scoped` (7.1, BC27), `risk_signals` and `explain_movement`
  (7.3: signals of configs/signals.yaml with BM25-quoted evidence or EVIDENCE_GAP; an explain
  answer is the verified value, its change from the prior year and 1-2 quoted Item 7 sentences,
  or GAP EVIDENCE_GAP showing the verified figure, D7). The gate rebuilds every quoted chunk from
  the pinned document (#157); see docs/insights_v1.md.
- **Replay hook** (`finagent/statements/replay.py`, #137): an `EvidenceView` (default FULL) says
  which evidence the build may read, for E8 (#119). With FULL the 8 validation statement sets
  equal statements-0008 (3,335 observations, 0 differences).
- **Registry** (configs/planner.yaml `actions`, `planner/registry.py`): 8.15 plus BC25. The
  eligible action with the lowest priority runs first; a fact goal starts with
  `reported_verified` and moves to `reported_tag_only` on DOC_MISSING or `reported_document_only`
  on TAG_MISSING. Forecast, credit, liquidity and debt goals need a VERIFIED statements goal of
  the same company and fiscal year; explain needs the fact. Goal expansion and conditions: #139.
- **Gate** (`planner/gate.py`, 8.16): each answer value is checked independently of the tool:
  metric allowed and requested; issuer and CIK; fiscal year and period end (a projection cites
  its origin filing and has no period end); unit and classification per metric
  (configs/planner.yaml `answers`, #118); the cited filing is known, filed by as_of and its URL
  and filed date match; a later citing filing only for a RESTATED / FROM_LATER_FILING fact
  (#117); a finite value (None only for a NOT_MEANINGFUL ratio with its reason); a basis;
  single-channel values flagged; quotes from the cited filing. A rejected value makes its goal
  INVALID with the rule's code; a non-VERIFIED outcome that carries values is INVALID
  (UNVERIFIED_WITH_VALUES).
- **Loop and state** (`planner/loop.py`, `planner/state.py`): step budget default 12 (0-30);
  identical calls are reused and not counted; stop reasons goals_resolved, evidence_gap,
  no_eligible_action, step_limit; run status COMPLETE, PARTIAL, EVIDENCE_GAP or CLARIFICATION.
- **Trace** (`planner/trace.py`, 8.17): append-only `trace.jsonl` (step lines, then the run line)
  and `trace.sqlite` (tables `runs`, `steps`); every row carries run_id and as_of.
- **Rendering** (`planner/render.py`): one line per goal with value, classification, flags,
  accession, URL and filed date (a restated value shows its original); gaps, skipped conditions
  and rejections are labelled; a comparison of two companies' same metric is a CALCULATION (or
  CALCULATION_FROM_MODEL_ASSUMPTION when either side is a projection) citing both filings; the
  Base default is stated.

Smoke check on validation companies (ad-hoc questions kept outside the repository, not gold):
every goal kind, a comparison, conditional debt with and without a shortfall and clarifications;
22 of 22 answered numbers equal statements-0008 / forecast-0006 (credit ratios also equal the
workbooks-0006 Ratios sheet). No genuine answer was rejected by the gate; tampered copies were
rejected with their codes (#142). Trace: runs/planner-smoke-0003 (gitignored).

## 7. E8 evidence replays (6.6: e8-0001, e8-0002, e8-0003)

Design (10.6, #146, configs/e8.yaml, `finagent/evaluation/e8.py`, `eval/e8_replays.py`): the 8
validation companies × 5 goal kinds (fact on 14 fixed metrics, statements, forecast Base, credit,
liquidity Base) × 4 views (complete; Company Facts hidden; both tag sources hidden = untagged;
document hidden), at the origin year, as_of 2026-11-24, through `investigate` with pre-compiled
goals and the replay hook. 384 goals per view, 160 investigations per run. Full report and the
three-run comparison: docs/e8_replays.md.

- **e8-0001** (first run) stopped for review: 5 fabricated values under the untagged view
  (understated funded debt for AMZN, NKE, MRK and net income for CVX, #147; T net income switched
  to the consolidated row, #148) and 10 wrong-reason refusals (#149).
- **Fixes after seeing e8-0001 and e2-0002 on validation (not blind):** #147 (NOT_REPORTED only
  when every channel that could show the item is visible; else TAG_HIDDEN / DOCUMENT_HIDDEN, and
  no partial sums), #151 (a statement located without rows is not located), BC26 (I6 compares
  parent net income only when NCI is known to be absent). e8-0002 applied #147 per view and
  refused 43 good document-hidden answers (#153); e8-0003 applies it per item.
- Default path unchanged: validation statement sets equal statements-0008 (3,335 observations)
  and forecast-0006 projections are unchanged (3,177 values); runs/default_path_check_e8.txt.

| view | e8-0001 correct | e8-0003 correct (95% CI over companies) | e8-0003 fallback correct | fabricated (e8-0001 → e8-0003) | stale |
|---|---|---|---|---|---|
| Company Facts hidden | 384/384 | 384/384 = 100% | 22/22 | 0 → 0 | 0 |
| untagged | 368/384 = 95.8% | 383/384 = 99.7% [99.2%, 100%] | 78/79 = 98.7% [96.1%, 100%] | **5 → 0** | 0 |
| document hidden | 383/384 = 99.7% | 383/384 = 99.7% [99.2%, 100%] | 101/101 | 0 → 0 | 0 |
| all degraded | 1135/1152 = 98.5% | 1150/1152 = 99.8% [99.6%, 100%] | 201/202 = 99.5% [98.5%, 100%] | **5 → 0** | 0 |

- e8-0003: no leak, wrong flag, gate rejection or new value in any view; the 2 goals not
  counted correct are safe refusals (AMZN net income IDENTITY_FAILED untagged; CVX revenue
  CONFLICT document hidden). Complete: 374 of 384 answered, 232 values equal the pinned runs.
- Coverage (#150, limitation): the model kinds are mostly safe refusals in the single-channel
  views (untagged final check always GAP; document hidden VERIFIED for 3 companies).

## 8. Open items

#130 (threshold: Vineet may revisit only before E5), #136 (dirty-tree builds), #138 and #139
(accepted as implemented, pending Vineet), #140 (false clarifications on two liquidity phrasings;
for Vineet, no compiler change before E5), #146, #147-#149, #114 / BC26 (reviewer decisions,
implemented, pending Vineet), #151 (b) (untagged statement location picks a quarterly table
without the BC6 counts; limitation), #152 (full-evidence funded debt leaves out a part unread in
that year: decision for Vineet).
