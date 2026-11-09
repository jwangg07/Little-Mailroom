# syntax=docker/dockerfile:1

FROM node:24-bookworm-slim AS frontend-build
WORKDIR /build/client
COPY client/package.json client/package-lock.json ./
RUN npm ci
COPY client/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS backend-build
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
WORKDIR /app/server
COPY server/pyproject.toml server/uv.lock ./
# main.py runs directly; the scaffolded "server" package isn't needed.
RUN uv sync --locked --no-dev --no-install-project --python /usr/local/bin/python

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/server/.venv/bin:$PATH"
RUN groupadd --gid 10001 mailroom \
    && useradd --uid 10001 --gid mailroom --no-create-home mailroom
WORKDIR /app/server
COPY --from=backend-build /app/server/.venv /app/server/.venv
COPY server/main.py ./main.py
COPY --from=frontend-build /build/client/dist /app/client/dist
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).close()"]
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
