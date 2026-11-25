# Insights with evidence v2 (post-hoc, D-EV): risk signals and insight cards, with low-confidence sentences (D-EV2)

Run `insights-0004`; git `b877923` (dirty); evidence manifest sha256 `89ee41619200`; as of 2026-11-24; signals.yaml sha256 `60962e4e422b`; query templates sha256 `4420e0ac6f19`; quote threshold 0.3 (provisional, chosen on the dev companies only).

## Method

Signals: Appendix C.2 thresholds as written (D5), on the validated statement set's accepted inputs (the credit ratios' inputs); R5 = funded debt / (EBIT + D&A from the cash flow statement) (D2); R10 on the Moderate scenario from the latest 10-K (D3); R2 and R9 GAP NO_COGS without a cost-of-sales line (D4); severity R7, R10, R11 high, the others medium (D5); D&A falls back to depreciation + amortisation when both are read, else GAP (#179). A missing input is never read as not fired. Every fired measure is recomputed a second way in Decimal by the risk tool.

Evidence (v2, post-hoc, D-EV; docs/evidence_v2.md): the signal's frozen query, searched with hybrid_rrf_rerank over the text of Item 7 and Item 8 with its notes of the same 10-K. Only passages whose cross-encoder score is at least 5.263 may supply a quote. That threshold was chosen by a rule written before the curve, leave one company out on the E3 labels (e3-0002). Sentences are taken in the passages' rank order and must be whole sentences of 6 to 60 words (longer ones skipped, #173), with an anchor term, not about an earlier year only, with at least the IDF score threshold. The best two are quoted, otherwise the card is EVIDENCE_GAP. The planner's gate rebuilds every quoted chunk from the pinned document (#157). An R10 card is model-derived: no search, no quote, labelled 'model-derived; no filing evidence expected' (#180). D-EV2 (Vineet, option b): an EVIDENCE_GAP card also shows, labelled 'Low confidence: may not explain the movement', the sentence v2 would have quoted first without the reranker gate: the first sentence, in the passages' rank order, of a passage below the threshold that passes every sentence rule and the IDF score threshold. It is not evidence: the card stays EVIDENCE_GAP, the gate never checks or counts it, and explain answers never use it.

## Summary

Signal evaluations (every company, year and signal): FIRED 69, NOT_FIRED 223, GAP 116, NOT_APPLIED 32.
Cards (every year): 69, of which quoted 4, EVIDENCE_GAP 62 and model-derived (no filing evidence expected, #180) 3. Latest year: 20 cards, 1 quoted, 16 EVIDENCE_GAP, 3 model-derived.
Low-confidence sentences (D-EV2; shown on EVIDENCE_GAP cards, never evidence): 42 of 62 EVIDENCE_GAP cards (latest year 12 of 16). The others have no passage below the threshold with a sentence that passes the rules.

## AMZN

Signals per year (**F** fired, - not fired, gap = not computable, n/a = not applicable):

| Year | R1 | R2 | R3 | R4 | R5 | R6 | R7 | R8 | R9 | R10 | R11 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| FY2021 | gap | gap | gap | gap | gap | - | - | gap | gap | n/a | gap |
| FY2022 | **F** | gap | gap | - | - | - | **F** | - | gap | n/a | - |
| FY2023 | **F** | - | gap | - | - | - | - | - | - | n/a | - |
| FY2024 | - | - | **F** | - | - | - | - | - | - | n/a | - |
| FY2025 | - | - | **F** | **F** | - | - | - | - | **F** | **F** | - |

### AMZN FY2025 (latest 10-K)

- **R3 Capex intensity spike** (medium; EVIDENCE_GAP): capex to revenue up +5.4 pp (test > +2.0 pp; FY2024 13.01, FY2025 18.39). EVIDENCE_GAP: the filing does not explain the movement (no sentence above the relevance threshold). Low confidence: may not explain the movement (ITEM_7, chunk 0001018724-26-000004:ITEM_7:0016; passage reranker score 3.650, below the threshold 5.263; not evidence): "Cash provided by (used in) investing activities corresponds with cash capital expenditures, including leasehold improvements, incentives received from property and equipment vendors, proceeds from asset sales, cash outlays for acquisitions, investments in other companies and intellectual property rights, and purchases, sales, and maturities of marketable securities."
  - EVIDENCE_GAP (no candidate sentence; 3 passage(s) below the reranker threshold 5.263 (best 3.650))
  - Low confidence: may not explain the movement (ITEM_7, chunk 0001018724-26-000004:ITEM_7:0016; passage reranker score 3.650, below the threshold 5.263; not evidence): "Cash provided by (used in) investing activities corresponds with cash capital expenditures, including leasehold improvements, incentives received from property and equipment vendors, proceeds from asset sales, cash outlays for acquisitions, investments in other companies and intellectual property rights, and purchases, sales, and maturities of marketable securities."
- **R4 Weak cash conversion** (medium; EVIDENCE_GAP): FCF margin down -4.1 pp (test < -3.0 pp; FY2024 5.154, FY2025 1.073). EVIDENCE_GAP: the filing does not explain the movement (no sentence above the relevance threshold). Low confidence: may not explain the movement (ITEM_7, chunk 0001018724-26-000004:ITEM_7:0015; passage reranker score 2.862, below the threshold 5.263; not evidence): "The increase in operating cash flow in 2025, compared to the prior year, was due to an increase in net income (loss), excluding non-cash expenses, and changes in working capital."
  - EVIDENCE_GAP (no candidate sentence; 3 passage(s) below the reranker threshold 5.263 (best 2.862))
  - Low confidence: may not explain the movement (ITEM_7, chunk 0001018724-26-000004:ITEM_7:0015; passage reranker score 2.862, below the threshold 5.263; not evidence): "The increase in operating cash flow in 2025, compared to the prior year, was due to an increase in net income (loss), excluding non-cash expenses, and changes in working capital."
- **R9 Payables stretch** (medium; EVIDENCE_GAP): DPO up +10.2 days (test > +10.0 days; FY2024 100.6, FY2025 110.7). EVIDENCE_GAP: the filing does not explain the movement (no sentence above the relevance threshold).
  - EVIDENCE_GAP (no candidate sentence; 3 passage(s) below the reranker threshold 5.263 (best -2.873))
- **R10 Scenario cash shortfall** (high; VERIFIED): largest modelled cash shortfall in a Moderate year USD 36,898.0m (test > USD 0.0m; FY2026 USD 0.0m, FY2027 USD 0.0m, FY2028 USD 36,898.0m; Moderate scenario from FY2025; largest in FY2028). Evidence: model-derived; no filing evidence expected.
  - Evidence: model-derived; no filing evidence expected (#180)

Not fired or not computable: R1 NOT_FIRED; R2 NOT_FIRED; R5 NOT_FIRED; R6 NOT_FIRED; R7 NOT_FIRED; R8 NOT_FIRED; R11 NOT_FIRED.

Planner answer: VERIFIED SIGNALS_EVALUATED; gate decisions: signals_fired accepted, R3.capex_to_revenue_change accepted, R3.capex_to_revenue_vs_median accepted, R4.fcf_margin_change accepted, R4.cfo_to_net_income_two_years accepted, R9.dpo_change accepted, R10.moderate_shortfall_max accepted.

## MSFT

Signals per year (**F** fired, - not fired, gap = not computable, n/a = not applicable):

| Year | R1 | R2 | R3 | R4 | R5 | R6 | R7 | R8 | R9 | R10 | R11 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| FY2022 | gap | gap | gap | gap | gap | - | - | gap | gap | n/a | gap |
| FY2023 | gap | gap | gap | **F** | - | - | - | - | gap | n/a | gap |
| FY2024 | - | - | **F** | - | - | - | - | - | - | n/a | - |
| FY2025 | - | - | **F** | **F** | - | - | - | - | - | n/a | - |
| FY2026 | - | **F** | **F** | **F** | - | - | - | - | **F** | - | **F** |

### MSFT FY2026 (latest 10-K)

- **R2 Inventory build** (medium; EVIDENCE_GAP): inventory growth minus COGS growth +27.8 pp (test > +10.0 pp; inventory +48.9%, cogs +21.1%). EVIDENCE_GAP: the filing does not explain the movement (no sentence above the relevance threshold). Low confidence: may not explain the movement (ITEM_7, chunk 0001193125-26-323660:ITEM_7:0019; passage reranker score -8.218, below the threshold 5.263; not evidence): "Windows OEM and Devices revenue decreased slightly driven by a decline in Devices, offset in part by Windows OEM growth of 5% with inventory levels that remained elevated."
  - EVIDENCE_GAP (no candidate sentence; 3 passage(s) below the reranker threshold 5.263 (best -6.214))
  - Low confidence: may not explain the movement (ITEM_7, chunk 0001193125-26-323660:ITEM_7:0019; passage reranker score -8.218, below the threshold 5.263; not evidence): "Windows OEM and Devices revenue decreased slightly driven by a decline in Devices, offset in part by Windows OEM growth of 5% with inventory levels that remained elevated."
- **R3 Capex intensity spike** (medium; EVIDENCE_GAP): capex to revenue up +12.0 pp (test > +2.0 pp; FY2025 22.91, FY2026 34.94); capex to revenue over its three-year median 1.93x (test > 1.50x; FY2023 0.1326, FY2024 0.1814, FY2025 0.2291, FY2026 0.3494; 3-year median 0.1814). EVIDENCE_GAP: the filing does not explain the movement (no sentence above the relevance threshold). Low confidence: may not explain the movement (ITEM_7, chunk 0001193125-26-323660:ITEM_7:0044; passage reranker score -0.197, below the threshold 5.263; not evidence): "We will continue to invest in capital expenditures to support growth in our cloud offerings and our investments in AI training and other infrastructure."
  - EVIDENCE_GAP (no candidate sentence; 3 passage(s) below the reranker threshold 5.263 (best -0.197))
  - Low confidence: may not explain the movement (ITEM_7, chunk 0001193125-26-323660:ITEM_7:0044; passage reranker score -0.197, below the threshold 5.263; not evidence): "We will continue to invest in capital expenditures to support growth in our cloud offerings and our investments in AI training and other infrastructure."
- **R4 Weak cash conversion** (medium; EVIDENCE_GAP): FCF margin down -5.2 pp (test < -3.0 pp; FY2025 25.42, FY2026 20.19). EVIDENCE_GAP: the filing does not explain the movement (no sentence above the relevance threshold). Low confidence: may not explain the movement (ITEM_7, chunk 0001193125-26-323660:ITEM_7:0034; passage reranker score 0.120, below the threshold 5.263; not evidence): "We expect existing cash, cash equivalents, short-term investments, cash flows from operations, and access to capital markets to continue to be sufficient to fund our operating activities and cash commitments for investing and financing activities, such as dividends, share repurchases, debt maturities, and material capital expenditures, for at least the next 12 months and thereafter for the foreseeable future."
  - EVIDENCE_GAP (no candidate sentence; 3 passage(s) below the reranker threshold 5.263 (best 0.120))
  - Low confidence: may not explain the movement (ITEM_7, chunk 0001193125-26-323660:ITEM_7:0034; passage reranker score 0.120, below the threshold 5.263; not evidence): "We expect existing cash, cash equivalents, short-term investments, cash flows from operations, and access to capital markets to continue to be sufficient to fund our operating activities and cash commitments for investing and financing activities, such as dividends, share repurchases, debt maturities, and material capital expenditures, for at least the next 12 months and thereafter for the foreseeable future."
- **R9 Payables stretch** (medium; EVIDENCE_GAP): DPO up +17.0 days (test > +10.0 days; FY2025 103.3, FY2026 120.3). EVIDENCE_GAP: the filing does not explain the movement (no sentence above the relevance threshold).
  - EVIDENCE_GAP (no candidate sentence; 3 passage(s) below the reranker threshold 5.263 (best -4.090))
- **R11 Operating and investing cash pressure (composite)** (high; EVIDENCE_GAP): revenue growth +17.8 pp (test > +0.0 pp; revenue +17.8%); components fired: R2, R3, R4. EVIDENCE_GAP: the filing does not explain the movement (no sentence above the relevance threshold).
  - EVIDENCE_GAP (no candidate sentence; 3 passage(s) below the reranker threshold 5.263 (best -2.026))

Not fired or not computable: R1 NOT_FIRED; R5 NOT_FIRED; R6 NOT_FIRED; R7 NOT_FIRED; R8 NOT_FIRED; R10 NOT_FIRED.

Planner answer: VERIFIED SIGNALS_EVALUATED; gate decisions: signals_fired accepted, R2.inventory_growth_gap accepted, R2.dio_change accepted, R3.capex_to_revenue_change accepted, R3.capex_to_revenue_vs_median accepted, R4.fcf_margin_change accepted, R4.cfo_to_net_income_two_years accepted, R9.dpo_change accepted, R11.revenue_growth accepted.

## CVX

Signals per year (**F** fired, - not fired, gap = not computable, n/a = not applicable):

| Year | R1 | R2 | R3 | R4 | R5 | R6 | R7 | R8 | R9 | R10 | R11 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| FY2021 | gap | gap | gap | gap | gap | - | - | gap | gap | n/a | gap |
| FY2022 | gap | gap | gap | - | - | - | - | - | gap | n/a | - |
| FY2023 | **F** | **F** | **F** | **F** | - | - | - | **F** | **F** | n/a | - |
| FY2024 | - | - | **F** | - | - | - | - | - | - | n/a | - |
| FY2025 | - | **F** | - | - | - | - | - | **F** | - | **F** | - |

### CVX FY2025 (latest 10-K)

- **R2 Inventory build** (medium; EVIDENCE_GAP): inventory growth minus COGS growth +16.2 pp (test > +10.0 pp; inventory +7.0%, cogs -9.2%). EVIDENCE_GAP: the filing does not explain the movement (no sentence above the relevance threshold). Low confidence: may not explain the movement (ITEM_7, chunk 0000093410-26-000078:ITEM_7:0024; passage reranker score -5.343, below the threshold 5.263; not evidence): "Industry margins can also be influenced by inventory levels, geopolitical events, costs of materials and services, refinery or chemical plant capacity utilization, maintenance programs, and disruptions at refineries or chemical plants resulting from unplanned outages due to severe weather, fires or other operational events."
  - EVIDENCE_GAP (no candidate sentence; 3 passage(s) below the reranker threshold 5.263 (best -5.343))
  - Low confidence: may not explain the movement (ITEM_7, chunk 0000093410-26-000078:ITEM_7:0024; passage reranker score -5.343, below the threshold 5.263; not evidence): "Industry margins can also be influenced by inventory levels, geopolitical events, costs of materials and services, refinery or chemical plant capacity utilization, maintenance programs, and disruptions at refineries or chemical plants resulting from unplanned outages due to severe weather, fires or other operational events."
- **R8 Margin compression** (medium; EVIDENCE_GAP): operating (EBIT) margin down -3.2 pp (test < -3.0 pp; FY2024 14.53, FY2025 11.36). EVIDENCE_GAP: the filing does not explain the movement (no sentence above the relevance threshold). Low confidence: may not explain the movement (ITEM_8, chunk 0000093410-26-000078:ITEM_8:0022; passage reranker score 0.074, below the threshold 5.263; not evidence): "The decrease in income before taxes for the company was primarily the result of higher upstream depreciation, depletion and amortization, lower upstream realizations, unfavorable foreign exchange impacts, and higher operating costs, partially offset by higher upstream sales volumes and higher downstream margins."
  - EVIDENCE_GAP (no candidate sentence; 3 passage(s) below the reranker threshold 5.263 (best 0.074))
  - Low confidence: may not explain the movement (ITEM_8, chunk 0000093410-26-000078:ITEM_8:0022; passage reranker score 0.074, below the threshold 5.263; not evidence): "The decrease in income before taxes for the company was primarily the result of higher upstream depreciation, depletion and amortization, lower upstream realizations, unfavorable foreign exchange impacts, and higher operating costs, partially offset by higher upstream sales volumes and higher downstream margins."
- **R10 Scenario cash shortfall** (high; VERIFIED): largest modelled cash shortfall in a Moderate year USD 27,647.5m (test > USD 0.0m; FY2026 USD 7,154.6m, FY2027 USD 18,043.6m, FY2028 USD 27,647.5m; Moderate scenario from FY2025; largest in FY2028). Evidence: model-derived; no filing evidence expected.
  - Evidence: model-derived; no filing evidence expected (#180)

Not fired or not computable: R1 NOT_FIRED; R3 NOT_FIRED; R4 NOT_FIRED; R5 NOT_FIRED; R6 NOT_FIRED; R7 NOT_FIRED; R9 NOT_FIRED; R11 NOT_FIRED.

Planner answer: VERIFIED SIGNALS_EVALUATED; gate decisions: signals_fired accepted, R2.inventory_growth_gap accepted, R2.dio_change accepted, R8.ebit_margin_change accepted, R10.moderate_shortfall_max accepted.

## NKE

Signals per year (**F** fired, - not fired, gap = not computable, n/a = not applicable):

| Year | R1 | R2 | R3 | R4 | R5 | R6 | R7 | R8 | R9 | R10 | R11 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| FY2022 | gap | gap | gap | gap | gap | gap | - | gap | gap | n/a | gap |
| FY2023 | gap | gap | gap | - | gap | gap | - | - | gap | n/a | - |
| FY2024 | - | - | gap | - | - | gap | - | - | - | n/a | - |
| FY2025 | **F** | - | - | **F** | **F** | gap | - | **F** | - | n/a | - |
| FY2026 | **F** | - | - | - | - | gap | - | - | - | - | - |

### NKE FY2026 (latest 10-K)

- **R1 Receivables outpacing sales** (medium; EVIDENCE_GAP): DSO up +5.8 days (test > +5.0 days; FY2025 36.04, FY2026 41.88); receivables growth minus revenue growth +25.5 pp (test > +10.0 pp; receivables +25.7%, revenue +0.2%). EVIDENCE_GAP: the filing does not explain the movement (no sentence above the relevance threshold). Low confidence: may not explain the movement (ITEM_7, chunk 0000320187-26-000088:ITEM_7:0063; passage reranker score -0.703, below the threshold 5.263; not evidence): "The increase in Accounts receivable was primarily due to the timing of wholesale shipments and the increase in Prepaid expenses and other current assets was primarily due to prepayments related to endorsement contracts."
  - EVIDENCE_GAP (no candidate sentence; 3 passage(s) below the reranker threshold 5.263 (best -0.703))
  - Low confidence: may not explain the movement (ITEM_7, chunk 0000320187-26-000088:ITEM_7:0063; passage reranker score -0.703, below the threshold 5.263; not evidence): "The increase in Accounts receivable was primarily due to the timing of wholesale shipments and the increase in Prepaid expenses and other current assets was primarily due to prepayments related to endorsement contracts."

Not fired or not computable: R2 NOT_FIRED; R3 NOT_FIRED; R4 NOT_FIRED; R5 NOT_FIRED; R6 GAP (INPUT_GAP); R7 NOT_FIRED; R8 NOT_FIRED; R9 NOT_FIRED; R10 NOT_FIRED; R11 NOT_FIRED.

Planner answer: VERIFIED SIGNALS_EVALUATED; gate decisions: signals_fired accepted, R1.dso_change accepted, R1.receivables_growth_gap accepted.

## MRK

Signals per year (**F** fired, - not fired, gap = not computable, n/a = not applicable):

| Year | R1 | R2 | R3 | R4 | R5 | R6 | R7 | R8 | R9 | R10 | R11 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| FY2021 | gap | gap | gap | gap | gap | - | - | gap | gap | n/a | gap |
| FY2022 | gap | gap | gap | gap | - | - | - | - | gap | n/a | gap |
| FY2023 | - | **F** | gap | **F** | **F** | **F** | - | **F** | - | n/a | gap |
| FY2024 | - | **F** | - | - | - | - | - | - | - | n/a | - |
| FY2025 | **F** | - | - | **F** | - | - | - | - | - | - | - |

### MRK FY2025 (latest 10-K)

- **R1 Receivables outpacing sales** (medium; EVIDENCE_GAP): receivables growth minus revenue growth +13.3 pp (test > +10.0 pp; receivables +14.6%, revenue +1.3%). EVIDENCE_GAP: the filing does not explain the movement (no sentence above the relevance threshold). Low confidence: may not explain the movement (ITEM_8/NOTE_06, chunk 0000310158-26-000063:ITEM_8/NOTE_06:0030; passage reranker score 1.087, below the threshold 5.263; not evidence): "In certain of these factoring arrangements, for ease of administration, the Company will collect customer payments related to the factored receivables, which it then remits to the financial institutions, generally within thirty days after receipt."
  - EVIDENCE_GAP (no candidate sentence; 3 passage(s) below the reranker threshold 5.263 (best 1.087))
  - Low confidence: may not explain the movement (ITEM_8/NOTE_06, chunk 0000310158-26-000063:ITEM_8/NOTE_06:0030; passage reranker score 1.087, below the threshold 5.263; not evidence): "In certain of these factoring arrangements, for ease of administration, the Company will collect customer payments related to the factored receivables, which it then remits to the financial institutions, generally within thirty days after receipt."
- **R4 Weak cash conversion** (medium; VERIFIED): FCF margin down -9.2 pp (test < -3.0 pp; FY2024 28.2, FY2025 19.01). Evidence: quoted below.
  - Quote (ITEM_7, score 0.41, `0000310158-26-000063:ITEM_7:0098`): "Cash provided by operating activities continues to be the Company’s primary source of funds to finance operating needs, with excess cash serving as the primary source of funds to finance business development transactions, capital expenditures, dividends paid to shareholders and treasury stock purchases."

Not fired or not computable: R2 NOT_FIRED; R3 NOT_FIRED; R5 NOT_FIRED; R6 NOT_FIRED; R7 NOT_FIRED; R8 NOT_FIRED; R9 NOT_FIRED; R10 NOT_FIRED; R11 NOT_FIRED.

Planner answer: VERIFIED SIGNALS_EVALUATED; gate decisions: signals_fired accepted, R1.receivables_growth_gap accepted, R1.dso_change accepted, R4.fcf_margin_change accepted, R4.cfo_to_net_income_two_years accepted.

## T

Signals per year (**F** fired, - not fired, gap = not computable, n/a = not applicable):

| Year | R1 | R2 | R3 | R4 | R5 | R6 | R7 | R8 | R9 | R10 | R11 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| FY2021 | gap | gap | gap | gap | **F** | **F** | - | gap | gap | n/a | gap |
| FY2022 | gap | gap | **F** | gap | **F** | **F** | **F** | **F** | gap | n/a | - |
| FY2023 | - | gap | gap | gap | **F** | **F** | - | - | gap | n/a | gap |
| FY2024 | - | gap | - | - | **F** | **F** | **F** | **F** | gap | n/a | - |
| FY2025 | - | gap | - | - | **F** | **F** | - | - | gap | - | - |

### T FY2025 (latest 10-K)

- **R5 Rising leverage** (medium; EVIDENCE_GAP): funded debt to (EBIT + D&A) 3.02x (test > 3.00x; FY2025 3.021). EVIDENCE_GAP: the filing does not explain the movement (no sentence above the relevance threshold). Low confidence: may not explain the movement (ITEM_7, chunk 0000732717-26-000120:ITEM_7:0063; passage reranker score 3.626, below the threshold 5.263; not evidence): "We had no outstanding commercial paper borrowings or other short-term borrowings on December 31, 2025."
  - EVIDENCE_GAP (no candidate sentence; 3 passage(s) below the reranker threshold 5.263 (best 3.626))
  - Low confidence: may not explain the movement (ITEM_7, chunk 0000732717-26-000120:ITEM_7:0063; passage reranker score 3.626, below the threshold 5.263; not evidence): "We had no outstanding commercial paper borrowings or other short-term borrowings on December 31, 2025."
- **R6 Thin interest cover** (medium; EVIDENCE_GAP): EBIT to gross interest 3.55x (test < 4.00x; FY2025 3.551). EVIDENCE_GAP: the filing does not explain the movement (no sentence above the relevance threshold). Low confidence: may not explain the movement (ITEM_8/NOTE_18, chunk 0000732717-26-000120:ITEM_8/NOTE_18:0001; passage reranker score 1.917, below the threshold 5.263; not evidence): "The financing obligation is increased by interest expense and estimated future net cash flows generated and retained by Crown Castle from operation of the tower sites, and reduced by our contractual payments."
  - EVIDENCE_GAP (no candidate sentence; 3 passage(s) below the reranker threshold 5.263 (best 1.917))
  - Low confidence: may not explain the movement (ITEM_8/NOTE_18, chunk 0000732717-26-000120:ITEM_8/NOTE_18:0001; passage reranker score 1.917, below the threshold 5.263; not evidence): "The financing obligation is increased by interest expense and estimated future net cash flows generated and retained by Crown Castle from operation of the tower sites, and reduced by our contractual payments."

Not fired or not computable: R1 NOT_FIRED; R2 GAP (INPUT_GAP); R3 NOT_FIRED; R4 NOT_FIRED; R7 NOT_FIRED; R8 NOT_FIRED; R9 GAP (INPUT_GAP); R10 NOT_FIRED; R11 NOT_FIRED.

Planner answer: VERIFIED SIGNALS_EVALUATED; gate decisions: signals_fired accepted, R5.funded_debt_to_ebitda accepted, R5.funded_debt_to_ebitda_change accepted, R6.ebit_to_gross_interest accepted.

## CL

Signals per year (**F** fired, - not fired, gap = not computable, n/a = not applicable):

| Year | R1 | R2 | R3 | R4 | R5 | R6 | R7 | R8 | R9 | R10 | R11 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| FY2021 | gap | gap | gap | gap | gap | - | - | gap | gap | n/a | gap |
| FY2022 | **F** | **F** | gap | **F** | **F** | - | - | **F** | gap | n/a | gap |
| FY2023 | - | - | gap | - | - | - | - | - | - | n/a | - |
| FY2024 | - | - | - | - | - | - | **F** | - | - | n/a | - |
| FY2025 | - | - | - | - | - | - | **F** | **F** | - | **F** | - |

### CL FY2025 (latest 10-K)

- **R7 Tight liquidity** (high; EVIDENCE_GAP): current ratio 0.83x (test < 1.00x; FY2025 0.8331); current ratio falling -0.09x (test < +0.00x; FY2024 0.9233, FY2025 0.8331). EVIDENCE_GAP: the filing does not explain the movement (no sentence above the relevance threshold). Low confidence: may not explain the movement (ITEM_7, chunk 0000021665-26-000006:ITEM_7:0096; passage reranker score -0.550, below the threshold 5.263; not evidence): "The Company defines working capital as the difference between current assets (excluding Cash and cash equivalents and marketable securities, the latter of which is reported in Other current assets) and current liabilities (excluding short-term debt)."
  - EVIDENCE_GAP (no candidate sentence; 3 passage(s) below the reranker threshold 5.263 (best 2.124))
  - Low confidence: may not explain the movement (ITEM_7, chunk 0000021665-26-000006:ITEM_7:0096; passage reranker score -0.550, below the threshold 5.263; not evidence): "The Company defines working capital as the difference between current assets (excluding Cash and cash equivalents and marketable securities, the latter of which is reported in Other current assets) and current liabilities (excluding short-term debt)."
- **R8 Margin compression** (medium; EVIDENCE_GAP): operating (EBIT) margin down -5.0 pp (test < -3.0 pp; FY2024 21.23, FY2025 16.22). EVIDENCE_GAP: the filing does not explain the movement (no sentence above the relevance threshold).
  - EVIDENCE_GAP (no candidate sentence; 3 passage(s) below the reranker threshold 5.263 (best 3.859))
- **R10 Scenario cash shortfall** (high; VERIFIED): largest modelled cash shortfall in a Moderate year USD 3,347.9m (test > USD 0.0m; FY2026 USD 875.7m, FY2027 USD 2,066.5m, FY2028 USD 3,347.9m; Moderate scenario from FY2025; largest in FY2028). Evidence: model-derived; no filing evidence expected.
  - Evidence: model-derived; no filing evidence expected (#180)

Not fired or not computable: R1 NOT_FIRED; R2 NOT_FIRED; R3 NOT_FIRED; R4 NOT_FIRED; R5 NOT_FIRED; R6 NOT_FIRED; R9 NOT_FIRED; R11 NOT_FIRED.

Planner answer: VERIFIED SIGNALS_EVALUATED; gate decisions: signals_fired accepted, R7.current_ratio accepted, R7.current_ratio_change accepted, R8.ebit_margin_change accepted, R10.moderate_shortfall_max accepted.

## ABT

Signals per year (**F** fired, - not fired, gap = not computable, n/a = not applicable):

| Year | R1 | R2 | R3 | R4 | R5 | R6 | R7 | R8 | R9 | R10 | R11 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| FY2021 | gap | gap | gap | gap | gap | - | - | gap | gap | n/a | gap |
| FY2022 | gap | **F** | gap | - | - | - | - | - | gap | n/a | - |
| FY2023 | **F** | **F** | gap | **F** | - | - | - | **F** | - | n/a | - |
| FY2024 | - | - | - | - | - | - | - | - | - | n/a | - |
| FY2025 | - | - | - | - | - | - | - | - | - | - | - |

### ABT FY2025 (latest 10-K)

No signal fired.

Not fired or not computable: R1 NOT_FIRED; R2 NOT_FIRED; R3 NOT_FIRED; R4 NOT_FIRED; R5 NOT_FIRED; R6 NOT_FIRED; R7 NOT_FIRED; R8 NOT_FIRED; R9 NOT_FIRED; R10 NOT_FIRED; R11 NOT_FIRED.

Planner answer: VERIFIED SIGNALS_EVALUATED; gate decisions: signals_fired accepted.

## Explain answers (dev companies only: AMZN, MSFT; D10)

**Why did Amazon's free cash flow fall in FY2025?** (EVIDENCE_GAP; compiler m1-intent-tfidf-lr-v3)

```
Evidence gap | AMZN FY2025 explain free cash flow: GAP EVIDENCE_GAP (no Item 7 sentence above the threshold (0.3) for 'free cash flow operating cash flow capital expenditures decreased compared with the prior year primarily due to' (no candidate sentence); 3 passage(s) below the reranker threshold 5.263 (best 4.261)).
Verified figure | AMZN FY2025 free cash flow: USD 7,695.0m (calculation; basis: DERIVED; from operating_cash_flow, capital_expenditure; 10-K 0001018724-26-000004, filed 2026-02-06, https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/amzn-20251231.htm).
```

**Why did Amazon's operating income increase in FY2025?** (COMPLETE; compiler m1-intent-tfidf-lr-v3)

```
AMZN FY2025 operating income: USD 79,975.0m (reported fact; basis: AGREE; row 'Operating income'; tags us-gaap:OperatingIncomeLoss; 10-K 0001018724-26-000004, filed 2026-02-06, https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/amzn-20251231.htm).
AMZN FY2025 operating income change: USD 11,382.0m (calculation; basis: FY2025 minus FY2024 operating_income (both VERIFIED); quotes: Item 7 of this 10-K for 'operating income increased compared with the prior year primarily due to'; 10-K 0001018724-26-000004, filed 2026-02-06, https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/amzn-20251231.htm).
  Quote | ITEM_7, chunk 0001018724-26-000004:ITEM_7:0037, score 1.00: "The increase in International operating income in 2025, compared to the prior year, is primarily due to increased unit sales and increased advertising sales, partially offset by increased fulfillment and shipping costs."
  Quote | ITEM_7, chunk 0001018724-26-000004:ITEM_7:0037, score 1.00: "The increase in AWS operating income in 2025, compared to the prior year, is primarily due to increased sales, partially offset by spending on technology infrastructure that was primarily driven by additional investments to support AWS business growth."
```

**Why did Amazon's operating cash flow increase in FY2025?** (EVIDENCE_GAP; compiler m1-intent-tfidf-lr-v3)

```
Evidence gap | AMZN FY2025 explain operating cash flow: GAP EVIDENCE_GAP (no Item 7 sentence above the threshold (0.3) for 'cash provided by operating activities increased compared with the prior year primarily due to' (no candidate sentence); 2 passage(s) below the reranker threshold 5.263 (best 5.009)).
Verified figure | AMZN FY2025 operating cash flow: USD 139,514.0m (reported fact; basis: AGREE; row 'Net cash provided by (used in) operating activities'; tags us-gaap:NetCashProvidedByUsedInOperatingActivities; 10-K 0001018724-26-000004, filed 2026-02-06, https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/amzn-20251231.htm).
```

**Why did Microsoft's revenue increase in fiscal 2026?** (EVIDENCE_GAP; compiler m1-intent-tfidf-lr-v3)

```
Evidence gap | MSFT FY2026 explain revenue: GAP EVIDENCE_GAP (no Item 7 sentence above the threshold (0.3) for 'revenue net sales increased compared with the prior year primarily due to' (no candidate sentence); 3 passage(s) below the reranker threshold 5.263 (best 4.458)).
Verified figure | MSFT FY2026 revenue: USD 331,839.0m (reported fact; basis: AGREE; row 'Total revenue'; tags us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax; 10-K 0001193125-26-323660, filed 2026-07-29, https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm).
```

**Why did Microsoft's operating income increase in fiscal 2026?** (EVIDENCE_GAP; compiler m1-intent-tfidf-lr-v3)

```
Evidence gap | MSFT FY2026 explain operating income: GAP EVIDENCE_GAP (no Item 7 sentence above the threshold (0.3) for 'operating income increased compared with the prior year primarily due to' (no candidate sentence); 3 passage(s) below the reranker threshold 5.263 (best 4.802)).
Verified figure | MSFT FY2026 operating income: USD 155,237.0m (reported fact; basis: AGREE; row 'Operating income'; tags us-gaap:OperatingIncomeLoss; 10-K 0001193125-26-323660, filed 2026-07-29, https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm).
```

**Why did Microsoft's free cash flow fall in fiscal 2026?** (EVIDENCE_GAP; compiler m1-intent-tfidf-lr-v3)

```
Evidence gap | MSFT FY2026 explain free cash flow: GAP EVIDENCE_GAP (no Item 7 sentence above the threshold (0.3) for 'free cash flow operating cash flow capital expenditures decreased compared with the prior year primarily due to' (no candidate sentence); 3 passage(s) below the reranker threshold 5.263 (best 1.075)).
Single-channel figure | MSFT FY2026 free cash flow: USD 66,987.0m (calculation; flags SINGLE_CHANNEL; basis: DERIVED; from operating_cash_flow, capital_expenditure; 10-K 0001193125-26-323660, filed 2026-07-29, https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm).
```

## Default path (statements-0009, forecast-0007)

```
STATEMENTS 16837 observations; 84 differences (vs statements-0009)
FORECAST 9486 projected values compared; 0 differences; 0 forecast-0007 values not produced []; 0 new lines []
CHECKS 1152 checks compared; 0 status or residual differences []
CHANGED
```


<!-- notes: text below this line is kept when the report is regenerated -->