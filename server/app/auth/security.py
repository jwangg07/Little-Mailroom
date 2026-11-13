"""Shared session and request-origin checks for authenticated routes."""

import time

from fastapi import HTTPException, Request

from app.auth.models import User
from app.auth.store import AuthStore
from app.config import Settings

SESSION_COOKIE = "mailroom_session"
STATE_COOKIE = "mailroom_oauth_state"


def session_cookie(settings: Settings) -> str:
    return (
        "__Host-" + SESSION_COOKIE
        if settings.app_env == "production"
        else SESSION_COOKIE
    )


def state_cookie(settings: Settings) -> str:
    return (
        "__Host-" + STATE_COOKIE if settings.app_env == "production" else STATE_COOKIE
    )


def require_user(request: Request, store: AuthStore, settings: Settings) -> User:
    session_id = request.cookies.get(session_cookie(settings))
    session = store.read_session(session_id, int(time.time())) if session_id else None
    if session is None:
        raise HTTPException(401, "Sign in to access your mail.")
    return session.user


def require_origin(request: Request, settings: Settings) -> None:
    origin = request.headers.get("origin")
    if request.headers.get("x-mailroom-request") != "1" or (
        origin and origin != settings.frontend_url.rstrip("/")
    ):
        raise HTTPException(403, "Invalid request origin")
