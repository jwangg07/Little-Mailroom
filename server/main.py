import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow
from oauthlib.oauth2 import OAuth2Error
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from requests.exceptions import RequestException

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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).with_name(".env"), extra="ignore"
    )
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"
    frontend_url: str = "http://localhost:5173"
    cookie_secure: bool = False


@dataclass
class PendingLogin:
    verifier: str
    nonce: str
    expires_at: float


@dataclass
class Session:
    user: dict[str, str]
    expires_at: float


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI()
    # Ephemeral until the persistence stage. Google tokens never reach cookies.
    pending: dict[str, PendingLogin] = {}
    sessions: dict[str, Session] = {}

    def prune() -> None:
        for store in (pending, sessions):
            for key, value in list(store.items()):
                if value.expires_at <= time.time():
                    store.pop(key, None)

    def make_flow(verifier: str) -> Flow:
        return Flow.from_client_config(
            {
                "web": {
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret.get_secret_value(),
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
        response.delete_cookie(STATE_COOKIE, path="/")
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
        prune()
        if (
            not settings.google_client_id
            or not settings.google_client_secret.get_secret_value()
        ):
            return finish("not_configured")
        state, verifier, nonce = (secrets.token_urlsafe(32) for _ in range(3))
        flow = make_flow(verifier)
        url, _ = flow.authorization_url(
            state=state, nonce=nonce, access_type="online", prompt="select_account"
        )
        pending[state] = PendingLogin(verifier, nonce, time.time() + LOGIN_TTL)
        response = RedirectResponse(url, 303)
        set_cookie(response, STATE_COOKIE, state, LOGIN_TTL)
        return response

    @app.get(urlsplit(settings.google_redirect_uri).path)
    def callback(request: Request, state: str = "", code: str = "", error: str = ""):
        prune()
        cookie_state = request.cookies.get(STATE_COOKIE, "")
        if (
            not state
            or not cookie_state
            or not secrets.compare_digest(state, cookie_state)
        ):
            return finish("invalid_state")
        attempt = pending.pop(state, None)
        if attempt is None:
            return finish("invalid_state")
        if error:
            return finish("access_denied" if error == "access_denied" else "failed")
        if not code:
            return finish("failed")
        try:
            flow = make_flow(attempt.verifier)
            flow.fetch_token(code=code, timeout=15)
            claims = id_token.verify_oauth2_token(
                flow.credentials.id_token, GoogleRequest(), settings.google_client_id
            )
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
        ):
            # Provider errors can contain codes/tokens; never send or log them.
            return finish("failed")
        sessions.pop(request.cookies.get(SESSION_COOKIE, ""), None)
        session_id = secrets.token_urlsafe(32)
        sessions[session_id] = Session(user, time.time() + SESSION_TTL)
        response = finish()
        set_cookie(response, SESSION_COOKIE, session_id, SESSION_TTL)
        return response

    @app.get("/auth/me")
    def me(request: Request):
        prune()
        session = sessions.get(request.cookies.get(SESSION_COOKIE, ""))
        return {"user": session.user if session else None}

    @app.post("/auth/logout")
    def logout(request: Request):
        # Cross-site forms cannot supply this custom header. No CORS is enabled.
        origin = request.headers.get("origin")
        if request.headers.get("x-mailroom-request") != "1" or (
            origin and origin != settings.frontend_url.rstrip("/")
        ):
            raise HTTPException(403, "Invalid request origin")
        sessions.pop(request.cookies.get(SESSION_COOKIE, ""), None)
        pending.pop(request.cookies.get(STATE_COOKIE, ""), None)
        response = JSONResponse({"user": None})
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(STATE_COOKIE, path="/")
        return response

    return app


app = create_app()
