# E5 question brief

Vineet: this file is your template. Write one question per row in the last column, in your own
words, as you would type it to FinAgent. Then run
`python -m finagent.evaluation.e5_gold import` to copy your questions into
`gold/intents_test.jsonl`. Labels (the expected goals) are added afterwards, not by you.

## Why this set is blind

The E5 set is written after the intent classifier was trained, but it stays blind because you
write it without seeing the training templates. **Before and while writing, do not open**:
`configs/intents.yaml`, `configs/intent_templates.yaml`, `datasets/intents_*`,
`finagent/planner/`, `tests/test_planner_*`, the planner sections of `docs/issues_log.md`, and
the Phase 6 reports and compiled examples. `docs/model_cards/intent_v*.md` show metrics only and
are safe. This brief describes each cell by what it asks for, never by training wording.

## Rules

- One question per cell. Do not use the `|` character in a question.
- Use only the company given in the row (its name or its ticker, as you prefer). Every company
  here is a validation company. Never use any other company in the universe
  (`configs/universe.yaml`) except where a row asks for a company outside it.
- Fiscal years are the company's own (e.g. a June year-end company's fiscal 2025 ends in June
  2025). Name the fiscal year the row gives, unless the row says not to.
- "Selected in the app" means the user has already picked that company on screen, so the
  question does not need to name it (or, for a mismatch row, names a different one).
- Projected years: name the fiscal year, or refer to it relative to now in your own words.
- Write naturally: vary the wording, length and tone across rows. Typos you would really make
  are fine.
- Traps and clarification rows say what the question should do and what FinAgent is expected to
  do with it. Write a question a real user might ask that fits the description.

## Your 70 questions

### fact

| Cell | What to ask | Company | Details | Your question |
|---|---|---|---|---|
| C010 | a reported figure from the 10-K for one fiscal year | Amazon (AMZN) | figure: operating income; compare with: Colgate-Palmolive (CL); fiscal year: FY2023 | compare amazon's operating income in fy2023 with colgate's |
| C011 | a reported figure from the 10-K for one fiscal year | Colgate-Palmolive (CL) | figure: inventory; compare with: Chevron (CVX); fiscal year: FY2024 | Colgate vs Chevron inventory, FY24? |
| C012 | a reported figure from the 10-K for one fiscal year | Chevron (CVX) | figure: accounts receivable; year: the latest reported year (do not name the year) | what were chevrons accounts receivable in their most recent 10-K |
| C013 | a reported figure from the 10-K for one fiscal year | Merck (MRK) | figure: income tax expense; year: the latest reported year (do not name the year) | merck income tax expense for the latest year? |
| C014 | a reported figure from the 10-K for one fiscal year | Microsoft (MSFT) | figure: free cash flow; fiscal year: FY2026; selected in the app: Microsoft (MSFT) (the question need not name it) | what was free cash flow for fy26 |
| C015 | a reported figure from the 10-K for one fiscal year | Nike (NKE) | figure: dividends paid; fiscal year: FY2022 | How much did Nike pay out in dividends in fiscal 2022? |
| C016 | a reported figure from the 10-K for one fiscal year | AT&T (T) | figure: revenue; fiscal year: FY2022 | AT&T total revenue 2022 |
| C017 | a reported figure from the 10-K for one fiscal year | Abbott (ABT) | figure: net income; fiscal year: FY2023 | whats abbotts net incme for FY2023 |
| C018 | a reported figure from the 10-K for one fiscal year | Amazon (AMZN) | figure: cash flow from operations; fiscal year: FY2024 | Can you pull Amazon's operating cash flow for 2024? |
| C019 | a reported figure from the 10-K for one fiscal year | Colgate-Palmolive (CL) | figure: total assets; fiscal year: FY2025 | total assets at colgate palmolive end of fy 2025? |
| C020 | a reported figure from the 10-K for one fiscal year | Chevron (CVX) | figure: long-term debt; fiscal year: FY2021 | I'm looking for Chevron's long term debt as of year end 2021 |
| C021 | a reported figure for one fiscal year, with another number in the question that could be mistaken for a year (an amount, a count, a page or note number). Expected: one reported-figure answer for the named fiscal year | Merck (MRK) | figure: capital expenditure; fiscal year: FY2022 | what was merck's capital expenditure in fy2022? someone told me it was about 2025 million but that sounds low |
| C022 | the same figure for two companies side by side, without any word asking for a comparison. Expected: FinAgent asks you to name one company or ask for a comparison (wrong_company) | Microsoft (MSFT) | figure: gross profit; second company: Nike (NKE); fiscal year: FY2024 | gross profit fy2024 for microsoft and nike |
| C023 | a reported figure for the latest year, said in words (do not name the fiscal year). Expected: one reported-figure answer for the latest fiscal year | Nike (NKE) | figure: cash and cash equivalents; year: the latest reported year (do not name the year) | how much cash and cash equivalents did nike have at the end of its latest fiscal year |
| C024 | a reported figure, using in its ordinary sense a word FinAgent also uses for another kind of request (forecasting, scenarios, explanations, checks, risk). Expected: one reported-figure answer | AT&T (T) | figure: total shareholders' equity; fiscal year: FY2024 | can you check what AT&T's total shareholders equity was for fy2024 |

