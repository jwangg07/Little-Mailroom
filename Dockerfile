# syntax=docker/dockerfile:1

FROM node:24-bookworm-slim AS frontend-build
WORKDIR /build/client
COPY client/package.json client/package-lock.json ./
RUN npm ci
COPY client/ ./
RUN npm run build && npm run lint

FROM python:3.12-slim-bookworm AS backend-build
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
WORKDIR /app/server
COPY server/pyproject.toml server/uv.lock ./
# main.py runs directly; the scaffolded "server" package isn't needed.
RUN uv sync --locked --no-dev --no-install-project --python /usr/local/bin/python

FROM backend-build AS test
RUN uv sync --locked --no-install-project --python /usr/local/bin/python
ENV PATH="/app/server/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1
COPY server/main.py server/auth_store.py server/google_secret.py server/credential_vault.py server/gmail_service.py server/config.py server/security.py ./
COPY server/routes/ ./routes/
COPY server/tests/ ./tests/
CMD ["sh", "-c", "python -m pytest tests -q && ruff check *.py routes tests && ruff format --check *.py routes tests"]

FROM python:3.12-slim-bookworm AS runtime
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:1.1.0 /lambda-adapter /opt/extensions/lambda-adapter
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/server/.venv/bin:$PATH" \
    AWS_LWA_PORT=8000 \
    AWS_LWA_READINESS_CHECK_PATH=/health \
    AWS_LWA_READINESS_CHECK_HEALTHY_STATUS=200 \
    AWS_LWA_ASYNC_INIT=true
RUN groupadd --gid 10001 mailroom \
    && useradd --uid 10001 --gid mailroom --no-create-home mailroom
WORKDIR /app/server
COPY --from=backend-build /app/server/.venv /app/server/.venv
COPY server/main.py server/auth_store.py server/google_secret.py server/credential_vault.py server/gmail_service.py server/config.py server/security.py ./
COPY server/routes/ ./routes/
COPY --from=frontend-build /build/client/dist /app/client/dist
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).close()"]
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
