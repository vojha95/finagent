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
| C010 | a reported figure from the 10-K for one fiscal year | Amazon (AMZN) | figure: operating income; compare with: Colgate-Palmolive (CL); fiscal year: FY2023 |  |
| C011 | a reported figure from the 10-K for one fiscal year | Colgate-Palmolive (CL) | figure: inventory; compare with: Chevron (CVX); fiscal year: FY2024 |  |
| C012 | a reported figure from the 10-K for one fiscal year | Chevron (CVX) | figure: accounts receivable; year: the latest reported year (do not name the year) |  |
| C013 | a reported figure from the 10-K for one fiscal year | Merck (MRK) | figure: income tax expense; year: the latest reported year (do not name the year) |  |
| C014 | a reported figure from the 10-K for one fiscal year | Microsoft (MSFT) | figure: free cash flow; fiscal year: FY2026; selected in the app: Microsoft (MSFT) (the question need not name it) |  |
| C015 | a reported figure from the 10-K for one fiscal year | Nike (NKE) | figure: dividends paid; fiscal year: FY2022 |  |
| C016 | a reported figure from the 10-K for one fiscal year | AT&T (T) | figure: revenue; fiscal year: FY2022 |  |
| C017 | a reported figure from the 10-K for one fiscal year | Abbott (ABT) | figure: net income; fiscal year: FY2023 |  |
| C018 | a reported figure from the 10-K for one fiscal year | Amazon (AMZN) | figure: cash flow from operations; fiscal year: FY2024 |  |
| C019 | a reported figure from the 10-K for one fiscal year | Colgate-Palmolive (CL) | figure: total assets; fiscal year: FY2025 |  |
| C020 | a reported figure from the 10-K for one fiscal year | Chevron (CVX) | figure: long-term debt; fiscal year: FY2021 |  |
| C021 | a reported figure for one fiscal year, with another number in the question that could be mistaken for a year (an amount, a count, a page or note number). Expected: one reported-figure answer for the named fiscal year | Merck (MRK) | figure: capital expenditure; fiscal year: FY2022 |  |
| C022 | the same figure for two companies side by side, without any word asking for a comparison. Expected: FinAgent asks you to name one company or ask for a comparison (wrong_company) | Microsoft (MSFT) | figure: gross profit; second company: Nike (NKE); fiscal year: FY2024 |  |
| C023 | a reported figure for the latest year, said in words (do not name the fiscal year). Expected: one reported-figure answer for the latest fiscal year | Nike (NKE) | figure: cash and cash equivalents; year: the latest reported year (do not name the year) |  |
| C024 | a reported figure, using in its ordinary sense a word FinAgent also uses for another kind of request (forecasting, scenarios, explanations, checks, risk). Expected: one reported-figure answer | AT&T (T) | figure: total shareholders' equity; fiscal year: FY2024 |  |

### statements

| Cell | What to ask | Company | Details | Your question |
|---|---|---|---|---|
| C028 | a check that the company's three financial statements reconcile (tie out) for a year | Chevron (CVX) | year: the latest reported year (do not name the year) |  |
| C029 | a check that the company's three financial statements reconcile (tie out) for a year | Merck (MRK) | year: the latest reported year (do not name the year) |  |
| C030 | a check that the company's three financial statements reconcile (tie out) for a year | Microsoft (MSFT) | fiscal year: FY2024 |  |
| C031 | a check that the company's three financial statements reconcile (tie out) for a year | Nike (NKE) | fiscal year: FY2025 |  |
| C032 | a check that the company's three financial statements reconcile (tie out) for a year | AT&T (T) | fiscal year: FY2025 |  |

### forecast

| Cell | What to ask | Company | Details | Your question |
|---|---|---|---|---|
| C040 | a projected figure from FinAgent's three-year forecast | AT&T (T) | figure: total debt; projected year: FY2028; scenario: do not name one; selected in the app: AT&T (T) (the question need not name it) |  |
| C041 | a projected figure from FinAgent's three-year forecast | Abbott (ABT) | figure: revenue; projected year: none named (the first projected year); scenario: Base |  |
| C042 | a projected figure from FinAgent's three-year forecast | Amazon (AMZN) | figure: free cash flow; projected year: none named (the first projected year); scenario: Moderate |  |
| C043 | a projected figure from FinAgent's three-year forecast | Colgate-Palmolive (CL) | figure: closing cash; projected year: FY2026; scenario: Severe |  |
| C044 | a projected figure from FinAgent's three-year forecast | Chevron (CVX) | figure: net income; projected year: FY2027; scenario: do not name one |  |
| C045 | a projected figure from FinAgent's three-year forecast | Merck (MRK) | figure: EBIT; projected year: FY2028; scenario: Base |  |
| C046 | a forecast for a fiscal year that has already been reported. Expected: a forecast goal for that year, answered as a gap (the year is not projected) | Microsoft (MSFT) | figure: capital expenditure; year to forecast: FY2026; scenario: Moderate |  |
| C047 | a forecast for a fiscal year beyond the three projected years. Expected: a forecast goal for that year, answered as a gap (outside the horizon) | Nike (NKE) | figure: total debt; year to forecast: FY2030; scenario: Severe |  |
| C048 | a forecast under a stressed or downside case without saying which scenario. Expected: FinAgent asks which scenario (Base, Moderate or Severe) (unsupported) | AT&T (T) | figure: revenue; projected year: FY2028; scenario: do not name one |  |

