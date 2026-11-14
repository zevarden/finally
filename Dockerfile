# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1: build the Next.js static export (frontend/out)
# ---------------------------------------------------------------------------
FROM node:22-slim AS frontend

WORKDIR /app/frontend

# Install deps first (cached unless the lockfile changes)
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build


# ---------------------------------------------------------------------------
# Stage 2: Python runtime (FastAPI + uvicorn), serves API and static files
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /uvx /usr/local/bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/backend/.venv \
    FINALLY_IN_DOCKER=1 \
    PATH="/app/backend/.venv/bin:${PATH}"

WORKDIR /app/backend

# Dependencies only (cached unless pyproject/uv.lock change)
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project

# Application code, then install the project itself
COPY backend/README.md ./
COPY backend/app ./app
RUN uv sync --frozen --no-dev

# Static frontend export
COPY --from=frontend /app/frontend/out ./static

# SQLite lives on the named volume mounted here
RUN mkdir -p /app/db
VOLUME ["/app/db"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
