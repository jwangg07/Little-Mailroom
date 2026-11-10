"""Shared session and request-origin checks for authenticated routes."""

import time

from fastapi import HTTPException, Request

from auth_store import AuthStore
from config import Settings

SESSION_COOKIE = "mailroom_session"
STATE_COOKIE = "mailroom_oauth_state"


def require_user(request: Request, store: AuthStore) -> dict:
    session_id = request.cookies.get(SESSION_COOKIE)
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
