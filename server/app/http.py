"""Shared private response headers and safe infrastructure error handlers."""

import asyncio
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.auth.store import AuthStoreError
from integrations.google.client_secret import SecretUnavailableError

logger = logging.getLogger(__name__)


class RequestSizeLimit:
    """Bound JSON bodies before FastAPI parses them, including chunked uploads."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        # Starlette converts receive errors during JSON parsing to HTTP 400.
        # Check the body here first so oversize requests reliably return 413.
        body = bytearray()
        try:
            async with asyncio.timeout(15):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    if len(body) + len(message.get("body", b"")) > 512 * 1024:
                        await JSONResponse(
                            {"detail": "Request too large."}, status_code=413
                        )(scope, receive, send)
                        return
                    body.extend(message.get("body", b""))
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await JSONResponse({"detail": "Request body timed out."}, status_code=408)(
                scope, receive, send
            )
            return
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def configure_http(app: FastAPI, *, production: bool = False) -> None:
    app.add_middleware(RequestSizeLimit)

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
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        # Production frontend has external same-origin JS/CSS and no inline code.
        # Development docs/Vite need their own script policy; framing stays denied.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'"
            if production
            else "frame-ancestors 'none'; object-src 'none'; base-uri 'none'"
        )
        if production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.url.path.startswith(("/auth/", "/api/mail/")):
            response.headers["Cache-Control"] = "no-store"
        return response
