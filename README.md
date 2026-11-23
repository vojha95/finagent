# FinAgent

Evidence-grounded financial analysis of SEC 10-K filings. Design: `docs/blueprint.md`.

## Setup
    python -m venv .venv
    .venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
    pip install -e ".[dev]"
    pytest -q
