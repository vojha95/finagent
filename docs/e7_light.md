# E7 light: latency, failures, cold vs warm (e7-0001)

Phase 9 cut-down (BC33). Written by `python eval/e7_light.py run`; definitions in `finagent/evaluation/e7.py`. Validation companies only; no held-out company is named.

## Run

- Commit 5eaac6c (dirty), evidence manifest 89ee41619200…, as_of 2026-11-24.
- Document model m1-tfidf-lr-v3; intent model v3.
- Machine: macOS-27.2.1-arm64-arm-64bit, arm64, 8 CPUs, Python 3.11.18. Local, CPU only, offline.
- Questions: 20 canonical E5 questions (gold sha256 44514619cbd0…), groups: fact 2, statements 2, forecast 2, credit 2, liquidity 2, debt 2, explain 2, risk 2, advice 1, two_periods 1, wrong_company 1, unsupported 1.

## Results

| Measure | Cold pass | Warm pass |
|---|---|---|
| p50 latency (s) | 0.25 | 0.22 |
| p95 latency (s) | 16.38 | 0.76 |
| Mean / max (s) | 4.10 / 35.53 | 0.27 / 0.80 |
| Total for 20 questions (s) | 81.9 | 5.5 |
| Failures (exceptions) | 0 | 0 |

- **Cold start:** loading the planner, the intent model and the tools took 2.02 s; with the first question, 16.62 s.
- **Failures:** 0 of 40 calls raised an exception.
- **Statuses (cold pass):** CLARIFICATION 4, COMPLETE 14, EVIDENCE_GAP 2.
- **Same status in both passes:** 20 of 20. **Same status as e5-0003:** 20 of 20.
- **Peak memory (RSS):** 870 MB.

## Per question

| # | Id | Group | Cold (s) | Warm (s) | Status | Question |
|---|---|---|---|---|---|---|
| 1 | Q001 | fact | 14.61 | 0.21 | COMPLETE | Compare Abbott and Amazon revenue in FY2021 |
| 2 | Q025 | statements | 0.14 | 0.14 | COMPLETE | Reconcile the three statements for Abbott |
| 3 | Q033 | forecast | 0.31 | 0.38 | COMPLETE | Show the Base scenario forecast for Abbott in FY2026 |
| 4 | Q049 | credit | 0.25 | 0.26 | COMPLETE | Show the current ratio for Abbott for the latest reported year |
| 5 | Q061 | liquidity | 35.53 | 0.41 | COMPLETE | Assess Base liquidity for Merck in FY2026 |
| 6 | Q073 | debt | 0.26 | 0.28 | COMPLETE | Assess Base liquidity for Abbott; if there is a shortfall, check debt service |
| 7 | Q081 | explain | 4.82 | 0.64 | EVIDENCE_GAP | Why did Abbott's revenue change in FY2022? |
| 8 | Q089 | risk | 0.29 | 0.32 | COMPLETE | What are the main risk signals for Abbott? |
| 9 | Q097 | advice | 0.00 | 0.00 | CLARIFICATION | Should I buy Abbott? |
| 10 | Q103 | two_periods | 0.00 | 0.00 | CLARIFICATION | What was Nike's operating income in FY2022 and FY2023? |
| 11 | Q110 | wrong_company | 0.00 | 0.00 | CLARIFICATION | What was net income in FY2023? |
| 12 | Q115 | unsupported | 0.00 | 0.00 | CLARIFICATION | What is Colgate-Palmolive's current share price? |
| 13 | Q002 | fact | 8.26 | 0.23 | COMPLETE | Compare Amazon and Colgate-Palmolive net income in FY2022 |
| 14 | Q026 | statements | 0.10 | 0.10 | COMPLETE | Reconcile the three statements for Amazon for FY2021 |
| 15 | Q034 | forecast | 0.17 | 0.20 | COMPLETE | Forecast revenue for Amazon under the Moderate scenario |
| 16 | Q050 | credit | 0.23 | 0.31 | COMPLETE | Show historical credit ratios for Amazon for FY2021 |
| 17 | Q062 | liquidity | 15.38 | 0.76 | COMPLETE | Assess moderate liquidity for Microsoft in FY2028 |
| 18 | Q074 | debt | 0.24 | 0.20 | COMPLETE | Check debt service for Amazon under the Moderate scenario |
| 19 | Q082 | explain | 1.07 | 0.80 | EVIDENCE_GAP | Why did Amazon's net income change in FY2023? |
| 20 | Q090 | risk | 0.24 | 0.21 | COMPLETE | What are the main risk signals for Amazon in FY2021? |

## Limits

- One machine, one run, 20 questions: an indication of local latency, not a load test. The full E7 (load, logging, cost) was cut (BC33, #318).
- The cold pass includes lazy per-company work (statement sets, the retrieval models), so its p95 reflects the first question about each company.
