"""Shared private response headers and safe infrastructure error handlers."""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.auth.store import AuthStoreError
from integrations.google.client_secret import SecretUnavailableError

logger = logging.getLogger(__name__)


def configure_http(app: FastAPI) -> None:
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
