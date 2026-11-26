# FinAgent: final results (E1 to E8)

Phase 10, first step (blueprint section 15). Written on 2026-11-26 from recorded runs only, on top of commit 65bf03b (clean tree before this step). Every number on a line comes from a file cited on that same line, and `tests/test_final_results.py` checks that each one appears there (a plain string match). Run ids, commits and the single change of each run are in `eval/experiments.csv`; E4 is recorded in its own score files.

Two rules hold throughout. The validation split (ABT, AMZN, CL, CVX, MRK, MSFT, NKE, T) is where everything was developed and measured, so validation figures are optimistic wherever a change followed a validation result; each section says which. The held-out companies were measured once, in held-out E1, and never debugged, tuned or evaluated again (blueprint section 9.2; BC36).

## Summary

| Eval | Question | Headline [interval] | Baseline | Decision | Source |
|---|---|---|---|---|---|
| E1 | Does the document channel read statements correctly on unseen companies? | Held-out field accuracy: shipped M1 v3 0.913 [0.846, 0.961]; M3 0.941 [0.887, 0.985]; paired M3 − M1 v3 +0.029 [0.000, +0.055] | M0 rules, validation only: 0.773 [0.660, 0.876] against M1 v3 0.905 [0.859, 0.950] | Pre-registered: M3 ships only if the paired lower bound is above 0. It is exactly 0, so **M3 is not shipped**. The 95% target is not met. | `docs/heldout_e1_results.md`, `docs/phase8_v1.md` |
| E2 | Does the Correlation Agent help, and is it safe? | Injected errors corrected 10/40 = 25.0% [12.5%, 35.0%]; 0 correct values wrongly changed; clean readings 90.5% → 90.8% (McNemar p = 0.25, no significant gain) | The same readings without the agent | None pre-registered. Kept as a safety net: 29 of 40 injected errors are invisible to its checks. | `docs/e2_agent.md` |
| E3 | Does retrieval find the explaining passage? | Primary MRR@5: BM25 0.677 [0.530, 0.815]; best change, hybrid + reranker, +0.048 [-0.049, +0.140] | BM25 | Rule written before scoring: switch only if a paired interval lies above 0. None does, so BM25 stays. | `docs/e3_scores_0001.md` |
| E4 | Are insight cards faithful? | Unsupported-claim rate 0.704 [0.519, 0.852] of 27 quoted cards (Vineet's labels); judge agreement, three-label kappa 0.411 [0.077, 0.700] | Vineet's labels against a local NLI judge | No pass mark. Evidence v2 was adopted afterwards (post-hoc): 0.926 of the 27 slots become EVIDENCE_GAP | `docs/e4_scores_0001.md`, `docs/e4_scores_0002.md` |
| E5 | Does the planner understand questions and answer correctly? | Trained compiler goal accuracy 68/92 = 73.9% [64%, 82%]; on Vineet's blind questions 29/52; precision 100.0% → 93.2% | Regex grammar: 24/92 = 26.1% [18%, 36%] | Blueprint expectation (more coverage without lower precision): **not met**. The post-hoc e5-0003 is not the headline. | `docs/e5_v1.md` |
| E6 | Is the forecast better than naive? | No accuracy claim on any row. CFO: APE difference -0.100 [-0.166, -0.012], naive better | Naive last value | Pre-registered: claim only if the paired interval lies entirely above 0. **No claim.** | `docs/e6_results.md` |
| E7 | Is it fast and cheap enough? Cost was not measured. | First answer about a company, cold p50 / p95 / max: 0.22 / 0.74 / 0.79 s (e7-0002), was 0.25 / 16.38 / 35.53 s (e7-0001); 0 of 40 calls failed | Manual build: about 20 hours, an estimate (D10), not measured | None. Light E7 on one machine only (BC33). | `docs/e7_light.md`, `docs/e7_light_e7-0002.md`, `docs/issues_log.md` |
| E8 | Does FinAgent return gaps when evidence is missing? | Degraded goals correct 1150/1152 = 99.8% [99.6%, 100.0%]; 0 fabricated values (e8-0004) | Complete evidence | Blueprint target of 0 fabricated: met from e8-0003, after the first run (e8-0001) found 5 fabricated values and fixes were made on validation | `docs/e8_replays_0004.md`, `eval/experiments.csv` |

## The honest negatives

- **M3 is not shipped.** On held-out, M3 − M1 v3 is +0.029 with a paired 95% interval of [0.000, +0.055]. The pre-registered rule needs the lower bound strictly above 0; it is exactly 0, so M1 v3 stays (e1-0010; `docs/heldout_e1_results.md`).
- **The 95% accuracy target is not met** on held-out: M3 0.941, M1 v3 0.913 (`docs/heldout_e1_results.md`).
- **E6: no forecast accuracy claim on any row.** For CFO the naive last value is clearly better: APE difference -0.100 [-0.166, -0.012], MAE 15,913 against 10,220 (USD millions). Only 28 of 32 origins had a VERIFIED forecast, and CFO was scored at 14 / 32 (`docs/e6_results.md`).
- **E4: most v1 quotes do not support their card.** Vineet judged 19 of 27 quoted cards unsupported, a rate of 0.704 [0.519, 0.852]. Under evidence v2, 0.926 of slots are EVIDENCE_GAP instead (`docs/e4_scores_0001.md`, `docs/e4_scores_0002.md`).
- **E5: Vineet's blind questions were understood far less often than the builder's.** Goal accuracy 29/52 = 55.8% on Vineet's questions against 39/40 = 97.5% on the builder's canonical ones, and precision fell from 100.0% to 93.2% (e5-0002; `docs/e5_v1.md`).
- **E2: the agent sees few errors.** 29 of 40 injected errors leave every evaluation-mode check unchanged, and on clean readings there is no significant gain (`docs/e2_agent.md`).
- **E8: the first run fabricated values.** e8-0001 returned 5 fabricated values under the untagged view; they were fixed before e8-0003 and e8-0004, which are therefore not blind (`eval/experiments.csv`).
- **E3: no retrieval variant beat BM25** on the primary measure; the reranker's gain shows only on the strict secondary measure, +0.164 [+0.012, +0.331], reported only (`docs/e3_scores_0001.md`).
- **Not built or not run:** the LLM fallback and its pass^3 (#121, #217); the E1 M4 ceiling (no run in `eval/experiments.csv`); a public deployment (#322).

## E1: document-channel accuracy

| | |
|---|---|
| Question | Does the document channel read statements correctly on unseen companies? (blueprint section 10.1) |
| Baseline | M0, synonym rules (`configs/label_synonyms.yaml`), run on validation. Held-out ran only M1 v3 and M3, as pre-registered. |
| Split | Held-out: COST, DHR, FDX, GIS, LLY, MAR, NFLX, TXN, 869 fields (`docs/heldout_e1_results.md`). Validation: 8 companies, 971 fields (`docs/phase8_v1.md`). |
| Runs and commits | Held-out: e1-0009 (M1 v3) and e1-0010 (M3), commit 089962e, clean tree. Validation: e1-0005 (M0) and e1-0006 (M1 v3) at a89cce5; e1-0007 (M2) at 0b303e8; e1-0008 (M3, with M0, M1 v3 and M2 re-run identical) at e7bac6f; all three with uncommitted changes. |

| Measure | Value | Source |
|---|---|---|
| Held-out field accuracy, M1 v3 (shipped) [95% company bootstrap] | 0.913 [0.846, 0.961] | `docs/heldout_e1_results.md` |
| Held-out field accuracy, M3 | 0.941 [0.887, 0.985] | `docs/heldout_e1_results.md` |
| Held-out paired M3 − M1 v3 | +0.029 [0.000, +0.055] | `docs/heldout_e1_results.md` |
| Held-out McNemar (secondary): right only under M1 v3 / only under M3 | 9 / 34, p = 0.00017 | `docs/heldout_e1_results.md` |
| Held-out companies at 95% or more | M3 at 4 of 8, M1 v3 at 3 | `docs/heldout_e1_results.md` |
| Validation field accuracy: M0 / M1 v3 / M2 / M3 | 0.773 / 0.905 / 0.865 / 0.929 | `docs/phase8_v1.md` |
| Validation intervals: M0 / M1 v3 / M3 | [0.660, 0.876] / [0.859, 0.950] / [0.893, 0.966] | `docs/phase8_v1.md` |
| Validation paired differences against M1 v3: M0 / M2 / M3 | -0.132 [-0.222, -0.044] / -0.040 [-0.092, +0.004] / +0.024 [-0.004, +0.056] | `docs/phase8_v1.md` |
| Audited label errors among validation rows (a lower bound) | 0.0014 | `docs/label_audit_v2.md` |

**Pre-registered decision** (`docs/heldout_e1_prereg.md`, section 6): M3 replaces M1 v3 only if the paired 95% company-bootstrap interval of M3 − M1 v3 on held-out lies entirely above zero. The lower bound is exactly 0.0, so M1 v3 stays and M3 is not wired in (`docs/heldout_e1_results.md`). The blueprint's starting target, 95% or more on held-out, is not met (`docs/heldout_e1_results.md`).

**Blind and post-hoc.**
- Blind: the held-out run. It was pre-registered at 089962e (models fixed by sha256, metrics and the wiring rule written down), run once on a clean tree, and no held-out error was opened afterwards.
- Not blind: every validation figure. The v2 and v3 datasets (unit-line rule BC12; label-audit resolutions BC13, BC14) followed validation errors, and M1 v3's C and temperature were chosen on validation, which tilts the comparison towards M1 v3 (`docs/e1_validation.md`, `docs/phase8_v1.md`).
- Limitation of the held-out result: GIS was scored on one fiscal year, 27 of the 869 fields; the cause was not investigated (#309, #311; `docs/heldout_e1_results.md`).

**In plain English:** on eight companies nobody looked at during development, the shipped reader matched the companies' own tagged figures about nine times in ten; a larger model did slightly better, but not by the margin set in advance, so it was not switched in.

## E2: Correlation Agent ablation

| | |
|---|---|
| Question | Does the Correlation Agent help, and is it safe? (blueprint section 10.2) |
| Baseline | The same M1 v3 readings without the agent (its "before"), in evaluation mode with tags hidden from the agent. |
| Split | Validation: 40 company-years, 971 scored fields; one injected error per company-year in the corrupted condition (`docs/e2_agent.md`). |
| Runs and commits | e2-0001 (agent v1) at 74d585c; e2-0002 (v1.1) at 9e4ed05; e2-0003 (v1.2, the shipped agent) at bb7e299; all with uncommitted changes. |

| Measure (e2-0003) | Clean | Corrupted | Source |
|---|---|---|---|
| Field accuracy before → after | 90.5% → 90.8% | 86.4% → 87.7% | `docs/e2_agent.md` |
| McNemar (right only before / only after, p) | 0 / 3, p = 0.25 | 0 / 13, p = 0.000244 | `docs/e2_agent.md` |
| Correct values wrongly changed | 0 | 0 | `docs/e2_agent.md` |
| Injected errors detected | — | 11/40 = 27.5% [17.5%, 37.5%] | `docs/e2_agent.md` |
| Injected errors corrected | — | 10/40 = 25.0% [12.5%, 35.0%] | `docs/e2_agent.md` |
| Identity checks passing after | 99.0% | 99.0% | `docs/e2_agent.md` |

The first run, e2-0001 (agent v1), corrected 8/40 injected errors (20% [10%, 30%]) and wrongly changed 2 correct values in the corrupted condition, 1 in the clean one (`eval/experiments.csv`).

**Decision:** none was pre-registered. The agent is kept as a safety net. 29 of 40 injected errors (73%) leave every evaluation-mode check unchanged, because only about 45% of scored fields enter a computed check; no agent working from this identity library can see them (#112, deferred; `docs/e2_agent.md`).

**Blind and post-hoc.** e2-0001 is the first result. e2-0002 and e2-0003 are **not blind**: the guards (#109-#111) and BC26 were decided after seeing e2-0001, e2-0002 and e8-0001 on the same 40 company-years, so their gains over e2-0001 are partly fitted. Held-out companies are never run in E2 (`docs/e2_agent.md`).

**In plain English:** the checking agent never broke a correct number in its final version and fixed a quarter of the planted mistakes, but most planted mistakes are of a kind its accounting checks cannot see.

## E3: retrieval

| | |
|---|---|
| Question | Does retrieval find the passage that explains a signal or a movement? (blueprint section 10.3) |
| Baseline | BM25, the default before E3. |
| Split | The six test companies (validation without the dev companies AMZN and MSFT): CVX, NKE, MRK, T, CL, ABT. 30 queries; 23 scored and 7 left out with no relevant passage (`docs/e3_scores_0001.md`). |
| Runs and commits | e3-0001 at 7964fd2 (uncommitted changes); e3-0002 (post-hoc quote threshold) at 932d238 (uncommitted changes). |

| Variant (primary, lenient) | Recall@5 | MRR@5 | MRR@5 minus BM25 [paired interval] | Source |
|---|---|---|---|---|
| BM25 | 0.803 [0.683, 0.909] | 0.677 [0.530, 0.815] | — | `docs/e3_scores_0001.md` |
| Dense | 0.579 [0.417, 0.729] | 0.588 [0.425, 0.750] | -0.088 [-0.220, +0.041] | `docs/e3_scores_0001.md` |
| Hybrid (RRF) | 0.687 [0.531, 0.822] | 0.721 [0.554, 0.862] | +0.044 [-0.054, +0.138] | `docs/e3_scores_0001.md` |
| Hybrid + reranker | 0.858 [0.768, 0.934] | 0.725 [0.594, 0.841] | +0.048 [-0.049, +0.140] | `docs/e3_scores_0001.md` |

**Decision rule, written before scoring** (`docs/e3_v1.md`, section 4): a variant replaces BM25 only if its primary MRR@5 gain has a paired 95% interval above zero. None has, so BM25 stays (`docs/e3_scores_0001.md`). Recall@5 is relative to the pool of the four variants' top 5, so it can overstate true recall (#237); one better passage lay outside every variant's top 5 (E3-Q19, #282).

**Blind and post-hoc.** Blind: queries were chosen by a rule written before retrieval ran on the test companies; Vineet labelled a shuffled pool with the variant key hidden; scoring followed the written rule. One annotator only. Post-hoc: e3-0002 chose the evidence v2 quote threshold on these labels by leave-one-company-out, 5.263 with cross-validated coverage 0.267 and strict precision 0.625 (D-EV; `docs/e3_threshold_0002.md`).

**In plain English:** the simple keyword search found the right paragraph as often as the fancier methods, so it stayed the default.

## E4: faithfulness of insight cards

| | |
|---|---|
| Question | Do the quoted passages on insight cards support the card's claim? (blueprint section 10.3) |
| Baseline | Vineet's labels as the reference, against an independent local NLI judge (BC31). |
| Split | The six test companies' quoted cards: 27 cards (`docs/e4_scores_0001.md`). |
| Runs and commits | e4-0001 at d1bcefc; e4-0002 (evidence v2, post-hoc) at b877923 with uncommitted changes. |

| Measure | e4-0001 (v1, BM25 quotes) | Source |
|---|---|---|
| Unsupported-claim rate (Vineet) | 0.704 [0.519, 0.852] | `docs/e4_scores_0001.md` |
| Partial rate (Vineet) | 0.148 [0.037, 0.296] | `docs/e4_scores_0001.md` |
| Unsupported rate (judge) | 0.741 [0.556, 0.889] | `docs/e4_scores_0001.md` |
| Cohen's kappa, three labels / supported vs not | 0.411 [0.077, 0.700] / 0.270 [-0.145, 0.665] | `docs/e4_scores_0001.md` |
| EVIDENCE_GAP share of cards, every year | 0.227 (15 of 66) | `docs/e4_scores_0001.md` |

| Measure | e4-0002 (v2, post-hoc and optimistic) | Source |
|---|---|---|
| Slots that become EVIDENCE_GAP | 0.926 [0.815, 1.000] | `docs/e4_scores_0002.md` |
| Unsupported-claim rate on the 2 slots still quoted | 0.500 [0.000, 1.000] | `docs/e4_scores_0002.md` |

**Decision:** the scoring rule was written before any label, with no pass mark. After e4-0001, Vineet chose evidence v2 (D-EV): quote only passages above a reranker threshold, otherwise show EVIDENCE_GAP (`docs/e4_scores_0002.md`).

**Blind and post-hoc.** Blind: Vineet labelled every card before seeing the judge, whose output was kept in a hidden key. One annotator only. Post-hoc: e4-0002. Evidence v2 was designed after e4-0001 and its threshold chosen on E3 labels of the same six companies, so e4-0002 favours v2 and rests on 2 quoted slots; its kappa of 1.000 is not informative (`docs/e4_scores_0002.md`).

**In plain English:** most of the quotations the first version attached to its risk cards did not actually explain the risk, so the shipped version now says "no evidence found" far more often instead of quoting something weak.

## E5: planner

| | |
|---|---|
| Question | Does the planner understand questions and answer them correctly? (blueprint section 10.4) |
| Baseline | M0, the prototype's regex grammar (e5-0001). |
| Split | Validation companies; frozen gold set of 120 questions: Vineet's 70, written blind, and the builder's 50 canonical questions (`docs/e5_v1.md`). |
| Runs and commits | e5-0001 (grammar) and e5-0002 (trained intent model v2, the headline) at 5dce109; e5-0003 (post-hoc, shipped intent model v3) at 0b303e8; all with uncommitted changes. |

| Measure | e5-0001 (grammar) | e5-0002 (trained, headline) | Source |
|---|---|---|---|
| Goal accuracy | 24/92 = 26.1% [18%, 36%] | 68/92 = 73.9% [64%, 82%] | `docs/e5_v1.md` |
| Precision (correct when compiled) | 24/24 = 100.0% [86%, 100%] | 68/73 = 93.2% [85%, 97%] | `docs/e5_v1.md` |
| Goal accuracy, Vineet's blind questions | 3/52 = 5.8% | 29/52 = 55.8% | `docs/e5_v1.md` |
| Goal accuracy, builder's canonical questions | 21/40 = 52.5% | 39/40 = 97.5% | `docs/e5_v1.md` |
| Correct clarification | 25/28 = 89.3% [73%, 96%] | 26/28 = 92.9% [77%, 98%] | `docs/e5_v1.md` |
| Answer correct (end-to-end questions) | 24/90 = 26.7% [19%, 37%] | 67/90 = 74.4% [65%, 82%] | `docs/e5_v1.md` |
| Values returned that were unverified or untraced | 0 of 84 | 0 of 274 | `docs/e5_v1.md` |

Paired by question, 45 were compiled correctly only under e5-0002 and 0 only under e5-0001: exact McNemar p = 5.7e-14 (`docs/e5_v1.md`).

**Decision:** the blueprint expected the trained compiler to raise coverage without lowering precision (section 10.4). Coverage rose, but precision fell from 100.0% to 93.2% with 5 silent wrong goals, so the expectation is **not met** (`docs/e5_v1.md`). The LLM fallback and its pass^3 were not built (#121, #217).

**Blind and post-hoc.** Blind: Vineet's 70 questions, written without seeing the training templates. Not blind: the builder's 50 canonical questions and all 120 labels, written after training (#207). Post-hoc: e5-0003, run on the same frozen set after changes prompted by e5-0002's errors (#234): goal accuracy 78/92 = 84.8%, Vineet's questions 39/52 = 75.0%, precision 78/78 = 100.0%. It is not the headline (`docs/e5_v1.md`).

**In plain English:** the trained question reader understood about three questions in four, against one in four for the old pattern rules, but it did worse on the questions an outsider wrote than on the builder's own, and in a few cases it confidently answered a different question.

## E6: point-in-time forecast backtest

| | |
|---|---|
| Question | Is the one-year-ahead forecast better than naive? (blueprint section 10.5) |
| Baseline | Naive last value. |
| Split | Validation companies; 32 origins at filing dates, all qualifying; the shipped Base forecast was VERIFIED at 28 and GAP at 4 (`docs/e6_results.md`). |
| Runs and commits | e6-0001 at 698bb67, clean tree, run once as pre-registered (`docs/e6_prereg.md`). |

| Row | Coverage: scored / qualifying | MAE model (USD millions) | MAE naive | APE difference [paired company bootstrap] | Claim | Source |
|---|---|---|---|---|---|---|
| Revenue | 28 / 32 | 19,608 | 20,912 | -0.019 [-0.072, +0.027] | none | `docs/e6_results.md` |
| Operating income | 15 / 32 | 8,744 | 12,830 | -0.025 [-0.111, +0.058] | none | `docs/e6_results.md` |
| CFO | 14 / 32 | 15,913 | 10,220 | -0.100 [-0.166, -0.012] | none (naive better) | `docs/e6_results.md` |
| Capex | 28 / 32 | 6,838 | 7,237 | -0.009 [-0.040, +0.034] | none | `docs/e6_results.md` |
| FCF | 14 / 32 | 12,132 | 9,078 | +0.035 [-0.241, +0.310] | none | `docs/e6_results.md` |

APE difference is naive APE minus model APE, so a positive value favours the model. Operating income is also reported by basis: reported 15 / 20 and EBIT proxy 0 / 12 (`docs/e6_results.md`).

**Pre-registered decision** (`docs/e6_prereg.md`): the model beats naive on a row only if the 95% paired company-bootstrap interval of the APE difference lies entirely above 0. No row qualifies, so **no accuracy claim is made on any of the 7 rows**. For CFO the interval lies entirely below 0: naive is better (`docs/e6_results.md`).

**Blind and post-hoc.** Blind: the harness was built and checked on train companies, the run was pre-registered and committed before it ran, and nothing was chosen afterwards. Every result applies only where the product issued a VERIFIED forecast; excluded origins are counted with their reasons, and a descriptive selection check makes no claim (BC35; `docs/e6_results.md`).

**In plain English:** a year ahead, the model's forecasts were no more accurate than simply assuming last year's number again, and for operating cash flow they were worse, so FinAgent makes no forecasting-accuracy claim.

## E7: speed and failures (light)

| | |
|---|---|
| Question | Is it fast and cheap enough? (blueprint section 10.7, cut down by BC33; cost was not measured) |
| Baseline | A manual build of one company's three-statement model: about 20 hours, Vineet's estimate from experience, not measured (D10; `docs/issues_log.md`). |
| Split | 20 canonical E5 questions about validation companies, cold then warm, in one process on one machine (`docs/e7_light.md`). |
| Runs and commits | e7-0001 at 5eaac6c; e7-0002 (after BC37) at d94c889; both with uncommitted changes. |

| Measure | e7-0001 | e7-0002 | Source |
|---|---|---|---|
| Cold pass p50 / p95 / max (s) | 0.25 / 16.38 / 35.53 | 0.22 / 0.74 / 0.79 | `docs/e7_light.md`, `docs/e7_light_e7-0002.md` |
| Warm pass p50 / p95 (s) | 0.22 / 0.76 | 0.23 / 0.59 | `docs/e7_light.md`, `docs/e7_light_e7-0002.md` |
| Start, models loaded (s) | 2.02 | 5.57 | `docs/e7_light.md`, `docs/e7_light_e7-0002.md` |
| Failures | 0 of 40 calls | 0 of 40 calls | `docs/e7_light.md`, `docs/e7_light_e7-0002.md` |
| Peak memory (MB) | 870 | 1043 | `docs/e7_light.md`, `docs/e7_light_e7-0002.md` |

FinAgent's own build of one company from the filings (statements, three-scenario model, credit, risk cards and workbook) took a median of 36 s and at most 92 s in serving-0001 (`docs/issues_log.md`). serving-0002's shorter times read cached passage embeddings and are not the build time (#370).

**Decision:** none. e7-0002 shows the effect of BC37 (the Ask page reuses the serving run's statement sets when the build fingerprint matches); it changed no answer (#365, #366).

**Blind and post-hoc.** Not an accuracy measure; not pre-registered. One machine and twenty questions, so an indication only: no load test, no logging, no hosting cost (#318).

**In plain English:** after the first loading step, answers come back in under a second, and a full model of a company is built in about half a minute, against an estimated twenty hours by hand.

## E8: evidence replays

| | |
|---|---|
| Question | Does FinAgent return gaps, not invented numbers, when evidence is missing? (blueprint section 10.6) |
| Baseline | The same goals with complete evidence. |
| Split | Validation: 8 companies × goal kinds × 3 degraded views; 384 goals per view (`docs/e8_replays_0004.md`). |
| Runs and commits | e8-0001 at f122497; e8-0002 and e8-0003 at bb7e299; e8-0004 (confirmation after Phase 8) at 0b303e8; all with uncommitted changes. |

| Measure (e8-0004) | Value | Source |
|---|---|---|
| Degraded goals correct | 1150/1152 = 99.8% [99.6%, 100.0%] | `docs/e8_replays_0004.md` |
| Degraded goals safe (no wrong, stale or leaked value) | 1152/1152 = 100.0% | `docs/e8_replays_0004.md` |
| Fabricated, stale, leaked and new values | 0 each | `docs/e8_replays_0004.md` |
| Fallback to the other channel correct | 201/202 = 99.5% | `docs/e8_replays_0004.md` |
| Complete run: goals with a value; checked against the pinned runs | 374 of 384; 232 goals, 0 differences | `docs/e8_replays_0004.md` |

**Decision:** the blueprint target is 0 fabricated values (section 10.6). The first run, e8-0001, stopped with 5 fabricated values under the untagged view; after fixes (#147, #151, BC26), e8-0003 and e8-0004 have 0 (`eval/experiments.csv`).

**Blind and post-hoc.** e8-0001 is the blind first run. The fixes were made after seeing it on the same validation split, so e8-0003 and e8-0004 are not blind. In single-channel views the forecast, credit and liquidity goals are mostly correct refusals, so they are lightly exercised (#150).

**In plain English:** when a source of evidence was hidden, FinAgent either fell back to the other reading or said the number was missing; after the first test exposed a few made-up values and they were fixed, it invented none.

## Blueprint 4.2 checklist

| Checklist item | Status | Evidence |
|---|---|---|
| The system runs, and someone outside the team has used it | Not done | Runs locally: the Streamlit app (`finagent/ui/app.py`) serves all 48 pinned companies, each labelled by split (serving-0001, serving-0002; BC36, BC37; `docs/issues_log.md`). Outside user: not held (Vineet's decision, 2026-11-26; #377). What exists is the 20-minute session pack (65bf03b; `docs/outside_user_session.md`). |
| Evaluation results with a baseline | Done | E1 against M0 rules (e1-0005), E5 against the regex grammar (e5-0001), E6 against naive last value (e6-0001); also E2 without the agent, E3 against BM25 and E8 against complete evidence. E7's manual baseline is an estimate (D10), not a measurement. All in this file and `eval/experiments.csv`. |
| Deployed and served, with a dashboard of latency, cost and errors | Partly | Local only: no public URL, because Hugging Face Docker Spaces need a paid plan (#322); the Dockerfile ships unbuilt (#326); no FastAPI service (#316). The dashboard is the app's Results page: every evaluation's headline, E7 latency and failures (e7-0001, e7-0002) and the limitations. No hosting cost or error-rate monitoring beyond E7; no external AI API is called, so marginal API cost is zero by design (BC33). |
| Contribution log | Done | `docs/contribution_log.md`, brought up to date in this step. |
| Honest limitations with fix paths | Done | `docs/limitations.md`, final pass in this step; also shown on the Results page. |
| Demo recording as backup | Not done | To be recorded by Vineet (BC33); there is no recording in the repository. |
| Rehearsed and inside the time limit | Not done | A later Phase 10 step, before the final on 5 December 2026 (`docs/blueprint.md`). |

## Outside-user session

Not held (Vineet's decision, 2026-11-26; #377). The session will not be held before the final, so there is no outside-user result and no write-up. What exists is the 20-minute session pack, committed at 65bf03b (`docs/outside_user_session.md`), which can be run as it is (`docs/limitations.md`).
