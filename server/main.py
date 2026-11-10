"""Application assembly and Uvicorn entry point (main:app)."""

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from auth_store import AuthStore, AuthStoreError, DynamoDBAuthStore, MemoryAuthStore
from config import Settings
from credential_vault import CredentialVault
from gmail_service import GmailService
from google_secret import GoogleClientSecret, SecretUnavailableError
from routes.auth import create_auth_router
from routes.mail import create_mail_router

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None, *, store: AuthStore | None = None
) -> FastAPI:
    settings = settings or Settings()
    on_lambda = bool(
        os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
        or os.environ.get("AWS_LAMBDA_RUNTIME_API")
    )
    if on_lambda and (
        settings.auth_store != "dynamodb"
        or not settings.dynamodb_table.strip()
        or (store is not None and not isinstance(store, DynamoDBAuthStore))
    ):
        raise RuntimeError("Lambda requires DYNAMODB_TABLE and DynamoDB authentication")
    if on_lambda and (
        not settings.google_client_secret_arn.strip() or not settings.cookie_secure
    ):
        raise RuntimeError(
            "Lambda requires GOOGLE_CLIENT_SECRET_ARN and COOKIE_SECURE=true"
        )
    if on_lambda and not settings.credential_kms_key_id.strip():
        raise RuntimeError("Lambda requires CREDENTIAL_KMS_KEY_ID")
    if store is None:
        store = (
            MemoryAuthStore()
            if settings.auth_store == "memory"
            else DynamoDBAuthStore(settings.dynamodb_table)
        )
    app = FastAPI()
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

    def get_secret():
        return (
            secret_source.get()
            if secret_source
            else settings.google_client_secret.get_secret_value()
        )

    def mail_service() -> GmailService:
        if vault is None:
            raise AuthStoreError("Credential encryption is not configured")
        return GmailService(store, vault, settings.google_client_id, get_secret)

    app.state.mail_service = mail_service

    @app.exception_handler(AuthStoreError)
    async def storage_unavailable(request: Request, exc: AuthStoreError):
        # Never log provider exceptions, identifiers, or callback query strings.
        logger.warning("Authentication storage unavailable")
        return JSONResponse(
            {"detail": "Authentication temporarily unavailable"},
            status_code=503,
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    @app.exception_handler(SecretUnavailableError)
    async def secret_unavailable(request: Request, exc: SecretUnavailableError):
        logger.warning("Google credential lookup unavailable")
        return JSONResponse(
            {"detail": "Sign-in temporarily unavailable"},
            status_code=503,
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    @app.middleware("http")
    async def private_auth_responses(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/auth/", "/api/mail/")):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/health")
    def health():
        return {"status": "ok"}

    app.include_router(create_auth_router(settings, store, get_secret, mail_service))
    app.include_router(create_mail_router(settings, store, mail_service))

    # Register last so API routes take priority over the frontend at "/".
    # An unbuilt frontend should not prevent running the backend in dev mode.
    frontend_dist = Path(__file__).resolve().parent.parent / "client" / "dist"
    if (frontend_dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    return app


app = create_app()
