# ── Stage 1: lint ────────────────────────────────────────────────────────────
FROM python:3.11-slim AS lint
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir "ruff>=0.4.0"
COPY trustline/ trustline/
COPY api/ api/
RUN ruff check trustline/ api/

# ── Stage 2: test ────────────────────────────────────────────────────────────
FROM python:3.11-slim AS test
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"
COPY trustline/ trustline/
COPY api/ api/
COPY tests/ tests/
COPY eval/ eval/
RUN python -m pytest tests/ -x -q --no-header --co -q && \
    python -m pytest tests/ -x -q --no-header

# ── Stage 3: runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY trustline/ trustline/
COPY api/ api/
COPY eval/ eval/

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
