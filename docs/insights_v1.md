# Insights v1: risk signals and insight cards (Phase 7.3)

Run `insights-0002`; git `820b90b` (dirty); evidence manifest sha256 `89ee41619200`; as of 2026-11-24; signals.yaml sha256 `065e7615a03f`; query templates sha256 `4420e0ac6f19`; quote threshold 0.3 (provisional, chosen on the dev companies only).

## Method

Signals: Appendix C.2 thresholds as written (D5), on the validated statement set's accepted inputs (the credit ratios' inputs); R5 = funded debt / (EBIT + D&A from the cash flow statement) (D2); R10 on the Moderate scenario from the latest 10-K (D3); R2 and R9 GAP NO_COGS without a cost-of-sales line (D4); severity R7, R10, R11 high, the others medium (D5); D&A falls back to depreciation + amortisation when both are read, else GAP (#179). A missing input is never read as not fired. Every fired measure is recomputed a second way in Decimal by the risk tool.

Evidence: the signal's frozen query, BM25 only (dense retrieval is 7.4), over the text of Item 7 and Item 8 with its notes of the same 10-K; whole sentences of 6 to 60 words (longer ones skipped, #173), with an anchor term, not about an earlier year only; score = share of the query's IDF weight in the sentence; the best two at or above the threshold are quoted, otherwise the card is EVIDENCE_GAP. The planner's gate rebuilds every quoted chunk from the pinned document (#157). An R10 card is model-derived: no search, no quote, labelled 'model-derived; no filing evidence expected' (#180).

## Summary

Signal evaluations (every company, year and signal): FIRED 69, NOT_FIRED 223, GAP 116, NOT_APPLIED 32.
Cards (every year): 69, of which quoted 51, EVIDENCE_GAP 15 and model-derived (no filing evidence expected, #180) 3. Latest year: 20 cards, 14 quoted, 3 EVIDENCE_GAP, 3 model-derived.

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

- **R3 Capex intensity spike** (medium; VERIFIED): capex to revenue up +5.4 pp (test > +2.0 pp; FY2024 13.01, FY2025 18.39). Evidence: quoted below.
  - Quote (ITEM_7, score 0.56, `0001018724-26-000004:ITEM_7:0016`): "Cash provided by (used in) investing activities corresponds with cash capital expenditures, including leasehold improvements, incentives received from property and equipment vendors, proceeds from asset sales, cash outlays for acquisitions, investments in other companies and intellectual property rights, and purchases, sales, and maturities of marketable securities."
  - Quote (ITEM_7, score 0.53, `0001018724-26-000004:ITEM_7:0016`): "Cash capital expenditures were $77.7 billion, and $128.3 billion in 2024 and 2025, which primarily reflect investments in technology infrastructure (the majority of which is to support AWS business growth) and in additional capacity to support our fulfillment network, both of which we expect to increase in 2026."
- **R4 Weak cash conversion** (medium; VERIFIED): FCF margin down -4.1 pp (test < -3.0 pp; FY2024 5.154, FY2025 1.073). Evidence: quoted below.
  - Quote (ITEM_7, score 0.50, `0001018724-26-000004:ITEM_7:0015`): "The increase in operating cash flow in 2025, compared to the prior year, was due to an increase in net income (loss), excluding non-cash expenses, and changes in working capital."
  - Quote (ITEM_7, score 0.42, `0001018724-26-000004:ITEM_7:0003`): "Free cash flow is driven primarily by increasing operating income and efficiently managing accounts receivable, inventory, accounts payable, and cash capital expenditures, including our decision to purchase or lease property and equipment."
- **R9 Payables stretch** (medium; EVIDENCE_GAP): DPO up +10.2 days (test > +10.0 days; FY2024 100.6, FY2025 110.7). EVIDENCE_GAP: the filing does not explain the movement (no sentence above the relevance threshold).
  - EVIDENCE_GAP (4 candidate sentence(s), best score 0.23; skipped {'NO_ANCHOR': 23, 'TOO_LONG': 1, 'NOT_A_SENTENCE': 3})
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

- **R2 Inventory build** (medium; VERIFIED): inventory growth minus COGS growth +27.8 pp (test > +10.0 pp; inventory +48.9%, cogs +21.1%). Evidence: quoted below.
  - Quote (ITEM_7, score 0.40, `0001193125-26-323660:ITEM_7:0019`): "Windows OEM and Devices revenue decreased slightly driven by a decline in Devices, offset in part by Windows OEM growth of 5% with inventory levels that remained elevated."
- **R3 Capex intensity spike** (medium; VERIFIED): capex to revenue up +12.0 pp (test > +2.0 pp; FY2025 22.91, FY2026 34.94); capex to revenue over its three-year median 1.93x (test > 1.50x; FY2023 0.1326, FY2024 0.1814, FY2025 0.2291, FY2026 0.3494; 3-year median 0.1814). Evidence: quoted below.
  - Quote (ITEM_7, score 0.36, `0001193125-26-323660:ITEM_7:0044`): "We will continue to invest in capital expenditures to support growth in our cloud offerings and our investments in AI training and other infrastructure."
  - Quote (ITEM_7, score 0.36, `0001193125-26-323660:ITEM_7:0034`): "We expect existing cash, cash equivalents, short-term investments, cash flows from operations, and access to capital markets to continue to be sufficient to fund our operating activities and cash commitments for investing and financing activities, such as dividends, share repurchases, debt maturities, and material capital expenditures, for at least the next 12 months and thereafter for the foreseeable future."
- **R4 Weak cash conversion** (medium; VERIFIED): FCF margin down -5.2 pp (test < -3.0 pp; FY2025 25.42, FY2026 20.19). Evidence: quoted below.
  - Quote (ITEM_7, score 0.44, `0001193125-26-323660:ITEM_7:0034`): "We expect existing cash, cash equivalents, short-term investments, cash flows from operations, and access to capital markets to continue to be sufficient to fund our operating activities and cash commitments for investing and financing activities, such as dividends, share repurchases, debt maturities, and material capital expenditures, for at least the next 12 months and thereafter for the foreseeable future."
- **R9 Payables stretch** (medium; EVIDENCE_GAP): DPO up +17.0 days (test > +10.0 days; FY2025 103.3, FY2026 120.3). EVIDENCE_GAP: the filing does not explain the movement (no sentence above the relevance threshold).
  - EVIDENCE_GAP (2 candidate sentence(s), best score 0.26; skipped {'NO_ANCHOR': 23, 'NOT_A_SENTENCE': 3})
- **R11 Operating and investing cash pressure (composite)** (high; EVIDENCE_GAP): revenue growth +17.8 pp (test > +0.0 pp; revenue +17.8%); components fired: R2, R3, R4. EVIDENCE_GAP: the filing does not explain the movement (no sentence above the relevance threshold).
  - EVIDENCE_GAP (3 candidate sentence(s), best score 0.20; skipped {'NOT_A_SENTENCE': 17, 'NO_ANCHOR': 29, 'TOO_LONG': 1})

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

- **R2 Inventory build** (medium; VERIFIED): inventory growth minus COGS growth +16.2 pp (test > +10.0 pp; inventory +7.0%, cogs -9.2%). Evidence: quoted below.
  - Quote (ITEM_8/NOTE_01, score 0.36, `0000093410-26-000078:ITEM_8/NOTE_01:0005`): "“Materials, supplies and other” inventories are primarily stated at cost or net realizable value."
  - Quote (ITEM_7, score 0.34, `0000093410-26-000078:ITEM_7:0024`): "Industry margins can also be influenced by inventory levels, geopolitical events, costs of materials and services, refinery or chemical plant capacity utilization, maintenance programs, and disruptions at refineries or chemical plants resulting from unplanned outages due to severe weather, fires or other operational events."
- **R8 Margin compression** (medium; VERIFIED): operating (EBIT) margin down -3.2 pp (test < -3.0 pp; FY2024 14.53, FY2025 11.36). Evidence: quoted below.
  - Quote (ITEM_7, score 0.49, `0000093410-26-000078:ITEM_7:0036`): "U.S. downstream earnings increased by $844 million, primarily due to lower operating expenses of $730 million and higher margins on refined product sales of $580 million, partly offset by lower earnings from the 50 percent-owned Chevron Phillips Chemical Company of $440 million."
  - Quote (ITEM_8, score 0.43, `0000093410-26-000078:ITEM_8:0022`): "The decrease in income before taxes for the company was primarily the result of higher upstream depreciation, depletion and amortization, lower upstream realizations, unfavorable foreign exchange impacts, and higher operating costs, partially offset by higher upstream sales volumes and higher downstream margins."
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

- **R1 Receivables outpacing sales** (medium; VERIFIED): DSO up +5.8 days (test > +5.0 days; FY2025 36.04, FY2026 41.88); receivables growth minus revenue growth +25.5 pp (test > +10.0 pp; receivables +25.7%, revenue +0.2%). Evidence: quoted below.
  - Quote (ITEM_7, score 0.40, `0000320187-26-000088:ITEM_7:0062`): "The increase in Accounts receivable was primarily due to the outstanding IEEPA tariff receivable, as well as higher wholesale revenues and the timing of receipts in the normal course of business."
  - Quote (ITEM_7, score 0.31, `0000320187-26-000088:ITEM_7:0063`): "The increase in Accounts receivable was primarily due to the timing of wholesale shipments and the increase in Prepaid expenses and other current assets was primarily due to prepayments related to endorsement contracts."

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

- **R1 Receivables outpacing sales** (medium; VERIFIED): receivables growth minus revenue growth +13.3 pp (test > +10.0 pp; receivables +14.6%, revenue +1.3%). Evidence: quoted below.
  - Quote (ITEM_7, score 0.38, `0000310158-26-000063:ITEM_7:0125`): "In addition, if collection of accounts receivable is expected to be in excess of one year, sales are recorded net of time value of money discounts, which have not been material."
  - Quote (ITEM_8/NOTE_06, score 0.38, `0000310158-26-000063:ITEM_8/NOTE_06:0030`): "In certain of these factoring arrangements, for ease of administration, the Company will collect customer payments related to the factored receivables, which it then remits to the financial institutions, generally within thirty days after receipt."
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

- **R5 Rising leverage** (medium; VERIFIED): funded debt to (EBIT + D&A) 3.02x (test > 3.00x; FY2025 3.021). Evidence: quoted below.
  - Quote (ITEM_8/NOTE_11, score 0.47, `0000732717-26-000120:ITEM_8/NOTE_11:0002`): "We had no outstanding commercial paper or other short-term borrowings as of December 31, 2025 and 2024."
  - Quote (ITEM_7, score 0.47, `0000732717-26-000120:ITEM_7:0063`): "We had no outstanding commercial paper borrowings or other short-term borrowings on December 31, 2025."
- **R6 Thin interest cover** (medium; VERIFIED): EBIT to gross interest 3.55x (test < 4.00x; FY2025 3.551). Evidence: quoted below.
  - Quote (ITEM_8/NOTE_18, score 0.46, `0000732717-26-000120:ITEM_8/NOTE_18:0001`): "The financing obligation is increased by interest expense and estimated future net cash flows generated and retained by Crown Castle from operation of the tower sites, and reduced by our contractual payments."

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

- **R7 Tight liquidity** (high; VERIFIED): current ratio 0.83x (test < 1.00x; FY2025 0.8331); current ratio falling -0.09x (test < +0.00x; FY2024 0.9233, FY2025 0.8331). Evidence: quoted below.
  - Quote (ITEM_7, score 0.41, `0000021665-26-000006:ITEM_7:0096`): "The Company defines working capital as the difference between current assets (excluding Cash and cash equivalents and marketable securities, the latter of which is reported in Other current assets) and current liabilities (excluding short-term debt)."
- **R8 Margin compression** (medium; VERIFIED): operating (EBIT) margin down -5.0 pp (test < -3.0 pp; FY2024 21.23, FY2025 16.22). Evidence: quoted below.
  - Quote (ITEM_7, score 0.43, `0000021665-26-000006:ITEM_7:0019`): "This decrease in Gross profit margin was due to significantly higher raw and packaging material costs (420 bps), partially offset by cost savings from the Company’s funding-the-growth initiatives (260 bps), higher pricing (80 bps) and favorable mix (30 bps)."
  - Quote (ITEM_7, score 0.42, `0000021665-26-000006:ITEM_7:0019`): "Excluding these charges, Worldwide Gross profit increased to $12,251 in 2025 compared to $12,181 in 2024, reflecting an increase of $170 resulting from higher Net sales, partially offset by lower Gross profit margin of $100."
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

**Why did Amazon's free cash flow fall in FY2025?** (COMPLETE; compiler m1-intent-tfidf-lr-v2)

```
AMZN FY2025 free cash flow: USD 7,695.0m (calculation; basis: DERIVED; from operating_cash_flow, capital_expenditure; 10-K 0001018724-26-000004, filed 2026-02-06, https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/amzn-20251231.htm).
AMZN FY2025 free cash flow change: USD -25,183.0m (calculation; basis: FY2025 minus FY2024 free_cash_flow (both VERIFIED); quotes: Item 7 of this 10-K for 'free cash flow operating cash flow capital expenditures decreased compared with the prior year primarily due to'; 10-K 0001018724-26-000004, filed 2026-02-06, https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/amzn-20251231.htm).
  Quote | ITEM_7, chunk 0001018724-26-000004:ITEM_7:0003, score 0.55: "Free cash flow is driven primarily by increasing operating income and efficiently managing accounts receivable, inventory, accounts payable, and cash capital expenditures, including our decision to purchase or lease property and equipment."
  Quote | ITEM_7, chunk 0001018724-26-000004:ITEM_7:0043, score 0.37: "Free cash flow has limitations as it omits certain components of the overall cash flow statement and does not represent the residual cash flow available for discretionary expenditures."
```

**Why did Amazon's operating income increase in FY2025?** (COMPLETE; compiler m1-intent-tfidf-lr-v2)

```
AMZN FY2025 operating income: USD 79,975.0m (reported fact; basis: AGREE; row 'Operating income'; tags us-gaap:OperatingIncomeLoss; 10-K 0001018724-26-000004, filed 2026-02-06, https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/amzn-20251231.htm).
AMZN FY2025 operating income change: USD 11,382.0m (calculation; basis: FY2025 minus FY2024 operating_income (both VERIFIED); quotes: Item 7 of this 10-K for 'operating income increased compared with the prior year primarily due to'; 10-K 0001018724-26-000004, filed 2026-02-06, https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/amzn-20251231.htm).
  Quote | ITEM_7, chunk 0001018724-26-000004:ITEM_7:0037, score 1.00: "The increase in International operating income in 2025, compared to the prior year, is primarily due to increased unit sales and increased advertising sales, partially offset by increased fulfillment and shipping costs."
  Quote | ITEM_7, chunk 0001018724-26-000004:ITEM_7:0037, score 1.00: "The increase in AWS operating income in 2025, compared to the prior year, is primarily due to increased sales, partially offset by spending on technology infrastructure that was primarily driven by additional investments to support AWS business growth."
```

**Why did Amazon's operating cash flow increase in FY2025?** (COMPLETE; compiler m1-intent-tfidf-lr-v2)

```
AMZN FY2025 operating cash flow: USD 139,514.0m (reported fact; basis: AGREE; row 'Net cash provided by (used in) operating activities'; tags us-gaap:NetCashProvidedByUsedInOperatingActivities; 10-K 0001018724-26-000004, filed 2026-02-06, https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/amzn-20251231.htm).
AMZN FY2025 operating cash flow change: USD 23,637.0m (calculation; basis: FY2025 minus FY2024 operating_cash_flow (both VERIFIED); quotes: Item 7 of this 10-K for 'cash provided by operating activities increased compared with the prior year primarily due to'; 10-K 0001018724-26-000004, filed 2026-02-06, https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/amzn-20251231.htm).
  Quote | ITEM_7, chunk 0001018724-26-000004:ITEM_7:0015, score 0.56: "The increase in operating cash flow in 2025, compared to the prior year, was due to an increase in net income (loss), excluding non-cash expenses, and changes in working capital."
  Quote | ITEM_7, chunk 0001018724-26-000004:ITEM_7:0015, score 0.39: "Cash provided by (used in) operating activities was $115.9 billion and $139.5 billion in 2024 and 2025."
```

**Why did Microsoft's revenue increase in fiscal 2026?** (EVIDENCE_GAP; compiler m1-intent-tfidf-lr-v2)

```
Evidence gap | MSFT FY2026 explain revenue: GAP EVIDENCE_GAP (no Item 7 sentence above the threshold (0.3) for 'revenue net sales increased compared with the prior year primarily due to' (best score 0.18)).
Verified figure | MSFT FY2026 revenue: USD 331,839.0m (reported fact; basis: AGREE; row 'Total revenue'; tags us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax; 10-K 0001193125-26-323660, filed 2026-07-29, https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm).
```

**Why did Microsoft's operating income increase in fiscal 2026?** (EVIDENCE_GAP; compiler m1-intent-tfidf-lr-v2)

```
Evidence gap | MSFT FY2026 explain operating income: GAP EVIDENCE_GAP (no Item 7 sentence above the threshold (0.3) for 'operating income increased compared with the prior year primarily due to' (best score 0.18)).
Verified figure | MSFT FY2026 operating income: USD 155,237.0m (reported fact; basis: AGREE; row 'Operating income'; tags us-gaap:OperatingIncomeLoss; 10-K 0001193125-26-323660, filed 2026-07-29, https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm).
```

**Why did Microsoft's free cash flow fall in fiscal 2026?** (COMPLETE; compiler m1-intent-tfidf-lr-v2)

```
MSFT FY2026 free cash flow: USD 66,987.0m (calculation; flags SINGLE_CHANNEL; basis: DERIVED; from operating_cash_flow, capital_expenditure; 10-K 0001193125-26-323660, filed 2026-07-29, https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm).
MSFT FY2026 free cash flow change: USD -4,624.0m (calculation; flags SINGLE_CHANNEL; basis: FY2026 minus FY2025 free_cash_flow (FY2026 SINGLE_CHANNEL, tag only, FY2025 SINGLE_CHANNEL, tag only; single-channel inputs, not verified by both channels); quotes: Item 7 of this 10-K for 'free cash flow operating cash flow capital expenditures decreased compared with the prior year primarily due to'; 10-K 0001193125-26-323660, filed 2026-07-29, https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm).
  Quote | ITEM_7, chunk 0001193125-26-323660:ITEM_7:0034, score 0.42: "We expect existing cash, cash equivalents, short-term investments, cash flows from operations, and access to capital markets to continue to be sufficient to fund our operating activities and cash commitments for investing and financing activities, such as dividends, share repurchases, debt maturities, and material capital expenditures, for at least the next 12 months and thereafter for the foreseeable future."
```

## Default path (statements-0008, forecast-0006)

```
STATEMENTS 16837 observations; 608 differences (vs statements-0008)
FORECAST 9486 projected values compared; 0 differences; 0 forecast-0006 values not produced []; 0 new lines []
CHECKS 1152 checks compared; 0 status or residual differences []
CHECK DETAIL TEXT (72 checks; status and residual unchanged) F5: 'headroom and shortfall recomputed (haircut 0, floor 0)' -> 'headroom and shortfall recomputed (cash_plus_haircut_securities; haircut 0, floor 0)'
CHECK DETAIL TEXT (72 checks; status and residual unchanged) F5: 'headroom and shortfall recomputed (haircut 0.2, floor 0)' -> 'headroom and shortfall recomputed (cash_plus_haircut_securities; haircut 0.2, floor 0)'
CHECK DETAIL TEXT (72 checks; status and residual unchanged) F5: 'headroom and shortfall recomputed (haircut 0.5, floor 0)' -> 'headroom and shortfall recomputed (cash_plus_haircut_securities; haircut 0.5, floor 0)'
CHANGED
```


<!-- notes: text below this line is kept when the report is regenerated -->

## Notes (hand-written)

**insights-0002 (Phase 7 Part A; issues #189-#196).** Against insights-0001: R5 is computed for MSFT, MRK and ABT through the #179 fallback (D&A = depreciation + amortisation of intangibles, both read; one missing stays GAP, as NKE FY2022); MRK FY2023 R5 fires (5.10x) with a new card. The three R10 cards (AMZN, CVX, CL) are model-derived: no search, no quote (#180). Explain (dev): MSFT operating income is now EVIDENCE_GAP because the segment sentence stating +28% is skipped (#185); MSFT free cash flow is answered from single-channel (tag-only) inputs, flagged SINGLE_CHANNEL (#186), but its quote is off-topic (quote quality, #180, for E3). The default-path section above shows CHANGED: the 608 statement differences are the 200 new amortisation lines (#179) and the #152 nil components and funded-debt flags; no forecast value changed.

**What was tuned where.** The quote rules (whole sentences of 6–60 words, anchor terms, the
earlier-year rule #181) and the provisional threshold 0.30 were chosen on the dev companies
only (AMZN, MSFT; D10), by reading their candidate sentences without labels. The explain
questions are dev-only. The six test companies' signals and cards were run and read for this
report; nothing was changed because of them. **Section detection is not blind for the test
companies:** the retrieval-0002 section fixes (#171, #172) were written after the problems were
found on test companies (CVX, CL, MRK) in retrieval-0001, although the rules themselves were
written and checked on train filings (docs/retrieval_v1.md).

**How 0.30 was read on dev (examples, dev only).**

| Score | Company, card | Sentence (start) | On topic? |
|---|---|---|---|
| 0.53 | AMZN FY2025 R3 | "Cash capital expenditures were $77.7 billion, and $128.3 billion in 2024 and 2025 …" | yes |
| 0.50 | AMZN FY2025 R4 | "The increase in operating cash flow in 2025, compared to the prior year, was due to …" | yes |
| 0.44 | MSFT FY2026 R4 | "We expect existing cash, cash equivalents … to continue to be sufficient …" | no |
| 0.36 | MSFT FY2026 R3 | "We will continue to invest in capital expenditures to support growth …" | yes |
| 0.33 | AMZN FY2025 R10 | "The amount and timing of revenue recognition will be driven by …" | no |
| 0.31 | MSFT FY2026 explain operating income | "Operating income increased $12.4 billion or 28%." | segment, not total (#185) |
| 0.26 | MSFT FY2026 R9 | "… purchases of property and equipment remaining in accounts payable were …" | partly |
| 0.23 | AMZN FY2025 R9 | "Free cash flow is driven primarily by … accounts payable …" | no |
| 0.18 | MSFT FY2026 explain revenue | "Revenue increased $50.1 billion or 18% driven by growth in Microsoft Cloud." | yes (missed) |

Below 0.30 almost no dev sentence discussed the movement; above it both kinds occur, so the
threshold removes noise but does not make a quote relevant. E3 (7.6) replaces it with a
labelled measurement (#180).

**Run history.** insights-0001 was run twice on 2026-11-25. The first run printed a signal's
quotes under a measure that did not fire (#187) and showed an explain GAP FACT_NOT_VERIFIED
next to a "Verified figure" line (#186). Both were fixed in the tool and the renderer; in the
second run signals.csv and cards.csv are byte-identical to the first. The header above is the
final run's.

**Limitations to read the cards with.**
- R5 is GAP for MSFT, MRK and ABT (no `depreciation_and_amortisation` value; #179).
- T's R2/R9 and NKE's R6 are GAP from statement inputs (#188).
- CVX: part of the MD&A is filed as ITEM_8 (#176), so CVX quotes cited as ITEM_8 (R8's "The
  decrease in income before taxes …", R10's "Contractual Obligations …") are MD&A text.
- Quotes support the subject of the signal, not necessarily its cause; R10 is a model output
  that no filing sentence can explain (#180).
- An explain quote can be about a segment, not the total (#185).
- The early years are mostly GAP NO_YEAR (#182).