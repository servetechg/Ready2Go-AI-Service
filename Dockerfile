# syntax=docker/dockerfile:1

# ---- Builder: resolve + install deps into a venv with uv -------------------
FROM python:3.12-slim AS builder

# Bring in the uv binary.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install dependencies first (cached layer), without the project code.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Now add the project source and install it.
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---- Runtime: slim image with just the venv + app --------------------------
FROM python:3.12-slim AS runtime

RUN groupadd --system app && useradd --system --gid app --create-home app
WORKDIR /app

COPY --from=builder --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PORT=8000

USER app
EXPOSE 8000

# `main` is the console script defined in pyproject.toml.
CMD ["main"]
