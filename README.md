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
