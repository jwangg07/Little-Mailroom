# Mini Mailroom backend

See the [project README](../README.md) for local startup and AWS configuration,
and the [architecture guide](../docs/ARCHITECTURE.md) for complete feature flows.

- `main.py`: small Uvicorn entry point (`main:app`).
- `app/application.py`: `create_app()` and explicit per-app dependency wiring.
- `app/config.py`: settings; `.env` remains at `server/.env`.
- `app/http.py`: safe error handlers and private response headers.
- `app/auth/`: OAuth router, Google exchange/verification, session checks,
  identity/session models, and the store contract with memory/DynamoDB stores.
- `app/mail/`: router, request validation, user operations, authenticated Gmail
  transport, credential lifecycle, and MIME/display formatting.
- `integrations/google/`: lazy client-secret retrieval and credential encryption.
- `tests/auth/`, `tests/mail/`, `tests/integrations/`: fake Google/AWS boundaries.

Run commands from `server/`. The application runs from source rather than an
installed CLI package. `uv sync` installs dependencies without the old scaffold.
App factories receive isolated settings and storage, with no global service registry.
