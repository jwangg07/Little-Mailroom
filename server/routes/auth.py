"""Google OAuth, browser sessions, and sign-out endpoints."""

import logging
import secrets
import time
from collections.abc import Callable
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow
from oauthlib.oauth2 import OAuth2Error
from requests.exceptions import RequestException

from auth_store import AuthStore, PendingLogin, Session
from config import Settings
from gmail_service import GmailService
from security import SESSION_COOKIE, STATE_COOKIE, require_origin

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.modify",
]
LOGIN_TTL = 600
SESSION_TTL = 60 * 60 * 8
logger = logging.getLogger(__name__)


def create_auth_router(
    settings: Settings,
    store: AuthStore,
    get_secret: Callable[[], str],
    mail_service: Callable[[], GmailService],
) -> APIRouter:
    router = APIRouter()

    def make_flow(verifier: str) -> Flow:
        return Flow.from_client_config(
            {
                "web": {
                    "client_id": settings.google_client_id,
                    "client_secret": get_secret(),
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

    @router.get("/auth/google/login")
    def login(reconnect: bool = False):
        if not settings.google_client_id or not (
            settings.google_client_secret_arn
            or settings.google_client_secret.get_secret_value()
        ):
            return finish("not_configured")
        state, verifier, nonce = (secrets.token_urlsafe(32) for _ in range(3))
        flow = make_flow(verifier)
        url, _ = flow.authorization_url(
            state=state,
            nonce=nonce,
            access_type="offline",
            prompt="consent select_account" if reconnect else "select_account",
        )
        store.save_oauth(
            state, PendingLogin(verifier, nonce, int(time.time()) + LOGIN_TTL)
        )
        response = RedirectResponse(url, 303)
        set_cookie(response, STATE_COOKIE, state, LOGIN_TTL)
        return response

    @router.get(urlsplit(settings.google_redirect_uri).path)
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
            mail_service().capture(user["id"], flow.credentials)
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

    @router.get("/auth/me")
    def me(request: Request):
        session_id = request.cookies.get(SESSION_COOKIE)
        session = (
            store.read_session(session_id, int(time.time())) if session_id else None
        )
        return {"user": session.user if session else None}

    @router.post("/auth/logout")
    def logout(request: Request):
        # Cross-site forms cannot supply this custom header. No CORS is enabled.
        require_origin(request, settings)
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

    return router
