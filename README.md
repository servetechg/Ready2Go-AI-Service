# Ready2Go AI Service

Vector-backed **continuity integrity scoring** + **bounded continuity-audit
summaries** for the Ready2Go (earth-quick-alert) platform. It replaces the
inline OpenAI calls in the Next.js app with a scalable, cost-controlled service.

- **Architecture:** see `earth-quick-alert/docs/PYTHON_AI_SERVICE_ARCHITECTURE.md`
- **Progress checklist:** see `earth-quick-alert/docs/PYTHON_AI_SERVICE_CHECKLIST.md`

> Status: **M0 scaffold** — runnable API skeleton (health + auth). The analysis,
> vector, scoring, and summary modules are wired as stubs and filled in by later
> steps.

## Tech

- Python **3.12**, **FastAPI** + **uvicorn**
- Tooling: **`uv`** (no `requirements.txt` — `pyproject.toml` + `uv.lock`)
- OpenAI (`text-embedding-3-small`, `gpt-4o-mini`), Weaviate, Postgres

## Prerequisites

Install `uv` (once):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# add to PATH for the current shell if needed:
source $HOME/.local/bin/env
```

## Setup & run

```bash
# 1. Install deps (uv fetches Python 3.12, creates .venv, writes uv.lock)
uv sync

# 2. Configure
cp .env.example .env   # then fill in real values

# 3. Run the API
uv run main            # serves on http://localhost:8000
```

Quick checks:

```bash
curl localhost:8000/healthz          # {"status":"ok"}
curl localhost:8000/readyz           # dependency report
open  localhost:8000/docs            # OpenAPI / Swagger UI
```

## Develop

```bash
uv run pytest          # tests
uv run ruff check .    # lint
uv run ruff format .   # format
uv run mypy app        # type-check
```

## Local stack (optional)

Brings up the API plus a local Postgres and Weaviate:

```bash
docker compose up --build
```

## Project layout

```
app/
  main.py        FastAPI app + `run()` (the `uv run main` target)
  config.py      env-driven settings
  security.py    bearer + HMAC auth for /v1
  schemas.py     request/response contract
  api/           health (live), integrity & audit (contract stubs -> 501)
  ingest/        (Step 2) fetch, extract, chunk
  vectors/       (Step 3) Weaviate client, schema, repo
  scoring/       (Step 4) signals, composite, thresholds
  store/         (Step 5) cache, audit_state, call log (SQLAlchemy)
  summary/       (Steps 6-7) per-doc + bounded audit summaries
  llm/           OpenAI chat + embeddings wrappers
```
