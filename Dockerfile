# syntax=docker/dockerfile:1

FROM node:24-bookworm-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54 AS frontend-build
WORKDIR /build/client
COPY client/package.json client/package-lock.json ./
# Optional trust bundle for builders behind an HTTPS inspection proxy.
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca; fi; \
    npm ci
COPY client/ ./
RUN npm run build && npm run lint

# Share a patched Debian 13 base between build and runtime stages.
FROM python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS python-base
RUN apt-get update \
    && apt-get upgrade --no-install-recommends -y \
    && rm -rf /var/lib/apt/lists/*

FROM python-base AS backend-build
COPY --from=ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
WORKDIR /app/server
COPY server/pyproject.toml server/uv.lock ./
# The application runs from source through main:app; no package install is needed.
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    uv sync --locked --no-dev --no-install-project --python /usr/local/bin/python

FROM backend-build AS test
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    uv sync --locked --no-install-project --python /usr/local/bin/python
ENV PATH="/app/server/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1
COPY server/main.py ./
COPY server/app/ ./app/
COPY server/integrations/ ./integrations/
COPY server/tests/ ./tests/
COPY scripts/ /app/scripts/
CMD ["sh", "-c", "python -m pytest tests -q && ruff check main.py app integrations tests && ruff format --check main.py app integrations tests"]

FROM python-base AS runtime
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:1.1.0 /lambda-adapter /opt/extensions/lambda-adapter
ENV APP_ENV=production \
    PYTHONUNBUFFERED=1 \
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
COPY --chown=10001:10001 server/main.py ./
COPY --chown=10001:10001 server/app/ ./app/
COPY --chown=10001:10001 server/integrations/ ./integrations/
COPY --from=frontend-build /build/client/dist /app/client/dist
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).close()"]
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log", "--no-proxy-headers"]
