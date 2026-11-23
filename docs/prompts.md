# Prompts for Claude Code, by phase

Run one prompt per session. Type `/clear` between components. Always approve the plan before letting it write code.

---

## Phase 0: Foundation

### 0.1 Orientation (no code)
> Read CLAUDE.md and docs/blueprint.md sections 1, 6, 7, 8.1, 12, 13 and 15. In 10 bullet points or fewer, summarise what FinAgent is, the rules you must follow, and what Phase 0 must deliver. Do not write any code.

Check its summary. If it misstates a rule, correct it now.

### 0.2 Inspect the evidence (no code changes)
> Inspect evidence/manifest.json and list the files by company and type. Tell me the manifest's structure (keys per entry). Do not modify anything.

### 0.3 Evidence store: plan
> Plan the evidence store from blueprint section 8.1, using the FilingRecord schema in section 13. Scope: (a) read a snapshot, decompress it and verify its SHA-256 against the manifest; (b) ticker → CIK resolution from company_tickers.json; (c) load each company's 10-K filings from submissions JSON: form 10-K only, dedupe by period end keeping the first filed, last five fiscal years plus the preceding year for start dates, consecutive years required; (d) as_of filtering; (e) fiscal year from the filing's dei:DocumentFiscalYearFocus. Propose files, functions, dataclasses and tests. Do not write code yet.

### 0.4 Evidence store: build
> Implement the approved plan in finagent/evidence/ with tests in tests/. Tests must cover: hash mismatch raises; AMZN and MSFT resolve to CIK 1018724 and 789019; five consecutive fiscal years load for each; an as_of date before the latest filing excludes it; Microsoft's fiscal year matches the filing's own label. Run pytest -q and show the output.

Then: review the diff, commit ("Phase 0: evidence store with hash and as-of checks"), and add a row to docs/contribution_log.md.

---

## Phase 1: Readings

### 1.1 Tag store
> Plan, then build, the tag store from blueprint section 8.2: parse xbrli:context (period, explicit dimensions), USD units, and every ix:nonFraction (concept, context, unit, scale, sign, zero-dash format, Decimal amount, and its table cell address: table index, row index, column index, and the row label using the first-cell rule). Output a list of TagFact records (section 13). Test on Amazon FY2025: total net sales and purchases of property and equipment are found with the right amounts and signs.

### 1.2 Clean document + leakage test
> Build the clean-document function from section 8.2: unwrap every ix:* element keeping its text, delete ix:header and ix:hidden. Add a test asserting the clean HTML contains no "ix:", no "us-gaap:" and none of the concept names from the tag store.

### 1.3 Tag channel
> Build the tag channel from section 8.6: Company Facts primary lookup (form, accession, start, end, filed must match; exactly one distinct value or CONFLICT; none → MISSING_PRIMARY), inline corroboration (same amount and concept, non-dimensional context, else CONFLICT), and the inline-only fallback flagged single-source. Return Outcome records with statuses from section 6.1. Use the 9 core metrics and concept candidates from Appendix A for now, loaded from configs/concept_map.yaml.

### 1.4 Row builder
> Build statement location and the row builder from section 8.3 for the clean document. Output RowInstance records. Exit test (section 15, phase 1): for Amazon, Microsoft and one more company, all 5 years: three statements found, and at least 95% of tagged statement cells map to a row.

---

## Phase 2 onward
Use this template for each component:

> Read docs/blueprint.md section [X] (and schemas in section 13, evaluation in section 10 if relevant). Propose a plan: files, functions, configs, tests, and how the exit criterion in section 15 will be checked. Do not write code yet.

After you approve:

> Implement the approved plan with tests. Run pytest -q and show the output. Summarise what changed and suggest a commit message.

## Review prompts (use any time)
- "Explain [function] line by line as if I will be examined on it."
- "Which blueprint rules does this change touch? Show where each is enforced."
- "Write a test that would fail if someone added company-specific code here."
