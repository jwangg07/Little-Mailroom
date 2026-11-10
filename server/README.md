# Mini Mailroom backend

See the [project README](../README.md) for Google OAuth configuration, local
startup commands, and the scope and limitations of this sign-in increment.

## Backend layout

- `main.py`: creates the application, wires services and routers, registers error
  handlers and privacy headers, and serves the built frontend.
- `config.py`: loads backend settings from environment variables and `server/.env`.
- `routes/auth.py`: Google OAuth, session lookup, and sign-out endpoints.
- `routes/mail.py`: inbox, message reading, and sending endpoints, plus send validation.
- `security.py`: shared session-cookie names, authentication, and origin checks.
- `auth_store.py`, `credential_vault.py`, `google_secret.py`, and `gmail_service.py`:
  storage, encryption, secret retrieval, and Gmail operations.
- `tests/`: tests with fake Google and AWS boundaries.

Run commands still use `main:app`. Router factories receive each application's
settings, store, and services so separate app instances do not share session state.
