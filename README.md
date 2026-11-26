# FinAgent

FinAgent reads US 10-K filings in two independent ways: a trained model reads the visible statement tables, and a parser reads the inline XBRL tags. A value is VERIFIED when the two agree; a missing or conflicting value is reported as a gap, never filled.
From the verified statements it builds an integrated three-statement model with a forecast, three scenarios, credit and liquidity measures, and risk signals with quoted evidence.
A planner answers questions in plain English, and an observation gate checks every value in an answer against its source filing and the as-of date.
Everything runs offline on pinned SEC evidence; no external AI API is called.
Design: `docs/blueprint.md`, with approved changes in `docs/blueprint_changes.md`. Results: `docs/final_results.md`.

## Install

Python 3.11 (the version of the Docker image).

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,ml,dl,retrieval,excel,serve]"
pytest -q
```

On Windows, activate with `.venv\Scripts\activate`. `pip install -e ".[all]"` installs the same groups. The optional `llm` group is not used.

macOS note: LightGBM needs an OpenMP runtime (`libomp`), e.g. `brew install libomp` or `conda install llvm-openmp` in the base interpreter's env. If LightGBM segfaults after `import torch`, point torch's bundled `torch/lib/libomp.dylib` at the same libomp.

## Run the app

A local Streamlit app over the pinned evidence and runs. It serves all 48 pinned companies, each labelled by its split (validation: measured; train: in-sample; held-out: measured once in held-out E1). Pages: **Ask** (questions, answers, the gate and the trace), **Company** (statements, forecast and checks under three scenarios, credit and liquidity, risk cards, the workbook) and **Results** (every evaluation's headline, E7 latency and the limitations).

```bash
python -m finagent.ui.verify
streamlit run finagent/ui/app.py
```

The first command checks every shipped model and the evidence manifest against their sha256. The app opens at http://localhost:8501 and loads its models once at start.

**What it needs** (git-ignored, so present only in a working copy that built them): the pinned models (`models/m1_v3.joblib`, `models/intent_v3.joblib` with their `.meta.json`, and `models/hf/`, checked with `python -m finagent.retrieval.models verify`); the passage-embedding cache (`derived/embeddings`); and the runs the pages read: the serving run named in `configs/ui.yaml` (`serving.run`, built with `python -m finagent.ui.serving build --run-id <id>`), `runs/serving-0001/serving.json`, `runs/e4-0001`, `runs/e4-0002`, `runs/e7-0001` and `runs/e7-0002`.

At the serving run's as-of date the Ask page reuses that run's statement sets, but only while the code (all of `finagent/` except `finagent/ui`), the configs (except `ui.yaml`), models, evidence, platform and packages match the ones it was built with (BC37; never the git commit). Otherwise it builds them live. The trace says which path each company took; `python -m finagent.ui.snapshot check --serving-run <id>` says whether the snapshot is usable.

**Docker** (`Dockerfile`; shipped unbuilt, because there is no Docker on the development Mac): Python 3.11 slim with CPU-only torch; models and the evidence manifest verified by sha256 at build; evidence read-only; port 7860. Build it from a working copy that holds the git-ignored files above. In the container the Ask page builds statement sets live, because the Mac-built snapshot cannot match there (docs/limitations.md).

```bash
docker build -t finagent-demo .
docker run --rm -p 7860:7860 finagent-demo
```

## Reproduce the evaluations

Each command below is the one behind the named run, at as-of 2026-11-24 on the validation companies. Results depend on the code, so check out the run's commit (listed in `eval/experiments.csv` and in `docs/final_results.md`) to reproduce it exactly, and give a new run id where the runner takes one, so the recorded run is kept. Most runners take `--record` to append a row to `eval/experiments.csv`.

| Eval | Run id | Command |
|---|---|---|
| E1 | e1-0008 | `python eval/e1_document.py --model m3 --dataset-version v3 --split validation --as-of 2026-11-24 --compare m1` |
| E2 | e2-0003 | `python eval/e2_agent.py --run-id e2-0003 --as-of 2026-11-24 --agent-version v1.2 --compare-run e2-0002` |
| E3 | e3-0001 | `python -m finagent.evaluation.e3 score --run-id e3-0001` |
| E4 | e4-0001 | `python -m finagent.evaluation.e4 score --run-id e4-0001` |
| E5 | e5-0002 | `python eval/e5_planner.py run --run-id e5-0002 --compiler m1` |
| E6 | e6-0001 | `python eval/e6_backtest.py run --split validation --run-id e6-0001 --as-of 2026-11-24` |
| E7 | e7-0002 | `python eval/e7_light.py run --run-id e7-0002 --serving-run serving-0002` |
| E8 | e8-0004 | `python eval/e8_replays.py --run-id e8-0004 --as-of 2026-11-24 --statements-run statements-0009 --forecast-run forecast-0007 --report docs/e8_replays_0004.md` |

- **Held-out E1 (e1-0009, e1-0010) is not to be re-run.** It was pre-registered and run once at commit 089962e; its two commands are in `docs/heldout_e1_prereg.md` section 4. Held-out companies are never used for debugging, tuning or evaluation (BC36).
- **E5:** at commit 5dce109 the command runs intent model v2 (e5-0002, the headline); at later commits it runs the shipped intent model v3, as in the post-hoc e5-0003.
- **E3 and E4** score frozen labels in `gold/`; the labels themselves are Vineet's and are not regenerated.

## Repository layout

| Path | What it holds |
|---|---|
| `finagent/evidence`, `ixbrl`, `tags` | Pinned evidence store with sha256 and as-of checks; iXBRL splitting and the leakage check; the tag channel. |
| `finagent/document`, `dataset`, `training` | The document channel (statement location, rows, classifier, normaliser); the row dataset; training of M1-M3 and the intent model. |
| `finagent/verify`, `statements` | Dual-channel verification and the Correlation Agent; the validated statement set with derivations and identities. |
| `finagent/model`, `workbook`, `credit`, `risk` | Forecast, scenarios and checks; the Excel workbook; credit and liquidity; risk signals and insight cards. |
| `finagent/retrieval`, `planner`, `tools.py` | Evidence retrieval; the goal compiler, planner loop and observation gate; the tool layer with as-of dates. |
| `finagent/evaluation`, `ui` | E1-E8 harnesses and the default-path check; the Streamlit app, the serving build and its snapshot. |
| `configs/` | YAML for the universe, taxonomy, concept map, derivations, identities, scenarios, signals, intents, evaluations and the app. |
| `evidence/` | Pinned SEC snapshots and `manifest.json` (read-only, hash-checked). |
| `eval/` | Evaluation runners and `experiments.csv`, the record of every run. |
| `gold/` | Frozen hand labels and questions for E3, E4 and E5. |
| `docs/` | Blueprint, approved changes, results, limitations, issues log, contribution log, model cards and per-component reports. |
| `tests/` | Pytest tests mirroring the package. |
| `notebooks/` | Exploration only; notebooks import from the package. |
| `scripts/` | Documentation tooling: `md2docx.js` builds the Word version of the blueprint; `build_deck.js` builds the deck `docs/FinAgent_deck.pptx` (pptxgenjs, plus a theme helper named by the `PPTX_SKILL` environment variable, outside the repository). |

`models/`, `datasets/`, `derived/` and `runs/` are git-ignored outputs.

## Read more

- Results, E1 to E8, and the blueprint 4.2 checklist: [docs/final_results.md](docs/final_results.md)
- Limitations and fix paths: [docs/limitations.md](docs/limitations.md)
- Approved deviations from the blueprint: [docs/blueprint_changes.md](docs/blueprint_changes.md)
- Word version of the blueprint, [docs/FinAgent_Blueprint_v3.docx](docs/FinAgent_Blueprint_v3.docx); rebuild it after editing `docs/blueprint.md` with Node.js and two npm packages kept out of the repository: `npm install --prefix /tmp/md2docx docx@9.5.1 marked@15.0.12`, then `NODE_PATH=/tmp/md2docx/node_modules node scripts/md2docx.js docs/blueprint.md docs/FinAgent_Blueprint_v3.docx "FinAgent · Project Blueprint · Version 3 (as built), 26 November 2026"`.
- Every problem met, with its cause and fix: [docs/issues_log.md](docs/issues_log.md)
- Who did what: [docs/contribution_log.md](docs/contribution_log.md)
