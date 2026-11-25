# FinAgent demo image (Phase 9 cut-down; BC33): the Streamlit app, local and offline.
#
# Not built here: there is no Docker on the development Mac, and the public deployment was cut
# (#322). Build it from a working copy that holds the git-ignored pinned models (models/), the
# embedding cache (derived/embeddings) and the pinned runs listed below (runs/):
#
#   docker build -t finagent-demo .
#   docker run --rm -p 7860:7860 finagent-demo        # then open http://localhost:7860
#
# The build fails if any shipped model or the evidence manifest differs from its sha256
# (python -m finagent.ui.verify). Evidence is read-only for the user the app runs as.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

# 1. Dependencies, pinned: CPU-only torch from the PyTorch CPU index first, then the rest.
COPY docker/requirements-demo.txt docker/requirements-demo.txt
RUN pip install --no-cache-dir torch==2.15.0 --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r docker/requirements-demo.txt

# 2. The package (its dependencies are already installed above).
COPY pyproject.toml ./
COPY finagent/ finagent/
RUN pip install --no-cache-dir --no-deps .

# 3. Configs, the pinned evidence and the shipped models (M1 v3, intent v3, embedder,
#    reranker; not M2, M3, the calibrators or the E4 judge).
COPY configs/ configs/
COPY evidence/ evidence/
COPY datasets/rows_v3.meta.json datasets/rows_v3.meta.json
COPY models/m1_v3.joblib models/m1_v3.meta.json models/intent_v3.joblib models/intent_v3.meta.json models/
COPY models/hf/bge-small-en-v1.5/ models/hf/bge-small-en-v1.5/
COPY models/hf/ms-marco-MiniLM-L-6-v2/ models/hf/ms-marco-MiniLM-L-6-v2/
COPY derived/embeddings/ derived/embeddings/

# 4. What the Company and Results pages read: the latest pinned runs, the experiments and the
#    limitations. Update these lines when a newer run is pinned.
COPY runs/workbooks-0009/ runs/workbooks-0009/
COPY runs/forecast-0007/checks.csv runs/forecast-0007/checks.csv
COPY runs/credit-0003/ runs/credit-0003/
COPY runs/insights-0004/cards.json runs/insights-0004/cards.json
COPY runs/e4-0001/metrics.json runs/e4-0001/metrics.json
COPY runs/e4-0002/metrics.json runs/e4-0002/metrics.json
COPY runs/e7-0001/ runs/e7-0001/
COPY eval/experiments.csv eval/experiments.csv
COPY docs/limitations.md docs/limitations.md

# 5. Verify every shipped model and the evidence manifest by sha256; make the evidence
#    read-only; run as an unprivileged user (uid 1000) who can write only the embedding cache.
RUN python -m finagent.ui.verify \
 && useradd --create-home --uid 1000 app \
 && chmod -R a-w evidence \
 && chown -R app:app derived

USER app

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/_stcore/health', timeout=4)"]

ENTRYPOINT ["streamlit", "run", "finagent/ui/app.py", "--server.port=7860", "--server.address=0.0.0.0"]