### statements

| Cell | What to ask | Company | Details | Your question |
|---|---|---|---|---|
| C028 | a check that the company's three financial statements reconcile (tie out) for a year | Chevron (CVX) | year: the latest reported year (do not name the year) | do chevron's three statements tie out for the most recent year? |
| C029 | a check that the company's three financial statements reconcile (tie out) for a year | Merck (MRK) | year: the latest reported year (do not name the year) | Reconcile Merck's income statement, balance sheet and cash flow statement from their latest 10-K. Does everything foot? |
| C030 | a check that the company's three financial statements reconcile (tie out) for a year | Microsoft (MSFT) | fiscal year: FY2024 | msft fy24 - do the IS, BS and CFS reconcile with each other |
| C031 | a check that the company's three financial statements reconcile (tie out) for a year | Nike (NKE) | fiscal year: FY2025 | Can you verify Nike's financial statements are internally consistent for fiscal 2025? like does net income flow through and does the cash match |
| C032 | a check that the company's three financial statements reconcile (tie out) for a year | AT&T (T) | fiscal year: FY2025 | AT&T 2025 statement tie-out pls |

### forecast

| Cell | What to ask | Company | Details | Your question |
|---|---|---|---|---|
| C040 | a projected figure from FinAgent's three-year forecast | AT&T (T) | figure: total debt; projected year: FY2028; scenario: do not name one; selected in the app: AT&T (T) (the question need not name it) | what's total debt projected to be in fy2028? |
| C041 | a projected figure from FinAgent's three-year forecast | Abbott (ABT) | figure: revenue; projected year: none named (the first projected year); scenario: Base | what's the base case revenue projection for abbott |
| C042 | a projected figure from FinAgent's three-year forecast | Amazon (AMZN) | figure: free cash flow; projected year: none named (the first projected year); scenario: Moderate | Under the moderate scenario, what free cash flow do you project for Amazon? |
| C043 | a projected figure from FinAgent's three-year forecast | Colgate-Palmolive (CL) | figure: closing cash; projected year: FY2026; scenario: Severe | colgate closing cash fy2026 in the severe case? |
| C044 | a projected figure from FinAgent's three-year forecast | Chevron (CVX) | figure: net income; projected year: FY2027; scenario: do not name one | What's Chevron's net income expected to be in 2027? |
| C045 | a projected figure from FinAgent's three-year forecast | Merck (MRK) | figure: EBIT; projected year: FY2028; scenario: Base | Merck projected EBIT for FY28, base scenario |
| C046 | a forecast for a fiscal year that has already been reported. Expected: a forecast goal for that year, answered as a gap (the year is not projected) | Microsoft (MSFT) | figure: capital expenditure; year to forecast: FY2026; scenario: Moderate | forecast microsoft capex for fiscal 2026 under the moderate scenario |
| C047 | a forecast for a fiscal year beyond the three projected years. Expected: a forecast goal for that year, answered as a gap (outside the horizon) | Nike (NKE) | figure: total debt; year to forecast: FY2030; scenario: Severe | In a severe scenario, how much total debt would Nike be carrying by FY2030? |
| C048 | a forecast under a stressed or downside case without saying which scenario. Expected: FinAgent asks which scenario (Base, Moderate or Severe) (unsupported) | AT&T (T) | figure: revenue; projected year: FY2028; scenario: do not name one | what would AT&T's revenue look like in 2028 in a downside case |

### credit

| Cell | What to ask | Company | Details | Your question |
|---|---|---|---|---|
| C054 | a historical credit or financial ratio for one fiscal year | Microsoft (MSFT) | figure: total liabilities to total assets; year: the latest reported year (do not name the year) | what's microsoft's total liabilities to total assets ratio based on the latest annual report |
| C055 | a historical credit or financial ratio for one fiscal year | Nike (NKE) | figure: operating cash flow to current liabilities; year: the latest reported year (do not name the year) | Nike - operating cash flow divided by current liabilities, most recent fiscal year? |
| C056 | a historical credit or financial ratio for one fiscal year | AT&T (T) | figure: none named (let FinAgent choose); fiscal year: FY2025 | How do AT&T's credit ratios look for FY2025? |
| C057 | a historical credit or financial ratio for one fiscal year | Abbott (ABT) | figure: days sales outstanding; fiscal year: FY2021 | abbott DSO 2021 |
| C058 | a historical credit or financial ratio for one fiscal year | Amazon (AMZN) | figure: EBIT margin; fiscal year: FY2022 | What was Amazon's EBIT margin in fiscal year 2022? |
| C059 | a historical credit or financial ratio for one fiscal year | Colgate-Palmolive (CL) | figure: current ratio; fiscal year: FY2023 | colgate's current ratio at the end of 2023, please |
| C060 | a historical ratio "under" a named scenario. Expected: FinAgent says scenarios apply only to projections (unsupported) | Chevron (CVX) | figure: quick ratio; fiscal year: FY2024; scenario: Severe | what was chevron's quick ratio in 2024 under the severe scenario? |

