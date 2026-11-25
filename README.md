# FinAgent

Evidence-grounded financial analysis of SEC 10-K filings. Design: `docs/blueprint.md`.

## Setup
Requires Python 3.11 (matches the Docker image in blueprint section 11.3).

    python -m venv .venv
    .venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
    pip install -e ".[dev]"
    pytest -q

Optional dependency groups follow blueprint section 12 and can be added as phases need them:
`ml` (scikit-learn, LightGBM), `dl` (torch, transformers), `retrieval`, `excel`, `serve`,
`llm` (llama-cpp-python, optional), or `all` (everything except `llm`), e.g. `pip install -e ".[all]"`.

macOS note: LightGBM needs an OpenMP runtime (`libomp`), e.g. `brew install libomp` or
`conda install llvm-openmp` in the base interpreter's env. If LightGBM segfaults after
`import torch`, point torch's bundled `torch/lib/libomp.dylib` at the same libomp.

## Run the demo
A local Streamlit app over the pinned evidence (Phase 9 cut-down, BC33). It runs offline and
serves the 8 validation companies only (AMZN, MSFT, CVX, NKE, MRK, T, CL, ABT); a question that
names a held-out or any other company is refused before anything runs. No external AI API is
called.

**What it needs** (git-ignored, so present only in a working copy that built them):
- the pinned models: `models/m1_v3.joblib`, `models/intent_v3.joblib` (with their `.meta.json`)
  and `models/hf/` (embedder and reranker; `python -m finagent.retrieval.models verify`);
- the pinned runs the Company and Results pages read: the latest `runs/workbooks-*`,
  `runs/forecast-*`, `runs/credit-*` and `runs/insights-*`, plus `runs/e4-0001`, `runs/e4-0002`
  and `runs/e7-0001`.

**Run it:**

    source .venv/bin/activate
    pip install -e ".[dev,ml,dl,retrieval,excel,serve]"
    python -m finagent.ui.verify        # every shipped model and the manifest match their sha256
    streamlit run finagent/ui/app.py    # opens http://localhost:8501

**The sidebar** picks the page, the company and the as-of date. The as-of date defaults to
2026-11-24; its maximum is the evidence snapshot date.

**The pages:**
- **Ask:** a question box, six example questions, the answer with its status, the values with
  their verification (VERIFIED, SINGLE_CHANNEL, ...), and the observation gate. A trace expander
  shows goals, tool calls and sources by accession. The trace can be downloaded as JSON; it is
  never saved.
- **Company:** the statements (historicals and forecast in one table per statement), under a
  Base/Moderate/Severe selector. Also: the F1-F5 checks; credit and liquidity (scoped DSCR,
  headroom on both bases); the workbook download; and the latest year's insight cards. A
  low-confidence sentence is labelled as such and is never evidence. This page shows the pinned
  runs, which were built as of 2026-11-24.
- **Results:** every experiment's headline from `eval/experiments.csv` and the runs, with two
  charts (E1 by model, E5 M0 vs M1), the E7 latency run and the limitations
  (`docs/limitations.md`).

**Light E7** (latency on 20 fixed E5 questions, cold then warm):

    python eval/e7_light.py run --run-id e7-0002

**Docker** (`Dockerfile`; not built here, because there is no Docker on the development Mac).
It is Python 3.11 slim with CPU-only torch. It verifies the models and the evidence manifest by
sha256 at build, makes the evidence read-only, and serves on port 7860. Build it from a working
copy that holds the pinned models, `derived/embeddings` and the runs listed in the Dockerfile:

    docker build -t finagent-demo .
    docker run --rm -p 7860:7860 finagent-demo      # http://localhost:7860