### credit

| Cell | What to ask | Company | Details | Your question |
|---|---|---|---|---|
| C054 | a historical credit or financial ratio for one fiscal year | Microsoft (MSFT) | figure: total liabilities to total assets; year: the latest reported year (do not name the year) |  |
| C055 | a historical credit or financial ratio for one fiscal year | Nike (NKE) | figure: operating cash flow to current liabilities; year: the latest reported year (do not name the year) |  |
| C056 | a historical credit or financial ratio for one fiscal year | AT&T (T) | figure: none named (let FinAgent choose); fiscal year: FY2025 |  |
| C057 | a historical credit or financial ratio for one fiscal year | Abbott (ABT) | figure: days sales outstanding; fiscal year: FY2021 |  |
| C058 | a historical credit or financial ratio for one fiscal year | Amazon (AMZN) | figure: EBIT margin; fiscal year: FY2022 |  |
| C059 | a historical credit or financial ratio for one fiscal year | Colgate-Palmolive (CL) | figure: current ratio; fiscal year: FY2023 |  |
| C060 | a historical ratio "under" a named scenario. Expected: FinAgent says scenarios apply only to projections (unsupported) | Chevron (CVX) | figure: quick ratio; fiscal year: FY2024; scenario: Severe |  |

### liquidity

| Cell | What to ask | Company | Details | Your question |
|---|---|---|---|---|
| C066 | the company's liquidity position (headroom or shortfall) in a projected year | Amazon (AMZN) | figure: none named (let FinAgent choose); projected year: FY2026; scenario: Moderate |  |
| C067 | the company's liquidity position (headroom or shortfall) in a projected year | Colgate-Palmolive (CL) | figure: none named (let FinAgent choose); projected year: FY2027; scenario: Severe |  |
| C068 | the company's liquidity position (headroom or shortfall) in a projected year | Chevron (CVX) | figure: none named (let FinAgent choose); projected year: FY2028; scenario: do not name one |  |
| C069 | the company's liquidity position (headroom or shortfall) in a projected year | Merck (MRK) | figure: liquidity shortfall; projected year: none named (the first projected year); scenario: Base |  |
| C070 | the company's liquidity position (headroom or shortfall) in a projected year | Microsoft (MSFT) | figure: liquidity headroom; projected year: none named (the first projected year); scenario: Moderate |  |
| C071 | the company's liquidity position (headroom or shortfall) in a projected year | Nike (NKE) | figure: liquidity shortfall; projected year: FY2027; scenario: Severe |  |
| C072 | liquidity in a projected year without naming any scenario. Expected: the Base scenario is used and the answer says so | AT&T (T) | figure: liquidity headroom; projected year: FY2027; scenario: do not name one |  |

### debt

| Cell | What to ask | Company | Details | Your question |
|---|---|---|---|---|
| C077 | whether the company can meet its scheduled debt service in a projected year | Merck (MRK) | projected year: none named (the first projected year); scenario: Base |  |
| C078 | whether the company can meet its scheduled debt service in a projected year | Microsoft (MSFT) | projected year: FY2029; scenario: Moderate; ask for liquidity, and for debt service only if there is a shortfall |  |
| C079 | whether the company can meet its scheduled debt service in a projected year | Nike (NKE) | projected year: FY2027; scenario: Severe; ask for liquidity, and for debt service only if there is a shortfall |  |
| C080 | debt service only if there is a liquidity shortfall, without asking for liquidity itself. Expected: FinAgent asks for the liquidity assessment the condition depends on (unsupported) | AT&T (T) | projected year: FY2027; scenario: do not name one |  |

### explain

