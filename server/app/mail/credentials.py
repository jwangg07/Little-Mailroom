"""Capture, load, and refresh encrypted Google credentials."""

from collections.abc import Callable
from functools import partial

import requests
from fastapi import HTTPException
from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials

from app.auth.store import AuthStore
from integrations.google.credential_vault import CredentialVault

PREFIX = "https://www.googleapis.com/auth/gmail."
READ_SCOPES = {PREFIX + "readonly", PREFIX + "modify", "https://mail.google.com/"}
SEND_SCOPES = {
    PREFIX + "send",
    PREFIX + "compose",
    PREFIX + "modify",
    "https://mail.google.com/",
}


def reconnect() -> HTTPException:
    return HTTPException(409, "Reconnect Gmail to grant mail access.")


class GmailCredentials:
    def __init__(
        self,
        store: AuthStore,
        vault: CredentialVault,
        client_id: str,
        get_secret: Callable[[], str],
    ):
        self.store = store
        self.vault = vault
        self.client_id = client_id
        self.get_secret = get_secret

    def capture(self, user_id: str, credentials: Credentials) -> None:
        old = self.store.read_credentials(user_id)
        previous = self.vault.decrypt(user_id, old) if old else {}
        data = {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token or previous.get("refresh_token"),
            "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
            "scopes": list(
                credentials.granted_scopes
                if credentials.granted_scopes is not None
                else credentials.scopes or []
            ),
        }
        self.store.save_credentials(user_id, self.vault.encrypt(user_id, data))

    def load(self, user_id: str) -> tuple[str, dict]:
        encrypted = self.store.read_credentials(user_id)
        if not encrypted:
            raise reconnect()
        data = self.vault.decrypt(user_id, encrypted)
        return encrypted, data

    def refresh(self, user_id: str, encrypted: str, data: dict) -> dict:
        if not data.get("refresh_token"):
            raise reconnect()
        credentials = Credentials(
            token=data.get("token"),
            refresh_token=data["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self.client_id,
            client_secret=self.get_secret(),
            scopes=data["scopes"],
        )
        try:
            with requests.Session() as session:
                credentials.refresh(partial(GoogleRequest(session=session), timeout=15))
        except RefreshError as exc:
            if exc.retryable:
                raise HTTPException(
                    503, "Gmail refresh temporarily unavailable."
                ) from None
            raise reconnect() from None
        except (TransportError, requests.RequestException):
            raise HTTPException(503, "Gmail refresh temporarily unavailable.") from None
        updated = {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token or data["refresh_token"],
            "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
            "scopes": list(
                credentials.granted_scopes
                if credentials.granted_scopes is not None
                else data["scopes"]
            ),
        }
        if self.store.replace_credentials(
            user_id, encrypted, self.vault.encrypt(user_id, updated)
        ):
            return updated
        # A concurrent login/refresh won; use its credential, never overwrite it.
        return self.load(user_id)[1]