### liquidity

| Cell | What to ask | Company | Details | Your question |
|---|---|---|---|---|
| C066 | the company's liquidity position (headroom or shortfall) in a projected year | Amazon (AMZN) | figure: none named (let FinAgent choose); projected year: FY2026; scenario: Moderate | Is Amazon going to have enough liquidity in FY2026 in the moderate scenario? |
| C067 | the company's liquidity position (headroom or shortfall) in a projected year | Colgate-Palmolive (CL) | figure: none named (let FinAgent choose); projected year: FY2027; scenario: Severe | colgate liquidity position 2027, severe case. any headroom or are they short? |
| C068 | the company's liquidity position (headroom or shortfall) in a projected year | Chevron (CVX) | figure: none named (let FinAgent choose); projected year: FY2028; scenario: do not name one | how does chevron's liquidity look in 2028 |
| C069 | the company's liquidity position (headroom or shortfall) in a projected year | Merck (MRK) | figure: liquidity shortfall; projected year: none named (the first projected year); scenario: Base | Does Merck face a liquidity shortfall in the first forecast year under base? |
| C070 | the company's liquidity position (headroom or shortfall) in a projected year | Microsoft (MSFT) | figure: liquidity headroom; projected year: none named (the first projected year); scenario: Moderate | how much liquidity headroom does microsoft have in the moderate scenario |
| C071 | the company's liquidity position (headroom or shortfall) in a projected year | Nike (NKE) | figure: liquidity shortfall; projected year: FY2027; scenario: Severe | Under severe stress in FY27, how big is Nike's liquidity shortfall, if there is one? |
| C072 | liquidity in a projected year without naming any scenario. Expected: the Base scenario is used and the answer says so | AT&T (T) | figure: liquidity headroom; projected year: FY2027; scenario: do not name one | at&t liquidity headroom in 2027? |

### debt

| Cell | What to ask | Company | Details | Your question |
|---|---|---|---|---|
| C077 | whether the company can meet its scheduled debt service in a projected year | Merck (MRK) | projected year: none named (the first projected year); scenario: Base | Can Merck cover its scheduled debt payments in the base case? |
| C078 | whether the company can meet its scheduled debt service in a projected year | Microsoft (MSFT) | projected year: FY2029; scenario: Moderate; ask for liquidity, and for debt service only if there is a shortfall | For Microsoft in FY2029 under the moderate scenario, check liquidity first, and if there's a shortfall tell me whether they can still meet scheduled debt service. |
| C079 | whether the company can meet its scheduled debt service in a projected year | Nike (NKE) | projected year: FY2027; scenario: Severe; ask for liquidity, and for debt service only if there is a shortfall | nike fy27 severe: whats the liquidity picture, and if theyre short can they still service their debt |
| C080 | debt service only if there is a liquidity shortfall, without asking for liquidity itself. Expected: FinAgent asks for the liquidity assessment the condition depends on (unsupported) | AT&T (T) | projected year: FY2027; scenario: do not name one | if AT&T ends up with a shortfall in 2027, can it still make its debt payments? |

### explain

| Cell | What to ask | Company | Details | Your question |
|---|---|---|---|---|
| C085 | why a reported figure changed in a fiscal year compared with the year before | Merck (MRK) | figure: gross profit; year: the latest reported year (do not name the year) | why did merck's gross profit change in the latest year compared to the year before? |
| C086 | why a reported figure changed in a fiscal year compared with the year before | Microsoft (MSFT) | figure: revenue; fiscal year: FY2023 | What drove Microsoft's revenue growth in fiscal 2023? |
| C087 | why a reported figure changed in a fiscal year compared with the year before | Nike (NKE) | figure: net income; fiscal year: FY2024 | why did nike's net income move the way it did in fy2024 |
| C088 | why a reported figure changed in a fiscal year compared with the year before | AT&T (T) | figure: operating income; fiscal year: FY2024 | Explain the year-over-year change in AT&T operating income for 2024 |

### risk

