"""Authenticated Gmail HTTP requests and provider error translation."""

import json
from datetime import UTC, datetime

import requests
from fastapi import HTTPException

from app.mail.credentials import READ_SCOPES, SEND_SCOPES, GmailCredentials, reconnect

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class GmailClient:
    def __init__(self, credentials: GmailCredentials):
        self.credentials = credentials

    def request(
        self, user_id: str, method: str, path: str, *, _retry: bool = False, **kwargs
    ) -> dict:
        encrypted, data = self.credentials.load(user_id)
        needed = READ_SCOPES if method == "GET" else SEND_SCOPES
        if not needed.intersection(data["scopes"]):
            raise reconnect()
        expiry = (
            datetime.fromisoformat(data["expiry"]).replace(tzinfo=UTC).timestamp()
            if data.get("expiry")
            else 0
        )
        if not data.get("token") or expiry <= datetime.now(UTC).timestamp() + 60:
            data = self.credentials.refresh(user_id, encrypted, data)
        if not needed.intersection(data["scopes"]):
            raise reconnect()
        try:
            # No automatic transport retries: a timed-out send might have succeeded.
            with requests.Session() as session:
                response = session.request(
                    method,
                    GMAIL + path,
                    headers={"Authorization": "Bearer " + data["token"]},
                    timeout=20,
                    stream=True,
                    allow_redirects=False,
                    **kwargs,
                )
                if response.status_code == 200:
                    payload = bytearray()
                    try:
                        for chunk in response.iter_content(chunk_size=65536):
                            payload.extend(chunk)
                            if len(payload) > MAX_RESPONSE_BYTES:
                                raise HTTPException(
                                    413,
                                    "This message is too large for Mini Mailroom. Open it in Gmail.",
                                )
                    finally:
                        response.close()
                else:
                    response.close()
        except requests.RequestException:
            detail = (
                "Send result unknown. Check Sent in Gmail before sending again."
                if method == "POST"
                else "Gmail temporarily unavailable."
            )
            raise HTTPException(503, detail) from None
        if response.status_code == 401:
            if _retry:
                raise reconnect()
            # Google rejected this request before processing it. Refresh and retry once.
            current, latest = self.credentials.load(user_id)
            if latest.get("token") == data.get("token"):
                self.credentials.refresh(user_id, current, latest)
            return self.request(user_id, method, path, _retry=True, **kwargs)
        if response.status_code == 403:
            raise HTTPException(
                403,
                "Gmail denied access. Check granted permissions and that Gmail API is enabled.",
            )
        if response.status_code == 404:
            raise HTTPException(404, "Message not found.")
        if response.status_code == 429:
            raise HTTPException(429, "Gmail rate limit reached. Try again later.")
        if response.status_code != 200:
            detail = (
                "Send result unknown. Check Sent in Gmail before sending again."
                if method == "POST" and response.status_code >= 500
                else "Gmail could not complete this request."
            )
            raise HTTPException(502, detail)
        try:
            result = json.loads(payload)
            if not isinstance(result, dict):
                raise TypeError
            return result
        except (ValueError, TypeError):
            raise HTTPException(502, "Gmail returned an invalid response.") from None