| Cell | What to ask | Company | Details | Your question |
|---|---|---|---|---|
| C085 | why a reported figure changed in a fiscal year compared with the year before | Merck (MRK) | figure: gross profit; year: the latest reported year (do not name the year) |  |
| C086 | why a reported figure changed in a fiscal year compared with the year before | Microsoft (MSFT) | figure: revenue; fiscal year: FY2023 |  |
| C087 | why a reported figure changed in a fiscal year compared with the year before | Nike (NKE) | figure: net income; fiscal year: FY2024 |  |
| C088 | why a reported figure changed in a fiscal year compared with the year before | AT&T (T) | figure: operating income; fiscal year: FY2024 |  |

### risk

| Cell | What to ask | Company | Details | Your question |
|---|---|---|---|---|
| C092 | the risk signals (warning signs) in the company's latest filings | Chevron (CVX) | year: the latest reported year (do not name the year) |  |
| C093 | the risk signals (warning signs) in the company's latest filings | Merck (MRK) | year: the latest reported year (do not name the year) |  |
| C094 | the risk signals (warning signs) in the company's latest filings | Microsoft (MSFT) | fiscal year: FY2024 |  |
| C095 | the risk signals (warning signs) in the company's latest filings | Nike (NKE) | fiscal year: FY2025 |  |
| C096 | the risk signals (warning signs) in the company's latest filings | AT&T (T) | fiscal year: FY2025 |  |

### advice

| Cell | What to ask | Company | Details | Your question |
|---|---|---|---|---|
| C100 | investment advice about the company (buy, sell, hold, value, price target, allocation). Expected: FinAgent declines investment advice (advice) | Chevron (CVX) |  |  |
| C101 | investment advice about the company (buy, sell, hold, value, price target, allocation). Expected: FinAgent declines investment advice (advice) | Merck (MRK) |  |  |
| C102 | investment advice about the company (buy, sell, hold, value, price target, allocation). Expected: FinAgent declines investment advice (advice) | Microsoft (MSFT) |  |  |

### two_periods

| Cell | What to ask | Company | Details | Your question |
|---|---|---|---|---|
| C105 | one figure for two named fiscal years in a single request. Expected: FinAgent asks for one fiscal year per request (two_periods) | Abbott (ABT) | figure: accounts receivable; years: FY2023 and FY2024 |  |
| C106 | one figure for two named fiscal years in a single request. Expected: FinAgent asks for one fiscal year per request (two_periods) | Amazon (AMZN) | figure: income tax expense; years: FY2024 and FY2025 |  |
| C107 | one figure over a span of fiscal years (a range). Expected: FinAgent asks for one fiscal year per request (two_periods) | Colgate-Palmolive (CL) | figure: free cash flow; years: FY2021 to FY2025 |  |
| C108 | one figure over several years without naming them. Expected: FinAgent asks for one fiscal year per request (two_periods) | Chevron (CVX) | figure: dividends paid |  |

### wrong_company

| Cell | What to ask | Company | Details | Your question |
|---|---|---|---|---|
| C111 | a supported request about a validation company other than the one selected in the app. Expected: FinAgent says the question names a different company (wrong_company) | AT&T (T) | figure: cash flow from operations; fiscal year: FY2024; selected in the app: Nike (NKE) |  |
| C112 | a supported request about a validation company other than the one selected in the app. Expected: FinAgent says the question names a different company (wrong_company) | Abbott (ABT) | figure: total assets; fiscal year: FY2024; selected in the app: AT&T (T) |  |
| C113 | a supported request about a well-known US listed company that is not in FinAgent's universe. Expected: FinAgent says the company is not covered (wrong_company) | one outside the universe (your choice) | figure: long-term debt; fiscal year: FY2025 |  |
| C114 | two separate requests about two companies in one question, not a comparison. Expected: FinAgent asks you to name one company (wrong_company) | Amazon (AMZN) | figure: capital expenditure; second company: Colgate-Palmolive (CL); fiscal year: FY2021 |  |

### unsupported

| Cell | What to ask | Company | Details | Your question |
|---|---|---|---|---|
| C118 | something about the company that a 10-K reader cannot answer (market data, people, news, opinions, writing tasks). Expected: FinAgent says the request is outside its goals (unsupported) | Microsoft (MSFT) |  |  |
| C119 | something about the company that a 10-K reader cannot answer (market data, people, news, opinions, writing tasks). Expected: FinAgent says the request is outside its goals (unsupported) | Nike (NKE) |  |  |
| C120 | a figure for a quarter or part of a year. Expected: FinAgent says only annual 10-K periods are supported (unsupported) | AT&T (T) | figure: cash and cash equivalents |  |