| Cell | What to ask | Company | Details | Your question |
|---|---|---|---|---|
| C092 | the risk signals (warning signs) in the company's latest filings | Chevron (CVX) | year: the latest reported year (do not name the year) | any red flags in chevron's latest 10-K? |
| C093 | the risk signals (warning signs) in the company's latest filings | Merck (MRK) | year: the latest reported year (do not name the year) | What warning signs show up in Merck's most recent filings? |
| C094 | the risk signals (warning signs) in the company's latest filings | Microsoft (MSFT) | fiscal year: FY2024 | risk signals for msft fy2024 |
| C095 | the risk signals (warning signs) in the company's latest filings | Nike (NKE) | fiscal year: FY2025 | Is there anything concerning in Nike's FY2025 filings I should be aware of? |
| C096 | the risk signals (warning signs) in the company's latest filings | AT&T (T) | fiscal year: FY2025 | flag the main risks in at&t's 2025 10k |

### advice

| Cell | What to ask | Company | Details | Your question |
|---|---|---|---|---|
| C100 | investment advice about the company (buy, sell, hold, value, price target, allocation). Expected: FinAgent declines investment advice (advice) | Chevron (CVX) |  | should i buy chevron at these levels |
| C101 | investment advice about the company (buy, sell, hold, value, price target, allocation). Expected: FinAgent declines investment advice (advice) | Merck (MRK) |  | Is Merck undervalued? What's a fair price target? |
| C102 | investment advice about the company (buy, sell, hold, value, price target, allocation). Expected: FinAgent declines investment advice (advice) | Microsoft (MSFT) |  | how much of my portfolio should I put in Microsoft |

### two_periods

| Cell | What to ask | Company | Details | Your question |
|---|---|---|---|---|
| C105 | one figure for two named fiscal years in a single request. Expected: FinAgent asks for one fiscal year per request (two_periods) | Abbott (ABT) | figure: accounts receivable; years: FY2023 and FY2024 | abbott accounts receivable for 2023 and 2024 |
| C106 | one figure for two named fiscal years in a single request. Expected: FinAgent asks for one fiscal year per request (two_periods) | Amazon (AMZN) | figure: income tax expense; years: FY2024 and FY2025 | What was Amazon's income tax expense in FY2024 and FY2025? |
| C107 | one figure over a span of fiscal years (a range). Expected: FinAgent asks for one fiscal year per request (two_periods) | Colgate-Palmolive (CL) | figure: free cash flow; years: FY2021 to FY2025 | show me colgate's free cash flow from 2021 through 2025 |
| C108 | one figure over several years without naming them. Expected: FinAgent asks for one fiscal year per request (two_periods) | Chevron (CVX) | figure: dividends paid | how have chevron's dividend payments trended over the past few years? |

### wrong_company

| Cell | What to ask | Company | Details | Your question |
|---|---|---|---|---|
| C111 | a supported request about a validation company other than the one selected in the app. Expected: FinAgent says the question names a different company (wrong_company) | AT&T (T) | figure: cash flow from operations; fiscal year: FY2024; selected in the app: Nike (NKE) | What was AT&T's cash flow from operations in 2024? |
| C112 | a supported request about a validation company other than the one selected in the app. Expected: FinAgent says the question names a different company (wrong_company) | Abbott (ABT) | figure: total assets; fiscal year: FY2024; selected in the app: AT&T (T) | abbott total assets fy24 |
| C113 | a supported request about a well-known US listed company that is not in FinAgent's universe. Expected: FinAgent says the company is not covered (wrong_company) | one outside the universe (your choice) | figure: long-term debt; fiscal year: FY2025 | What was Chipotle's long-term debt at the end of FY2025? |
| C114 | two separate requests about two companies in one question, not a comparison. Expected: FinAgent asks you to name one company (wrong_company) | Amazon (AMZN) | figure: capital expenditure; second company: Colgate-Palmolive (CL); fiscal year: FY2021 | give me amazon's capex for fy2021, and separately colgate's capex for the same year |

### unsupported

| Cell | What to ask | Company | Details | Your question |
|---|---|---|---|---|
| C118 | something about the company that a 10-K reader cannot answer (market data, people, news, opinions, writing tasks). Expected: FinAgent says the request is outside its goals (unsupported) | Microsoft (MSFT) |  | who's microsoft's cfo these days |
| C119 | something about the company that a 10-K reader cannot answer (market data, people, news, opinions, writing tasks). Expected: FinAgent says the request is outside its goals (unsupported) | Nike (NKE) |  | draft a short email to my team summarizing the latest news on nike |
| C120 | a figure for a quarter or part of a year. Expected: FinAgent says only annual 10-K periods are supported (unsupported) | AT&T (T) | figure: cash and cash equivalents | AT&T cash and cash equivalents as of Q2 2025 |
