# FinAgent: instructions for Claude Code

## What this project is
FinAgent reads US 10-K filings in two independent ways. A trained model reads the visible statement tables (the document channel) and a parser reads the inline XBRL tags (the tag channel). Values are verified when the two agree. The project then builds an integrated three-statement model, forecast, scenarios, credit and liquidity analysis, and risk signals with quoted evidence. A planner answers questions, and an observation gate checks every answer.

**Source of truth: `docs/blueprint.md`** (Version 3, as built, 2026-11-26: approved changes BC1-BC37 folded in, planned parts not built marked "Not built"). Read the relevant section before any task. Section numbers in prompts refer to that file; they are the same as in Version 2. Version 2, the plan, is kept unchanged in `docs/blueprint_v2.md`.

## Non-negotiable rules
1. Follow the blueprint. If a task seems to need a design change, stop and ask. Do not improvise.
2. Never modify anything in `evidence/`. It is pinned SEC data, read-only and hash-checked.
3. No company-specific code. Never write `if ticker == ...` or anything like it. Put company differences in `configs/*.yaml` as general rules.
4. Missing is never zero. Represent absent values as gaps (None plus a status), never 0. Never insert a balancing "plug".
5. Use `decimal.Decimal` for reported amounts. Use floats only in model calculations.
6. Every tool takes an `as_of` date. Never use a filing filed after it.
7. The document channel must never see XBRL tags, concept names or tagged values. One exception (blueprint 8.3 step 1; docs/blueprint_changes.md BC6): statement location may receive a per-table count of tagged cells (`{table_index: count}`), computed outside `finagent/document`. In evaluation mode, tags are hidden from the Correlation Agent.
11. Approved deviations from the blueprint are recorded in `docs/blueprint_changes.md`. BC1-BC37 are folded into Version 3; a later approved change takes precedence over the blueprint text until it is folded in. Log every problem met, with its cause and fix, in `docs/issues_log.md`.
8. Every outcome uses the status and classification vocabulary in blueprint section 6 (VERIFIED, GAP, CONFLICT, INVALID, REPORTED_FACT, CALCULATION, ...).
9. No external AI API calls anywhere in `finagent/`.
10. Logic lives in the `finagent/` package. Notebooks only import from it. Never execute notebook cells as code.

## Working method
- One component per task. Before writing code, propose a short plan: files, functions, tests. Wait for approval.
- Every public function gets a pytest test. Run `pytest -q` before saying a task is done, and show the result.
- Keep functions small and typed (type hints, dataclasses or pydantic for records in blueprint section 13).
- No hard-coded counts such as "exactly 45 facts". Check completeness per item instead.
- After finishing, summarise in 5 lines or fewer: what changed, the test result, and anything left open. Suggest a commit message.
- If unsure about a financial or accounting point, ask. The user is a finance professional.

## Commands
- Activate the environment: `.venv\Scripts\activate` (Windows) or `source .venv/bin/activate`
- Install: `pip install -e ".[dev]"`
- Test: `pytest -q`

## Layout (grow it as phases progress; see blueprint section 12)
- `finagent/`: the package
- `tests/`: tests mirroring the package
- `configs/`: YAML for taxonomy, concept map, derivations, identities, scenarios, signals, intents
- `evidence/`: pinned SEC snapshots + `manifest.json` (read-only)
- `eval/`: evaluation scripts E1–E8 and `experiments.csv`
- `notebooks/`: exploration and demo only
- `docs/`: blueprint, contribution log

## Current phase
Phase 10: Final (blueprint section 15). Phases 0-9 complete; E1-E8 recorded in docs/final_results.md (held-out E1: M1 v3 0.913, M3 not shipped; E6: no accuracy claim; E8: 0 fabricated).
Product frozen: local Streamlit app over serving-0002 (all 48 companies, split-labelled; BC36, BC37); Dockerfile unbuilt; no public deployment (BC33).
Phase 10 freeze (#378): the deck is committed (docs/FinAgent_deck.pptx, df87427); the outside-user session was dropped (Vineet's decision, 2026-11-26; #377). Open: the demo recording (runbook: blueprint 17.4) and rehearsals. Documentation only unless Vineet decides otherwise.
