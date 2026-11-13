"""Shared fixed-window quotas. Raw IP addresses are not stored in rate keys."""

import hashlib
import time

from fastapi import HTTPException, Request

from app.auth.store import AuthStore


def limit_requests(
    store: AuthStore, action: str, identity: str, limit: int, window: int = 60
) -> None:
    now = int(time.time())
    key = hashlib.sha256(f"{action}:{identity}".encode()).hexdigest()
    if not store.take_rate_limit(key, limit, window, now):
        raise HTTPException(
            429,
            "Too many requests. Please wait a moment and try again.",
            headers={"Retry-After": str(window - now % window)},
        )


def request_source(request: Request) -> str:
    # Never trust arbitrary X-Forwarded-For values. Proxied peers share this
    # quota unless a trusted ingress has established the real ASGI client IP.
    return request.client.host if request.client else "unknown"
