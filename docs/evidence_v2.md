# Evidence v2 (D-EV; post-hoc)

**Post-hoc.** Evidence v2 was designed after E4 was scored (e4-0001: 19 of 27 quoted cards
unsupported by Vineet's labels). Its threshold is chosen on the E3 labels, which come from the
same six test companies whose cards E4 labels. Its results are reported **next to** e3-0001,
insights-0002 and e4-0001, never instead of them.

What v2 changes, and nothing else:

1. Card and explain quotes come from the **hybrid_rrf_rerank** retrieval variant instead of
   BM25. Within the reranked chunks, candidate sentences are ordered by the chunk's reranker
   rank first, then by the v1 sentence score, then by position. So the top quote comes from the
   best accepted chunk.
2. A **quote acceptance threshold** applies to the reranker (cross-encoder) score of a chunk.
   - Only chunks at or above the threshold may supply a quote.
   - If no chunk qualifies, the card or explanation is EVIDENCE_GAP. An honest gap beats a
     misleading quote.

Everything else in v1 is kept unchanged:

- The sentence rules:
  - whole sentences of 6 to 60 words (a longer sentence is skipped, never truncated, #173);
  - an anchor term;
  - not about an earlier year only;
  - the #185 AMOUNT_MISMATCH rule.
- The v1 sentence score threshold (`min_score` 0.30), and at most 2 quotes.
- The R10 model-derived label (#180).
- The gate's quote check against the pinned document (#157).

The switch is `evidence_version: v1 | v2` in `configs/signals.yaml`. v2 is the default, and v1
reproduces insights-0002 exactly.

## 1. Threshold rule (written 2026-11-25, before any curve, precision or coverage was computed)

The only E3 results known when this rule was written are e3-0001's published aggregates. Strict
MRR@5: hybrid_rrf_rerank 0.631, bm25 0.467. Lenient MRR@5: 0.725 and 0.677. No query's reranker
score had been joined to its label for this purpose. The parameters are in `configs/e3.yaml`
`quote_threshold`, and `python -m finagent.evaluation.quote_threshold` applies them.

**Data**

- **Unit:** one E3 query. There are 30: six test companies with 5 each, 18 signal queries and 12
  explain queries. They use the product's own queries, sections and 10-Ks.
- **Its score s:** the cross-encoder score of hybrid_rrf_rerank's rank-1 chunk
  (`gold/e3_pool_key.jsonl`). Reading the list to depth 5 for the pool does not change rank 1.
- **Its relevance:** Vineet's label of that chunk (`gold/e3_labels.jsonl`).
  - strict: label 2 ("explains the movement");
  - lenient: label 1 or more.
  - The Q19 better passage is outside the pool and does not change any chunk's label.
- **A threshold t** accepts a query when s >= t, so the top quote may be shown. Below t the
  answer is EVIDENCE_GAP.
- **Precision(t):** the share of accepted queries whose top chunk is relevant.
- **Coverage(t):** the share of queries accepted.

**Primary rule: cost-balanced utility on strict relevance (lambda = 1)**

- **Scoring:** each accepted query scores +1 if its top chunk is strictly relevant, and
  -lambda if it is not (a misleading quote). A rejected query (an honest gap) scores 0.
  - With lambda = 1, a misleading quote costs as much as a correct one gains. A quote is
    accepted when its chunk is more likely than not to explain the movement.
- **Choice:** on the training queries, sort by s, highest first. Consider every cut between
  distinct scores, plus accept-all and accept-none. Take the cut with the highest total score.
- **Ties:** the cut that accepts more queries.
- **The threshold value:** midway between the lowest accepted score and the highest rejected
  score. Accept-all gives minus infinity (no gate); accept-none gives plus infinity.

**Why this rule rather than the example rule.** The example rule is "the lowest threshold whose
precision is at least bm25's strict level". e3-0001 already puts the reranker's strict MRR@5
above bm25's, so the example rule would probably be met with no threshold at all and gate
nothing. It also tests a cumulative precision on about 25 queries, which is unstable at the
lowest threshold that meets it. The utility rule states directly that a misleading quote is
worse than a gap.

**Cross-validation:** leave one company out.

- There are six folds. In each, the rule is applied to the 25 queries of the other five
  companies. The resulting threshold is then applied to the left-out company's 5 queries.
- Reported, pooled over the folds: the cross-validated strict precision, the lenient precision
  (as a check) and the coverage of the accepted top chunks. Each fold's threshold is also
  reported.

**The cross-validated curve:** the same procedure for lambda = 0.25, 0.5, 1, 1.5, 2 and 3. Each
lambda gives one cross-validated (coverage, strict precision, lenient precision) point. **lambda
= 1 is the choice, fixed here.** The other points are reported only. Also reported, for
reference:

- the in-sample curve: precision and coverage at every cut over all 30 queries;
- bm25's top chunk at full coverage, strict and lenient precision at rank 1, which is the v1
  retrieval.

**Sensitivity (reported; it does not change the choice):**

- (a) The example rule: the lowest threshold whose strict precision on the training queries is
  at least bm25's strict precision at rank 1 on the same training queries.
- (b) The primary rule on lenient relevance.

**The shipped threshold:** the primary rule applied to all 30 queries, whatever the
cross-validation shows. There is no second choice.

- If the rule gives accept-all, v2 ships with no gate: hybrid_rrf_rerank only, and that is
  reported.
- If it gives accept-none, v2 would quote nothing. In that case the work stops and the result is
  reported for Vineet to decide.

**Known limits:**

- The labels judge chunks; the product quotes a sentence of the chunk. If the rank-1 chunk has
  no sentence that passes the rules, the top quote comes from the next accepted chunk.
- With 30 queries the threshold is coarse.
- Because the threshold is chosen on the same six companies whose E4 cards are re-scored,
  e4-0002 is optimistic (section 4).

## 2. The threshold (e3-0002; full tables in docs/e3_threshold_0002.md)

`python -m finagent.evaluation.quote_threshold --run-id e3-0002 --record` was run once by the
rule above.

| top chunk of each E3 query (30) | coverage | strict precision | lenient precision |
|---|---|---|---|
| bm25, no threshold (the v1 retrieval) | 1.000 (30/30) | 0.133 (4/30) | 0.400 (12/30) |
| hybrid_rrf_rerank, no threshold | 1.000 (30/30) | 0.233 (7/30) | 0.400 (12/30) |
| **v2, leave-one-company-out (cross-validated)** | **0.267 (8/30)** | **0.625 (5/8)** | **0.625 (5/8)** |
| v2, shipped threshold on all 30 (in-sample) | 0.200 (6/30) | 0.833 (5/6) | 0.833 (5/6) |

- **Shipped threshold: 5.263** (midway between the scores 5.712 and 4.814). It is in
  configs/signals.yaml `versions.v2.min_rerank_score`. The rule gave neither accept-all nor
  accept-none, so nothing stopped.
- **Folds:** the threshold chosen on the other five companies was 5.263 for CVX, T and CL;
  4.995 for NKE; 5.427 for MRK; and 3.886 for ABT.
  - ABT's fold accepted 2 queries, neither relevant.
  - CVX and CL had no query above their threshold.
- **The cross-validated curve** (cost of a misleading quote, as coverage → strict precision):
  - 0.25: 0.500 → 0.333
  - 0.5: 0.300 → 0.556
  - **1 (the choice): 0.267 → 0.625**
  - 1.5, 2 and 3: 0.133 → 0.750
- **Sensitivity:**
  - (a) The example rule (bm25's strict level as the target) gives accept-all, so it gates
    nothing, as section 1 expected. Its cross-validated strict precision is 0.233.
  - (b) The primary rule on lenient relevance gives 3.271, with cross-validated coverage 0.400
    and strict precision 0.417.
- **What this means for the cards.**
  - Only 2 of the 18 signal queries pass the threshold, both R4 weak cash conversion (E3-Q08 NKE
    FY2025 and E3-Q11 MRK FY2025). The other 4 accepted queries are explain queries.
  - So under v2 most signal cards become EVIDENCE_GAP: "the filing does not explain the
    movement", with the passage's score in the detail.
  - On these labels, the reranker's top passage for a signal query rarely explains the movement
    (strict 2 of 18 at rank 1 with no threshold).
- **Small sample:** 8 accepted queries out of sample. The precision interval is wide; with 5 of
  8, a 95% Wilson interval is about 0.31 to 0.86.

<!-- generated by python -m finagent.evaluation.e4 doc --evidence v2; text below is replaced -->

## 3. insights-0003 (v2) against insights-0002 (v1)

Every card of each run (all fiscal years). Quoted = VERIFIED with a quote; R10 model-derived cards are counted apart. Changes are over the cards present in both runs.

| company | quoted v1 → v2 | EVIDENCE_GAP v1 → v2 | R10 model | same quotes | other quotes | quoted → gap | gap → quoted |
|---|---|---|---|---|---|---|---|
| AMZN | 3 → 0 | 4 → 7 | 1 → 1 | 0 | 0 | 3 | 0 |
| MSFT | 5 → 0 | 4 → 9 | 0 → 0 | 0 | 0 | 5 | 0 |
| CVX | 7 → 1 | 2 → 8 | 1 → 1 | 0 | 1 | 6 | 0 |
| NKE | 5 → 1 | 0 → 4 | 0 → 0 | 1 | 0 | 4 | 0 |
| MRK | 7 → 2 | 1 → 6 | 0 → 0 | 2 | 0 | 5 | 0 |
| T | 13 → 0 | 2 → 15 | 0 → 0 | 0 | 0 | 13 | 0 |
| CL | 8 → 0 | 0 → 8 | 1 → 1 | 0 | 0 | 8 | 0 |
| ABT | 3 → 0 | 2 → 5 | 0 → 0 | 0 | 0 | 3 | 0 |
| **total** | 51 → 4 | 15 → 62 | 3 → 3 | 3 | 1 | 47 | 0 |

## 4. The 27 E4 slots under v2

- Unchanged quotes (v1 labels carry over): **2**.
- Changed quotes (on the v2 sheet): **0**.
- Now EVIDENCE_GAP (listed, not labelled): **25**.
- Sheet `eval/e4_labels_v2.xlsx`: 0 cards, about 0 minutes at 75 s per card. Same instructions and worked examples as `eval/e4_labels.xlsx`. Judge key (hidden): `gold/e4_judge_key_v2.jsonl`.

| slot | company | FY | signal | under v2 |
|---|---|---|---|---|
| E4-C01 | AMZN | 2025 | R3 | EVIDENCE_GAP |
| E4-C02 | AMZN | 2025 | R4 | EVIDENCE_GAP |
| E4-C03 | MSFT | 2026 | R2 | EVIDENCE_GAP |
| E4-C04 | MSFT | 2026 | R3 | EVIDENCE_GAP |
| E4-C05 | MSFT | 2026 | R4 | EVIDENCE_GAP |
| E4-C06 | CVX | 2025 | R2 | EVIDENCE_GAP |
| E4-C07 | CVX | 2025 | R8 | EVIDENCE_GAP |
| E4-C08 | CVX | 2024 | R3 | EVIDENCE_GAP |
| E4-C09 | NKE | 2026 | R1 | EVIDENCE_GAP |
| E4-C10 | NKE | 2025 | R1 | EVIDENCE_GAP |
| E4-C11 | NKE | 2025 | R4 | unchanged |
| E4-C12 | NKE | 2025 | R5 | EVIDENCE_GAP |
| E4-C13 | NKE | 2025 | R8 | EVIDENCE_GAP |
| E4-C14 | MRK | 2025 | R1 | EVIDENCE_GAP |
| E4-C15 | MRK | 2025 | R4 | unchanged |
| E4-C16 | MRK | 2024 | R2 | EVIDENCE_GAP |
| E4-C17 | T | 2025 | R5 | EVIDENCE_GAP |
| E4-C18 | T | 2025 | R6 | EVIDENCE_GAP |
| E4-C19 | T | 2024 | R5 | EVIDENCE_GAP |
| E4-C20 | T | 2024 | R6 | EVIDENCE_GAP |
| E4-C21 | T | 2024 | R8 | EVIDENCE_GAP |
| E4-C22 | CL | 2025 | R7 | EVIDENCE_GAP |
| E4-C23 | CL | 2025 | R8 | EVIDENCE_GAP |
| E4-C24 | CL | 2024 | R7 | EVIDENCE_GAP |
| E4-C25 | ABT | 2023 | R2 | EVIDENCE_GAP |
| E4-C26 | ABT | 2023 | R4 | EVIDENCE_GAP |
| E4-C27 | ABT | 2023 | R8 | EVIDENCE_GAP |
