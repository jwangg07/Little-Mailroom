"""Explicit application assembly and per-app dependency wiring."""

import os
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.auth.google_oauth import GoogleOAuth
from app.auth.router import create_auth_router
from app.auth.store import AuthStore, AuthStoreError, DynamoDBAuthStore, MemoryAuthStore
from app.config import FRONTEND_DIST, Settings
from app.http import configure_http
from app.mail.credentials import GmailCredentials
from app.mail.gmail_client import GmailClient
from app.mail.router import create_mail_router
from app.mail.service import GmailService
from integrations.google.client_secret import GoogleClientSecret
from integrations.google.credential_vault import CredentialVault


def create_app(
    settings: Settings | None = None, *, store: AuthStore | None = None
) -> FastAPI:
    settings = settings or Settings()
    on_lambda = bool(
        os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
        or os.environ.get("AWS_LAMBDA_RUNTIME_API")
    )
    production = on_lambda or settings.app_env == "production"
    if production:
        settings = settings.model_copy(update={"app_env": "production"})
        if (
            settings.auth_store != "dynamodb"
            or not settings.dynamodb_table.strip()
            or (store is not None and not isinstance(store, DynamoDBAuthStore))
        ):
            raise RuntimeError("Production requires DynamoDB authentication")
        if (
            not settings.cookie_secure
            or not settings.credential_kms_key_id.strip()
            or not settings.google_client_secret_arn.strip()
        ):
            raise RuntimeError(
                "Production requires COOKIE_SECURE=true, GOOGLE_CLIENT_SECRET_ARN, and CREDENTIAL_KMS_KEY_ID"
            )
        frontend, callback = (
            urlsplit(settings.frontend_url),
            urlsplit(settings.google_redirect_uri),
        )
        if any(
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            for url in (frontend, callback)
        ):
            raise RuntimeError(
                "Production requires HTTPS frontend and callback URLs without credentials, query, or fragment"
            )
        if (
            frontend.netloc != callback.netloc
            or frontend.path not in ("", "/")
            or callback.path != "/auth/google/callback"
        ):
            raise RuntimeError(
                "Production requires same-origin frontend and /auth/google/callback"
            )
    if store is None:
        store = (
            MemoryAuthStore()
            if settings.auth_store == "memory"
            else DynamoDBAuthStore(settings.dynamodb_table)
        )
    app = FastAPI(
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
    )
    secret_source = (
        GoogleClientSecret(settings.google_client_secret_arn)
        if settings.google_client_secret_arn
        else None
    )
    # Only explicit memory storage may use an ephemeral encryption key.
    if isinstance(store, DynamoDBAuthStore) and not (
        settings.credential_kms_key_id
        or settings.credential_encryption_key.get_secret_value()
    ):
        # Fail lazily so anonymous health checks still work without AWS setup.
        vault = None
    else:
        vault = CredentialVault(
            settings.credential_kms_key_id,
            settings.credential_encryption_key.get_secret_value(),
        )

    def get_secret() -> str:
        return (
            secret_source.get()
            if secret_source
            else settings.google_client_secret.get_secret_value()
        )

    def mail_credentials() -> GmailCredentials:
        if vault is None:
            raise AuthStoreError("Credential encryption is not configured")
        return GmailCredentials(store, vault, settings.google_client_id, get_secret)

    def mail_service() -> GmailService:
        return GmailService(GmailClient(mail_credentials()))

    configure_http(app, production=production)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    app.include_router(
        create_auth_router(
            settings, store, GoogleOAuth(settings, get_secret), mail_credentials
        )
    )
    app.include_router(create_mail_router(settings, store, mail_service))

    # Register last so API routes take priority over the frontend at "/".
    # An unbuilt frontend should not prevent running the backend in dev mode.
    if (FRONTEND_DIST / "index.html").is_file():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

    return app
