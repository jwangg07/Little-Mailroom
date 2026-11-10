import logging
import os
import secrets
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow
from oauthlib.oauth2 import OAuth2Error
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from requests.exceptions import RequestException

from auth_store import (
    AuthStore,
    AuthStoreError,
    DynamoDBAuthStore,
    MemoryAuthStore,
    PendingLogin,
    Session,
)
from google_secret import GoogleClientSecret, SecretUnavailableError

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.readonly",
]
SESSION_COOKIE = "mailroom_session"
STATE_COOKIE = "mailroom_oauth_state"
LOGIN_TTL = 600
SESSION_TTL = 60 * 60 * 8
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).with_name(".env"), extra="ignore"
    )
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"
    frontend_url: str = "http://localhost:5173"
    cookie_secure: bool = False
    dynamodb_table: str = ""
    google_client_secret_arn: str = ""
    auth_store: Literal["dynamodb", "memory"] = "dynamodb"


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

    def make_flow(verifier: str) -> Flow:
        return Flow.from_client_config(
            {
                "web": {
                    "client_id": settings.google_client_id,
                    "client_secret": (
                        secret_source.get()
                        if secret_source is not None
                        else settings.google_client_secret.get_secret_value()
                    ),
                    "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=SCOPES,
            redirect_uri=settings.google_redirect_uri,
            code_verifier=verifier,
        )

    def set_cookie(response, name: str, value: str, max_age: int) -> None:
        response.set_cookie(
            name,
            value,
            max_age=max_age,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
            path="/",
        )

    def finish(error: str | None = None) -> RedirectResponse:
        suffix = "?" + urlencode({"auth_error": error}) if error else ""
        response = RedirectResponse(
            settings.frontend_url.rstrip("/") + "/" + suffix, 303
        )
        response.delete_cookie(
            STATE_COOKIE, path="/", secure=settings.cookie_secure, httponly=True
        )
        return response

    @app.middleware("http")
    async def private_auth_responses(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/auth/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/auth/google/login")
    def login():
        if not settings.google_client_id or not (
            secret_source is not None
            or settings.google_client_secret.get_secret_value()
        ):
            return finish("not_configured")
        state, verifier, nonce = (secrets.token_urlsafe(32) for _ in range(3))
        flow = make_flow(verifier)
        url, _ = flow.authorization_url(
            state=state, nonce=nonce, access_type="online", prompt="select_account"
        )
        store.save_oauth(
            state, PendingLogin(verifier, nonce, int(time.time()) + LOGIN_TTL)
        )
        response = RedirectResponse(url, 303)
        set_cookie(response, STATE_COOKIE, state, LOGIN_TTL)
        return response

    @app.get(urlsplit(settings.google_redirect_uri).path)
    def callback(request: Request, state: str = "", code: str = "", error: str = ""):
        cookie_state = request.cookies.get(STATE_COOKIE, "")
        if (
            not state
            or not cookie_state
            or not secrets.compare_digest(state.encode(), cookie_state.encode())
        ):
            return finish("invalid_state")
        attempt = store.consume_oauth(state, int(time.time()))
        if attempt is None:
            return finish("invalid_state")
        if error:
            return finish("access_denied" if error == "access_denied" else "failed")
        if not code:
            return finish("failed")
        stage = "flow_setup"
        try:
            flow = make_flow(attempt.verifier)
            stage = "token_exchange"
            flow.fetch_token(code=code, timeout=15)
            stage = "id_token_verification"
            claims = id_token.verify_oauth2_token(
                flow.credentials.id_token, GoogleRequest(), settings.google_client_id
            )
            stage = "identity_claims"
            if claims.get("nonce") != attempt.nonce or not claims.get("email_verified"):
                raise ValueError("Unverified identity")
            user = {
                "id": claims["sub"],
                "email": claims["email"],
                "name": claims.get("name", claims["email"]),
            }
        except (
            GoogleAuthError,
            OAuth2Error,
            RequestException,
            ValueError,
            KeyError,
            Warning,
        ) as exc:
            # Exception messages, tracebacks and callback URLs can contain tokens.
            logger.warning(
                "Google sign-in failed stage=%s exception_type=%s",
                stage,
                type(exc).__name__,
            )
            return finish("failed")
        session_id = secrets.token_urlsafe(32)
        store.save_session(session_id, Session(user, int(time.time()) + SESSION_TTL))
        old_session_id = request.cookies.get(SESSION_COOKIE)
        if old_session_id:
            store.delete_session(old_session_id)
        response = finish()
        set_cookie(response, SESSION_COOKIE, session_id, SESSION_TTL)
        return response

    @app.get("/auth/me")
    def me(request: Request):
        session_id = request.cookies.get(SESSION_COOKIE)
        session = (
            store.read_session(session_id, int(time.time())) if session_id else None
        )
        return {"user": session.user if session else None}

    @app.post("/auth/logout")
    def logout(request: Request):
        # Cross-site forms cannot supply this custom header. No CORS is enabled.
        origin = request.headers.get("origin")
        if request.headers.get("x-mailroom-request") != "1" or (
            origin and origin != settings.frontend_url.rstrip("/")
        ):
            raise HTTPException(403, "Invalid request origin")
        session_id = request.cookies.get(SESSION_COOKIE)
        state = request.cookies.get(STATE_COOKIE)
        if session_id:
            store.delete_session(session_id)
        if state:
            store.delete_oauth(state)
        response = JSONResponse({"user": None})
        response.delete_cookie(
            SESSION_COOKIE, path="/", secure=settings.cookie_secure, httponly=True
        )
        response.delete_cookie(
            STATE_COOKIE, path="/", secure=settings.cookie_secure, httponly=True
        )
        return response

    # Register last so API routes take priority over the frontend at "/".
    # An unbuilt frontend should not prevent running the backend in dev mode.
    frontend_dist = Path(__file__).resolve().parent.parent / "client" / "dist"
    if (frontend_dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    return app


app = create_app()
