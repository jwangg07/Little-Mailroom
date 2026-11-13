"""Google OAuth, browser sessions, and sign-out endpoints."""

import secrets
import time
from collections.abc import Callable
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.auth.google_oauth import GoogleOAuth, GoogleSignInError
from app.auth.models import PendingLogin, Session
from app.auth.rate_limit import limit_requests, request_source
from app.auth.security import require_origin, session_cookie, state_cookie
from app.auth.store import AuthStore
from app.config import Settings
from app.mail.credentials import GmailCredentials

LOGIN_TTL = 600
SESSION_TTL = 60 * 60 * 8


def create_auth_router(
    settings: Settings,
    store: AuthStore,
    oauth: GoogleOAuth,
    mail_credentials: Callable[[], GmailCredentials],
) -> APIRouter:
    router = APIRouter()
    session_name, state_name = session_cookie(settings), state_cookie(settings)

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
            state_name, path="/", secure=settings.cookie_secure, httponly=True
        )
        return response

    @router.get("/auth/google/login")
    def login(request: Request, reconnect: bool = False):
        if not settings.google_client_id or not (
            settings.google_client_secret_arn
            or settings.google_client_secret.get_secret_value()
        ):
            return finish("not_configured")
        limit_requests(
            store, "login", request_source(request), settings.login_requests_per_minute
        )
        state, verifier, nonce = (secrets.token_urlsafe(32) for _ in range(3))
        url = oauth.authorization_url(state, verifier, nonce, reconnect)
        store.save_oauth(
            state, PendingLogin(verifier, nonce, int(time.time()) + LOGIN_TTL)
        )
        response = RedirectResponse(url, 303)
        set_cookie(response, state_name, state, LOGIN_TTL)
        return response

    @router.get(urlsplit(settings.google_redirect_uri).path)
    def callback(request: Request, state: str = "", code: str = "", error: str = ""):
        cookie_state = request.cookies.get(state_name, "")
        if (
            not state
            or not cookie_state
            or not secrets.compare_digest(state.encode(), cookie_state.encode())
        ):
            return finish("invalid_state")
        limit_requests(store, "callback", request_source(request), 20)
        attempt = store.consume_oauth(state, int(time.time()))
        if attempt is None:
            return finish("invalid_state")
        if error:
            return finish("access_denied" if error == "access_denied" else "failed")
        if not code:
            return finish("failed")
        try:
            user, credentials = oauth.exchange(code, attempt)
            mail_credentials().capture(user["id"], credentials)
        except GoogleSignInError:
            return finish("failed")
        session_id = secrets.token_urlsafe(32)
        store.save_session(session_id, Session(user, int(time.time()) + SESSION_TTL))
        old_session_id = request.cookies.get(session_name)
        if old_session_id:
            store.delete_session(old_session_id)
        response = finish()
        set_cookie(response, session_name, session_id, SESSION_TTL)
        return response

    @router.get("/auth/me")
    def me(request: Request):
        session_id = request.cookies.get(session_name)
        session = (
            store.read_session(session_id, int(time.time())) if session_id else None
        )
        return {"user": session.user if session else None}

    @router.post("/auth/logout")
    def logout(request: Request):
        # Cross-site forms cannot supply this custom header. No CORS is enabled.
        require_origin(request, settings)
        session_id = request.cookies.get(session_name)
        session = (
            store.read_session(session_id, int(time.time())) if session_id else None
        )
        revoked = False
        if session:
            limit_requests(store, "logout", session.user["id"], 10)
            # Revoke before discarding the credentials needed to retry a failure.
            revoked = mail_credentials().disconnect(session.user["id"])
        if session_id:
            store.delete_session(session_id)
        response = JSONResponse({"user": None, "revoked": revoked})
        state = request.cookies.get(state_name)
        if state:
            store.delete_oauth(state)
        for name in (session_name, state_name):
            response.delete_cookie(
                name, path="/", secure=settings.cookie_secure, httponly=True
            )
        return response

    return router
