"""Gmail operations with encrypted, refreshable credentials and safe errors."""

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from functools import partial
from urllib.parse import quote

import requests
from fastapi import HTTPException
from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials

from auth_store import AuthStore
from credential_vault import CredentialVault

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
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


class GmailService:
    def __init__(
        self, store: AuthStore, vault: CredentialVault, client_id: str, get_secret
    ):
        self.store, self.vault = store, vault
        self.client_id, self.get_secret = client_id, get_secret

    def capture(self, user_id: str, credentials) -> None:
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

    def _load(self, user_id: str):
        encrypted = self.store.read_credentials(user_id)
        if not encrypted:
            raise reconnect()
        data = self.vault.decrypt(user_id, encrypted)
        return encrypted, data

    def _refresh(self, user_id: str, encrypted: str, data: dict) -> dict:
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
        return self._load(user_id)[1]

    def request(
        self, user_id: str, method: str, path: str, *, _retry: bool = False, **kwargs
    ) -> dict:
        encrypted, data = self._load(user_id)
        needed = READ_SCOPES if method == "GET" else SEND_SCOPES
        if not needed.intersection(data["scopes"]):
            raise reconnect()
        expiry = (
            datetime.fromisoformat(data["expiry"])
            .replace(tzinfo=timezone.utc)
            .timestamp()
            if data.get("expiry")
            else 0
        )
        if (
            not data.get("token")
            or expiry <= datetime.now(timezone.utc).timestamp() + 60
        ):
            data = self._refresh(user_id, encrypted, data)
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
                    **kwargs,
                )
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
            current, latest = self._load(user_id)
            if latest.get("token") == data.get("token"):
                self._refresh(user_id, current, latest)
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
        if not response.ok:
            detail = (
                "Send result unknown. Check Sent in Gmail before sending again."
                if method == "POST" and response.status_code >= 500
                else "Gmail could not complete this request."
            )
            raise HTTPException(502, detail)
        try:
            return response.json()
        except ValueError:
            raise HTTPException(502, "Gmail returned an invalid response.") from None

    def inbox(self, user_id: str, page_token: str = "") -> dict:
        page = self.request(
            user_id,
            "GET",
            "/messages",
            params={
                "labelIds": "INBOX",
                "maxResults": 20,
                "pageToken": page_token,
            },
        )

        def summary(item):
            message = self.request(
                user_id,
                "GET",
                "/messages/" + quote(item["id"], safe=""),
                params={
                    "format": "metadata",
                    "metadataHeaders": ["From", "Subject", "Date"],
                },
            )
            headers = {
                h["name"].lower(): h["value"]
                for h in message.get("payload", {}).get("headers", [])
            }
            return {
                "id": message["id"],
                "threadId": message.get("threadId"),
                "from": headers.get("from", ""),
                "subject": headers.get("subject", "(No subject)"),
                "date": headers.get("date", ""),
                "snippet": message.get("snippet", ""),
                "unread": "UNREAD" in message.get("labelIds", []),
            }

        with ThreadPoolExecutor(max_workers=5) as pool:
            messages = list(pool.map(summary, page.get("messages", [])))
        return {"messages": messages, "nextPageToken": page.get("nextPageToken")}

    def message(self, user_id: str, message_id: str) -> dict:
        data = self.request(
            user_id,
            "GET",
            "/messages/" + quote(message_id, safe=""),
            params={"format": "raw"},
        )
        raw = data["raw"]
        message = BytesParser(policy=policy.default).parsebytes(
            base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        )
        body = message.get_body(preferencelist=("plain",))
        text = (
            body.get_content()
            if body
            else "This message has no plain-text version. View it in Gmail."
        )
        return {
            "id": data["id"],
            "from": str(message.get("From", "")),
            "subject": str(message.get("Subject", "(No subject)")),
            "body": text,
        }

    def send(self, user_id: str, sender: str, to: str, subject: str, body: str) -> dict:
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = sender, to, subject
        message.set_content(body)
        data = self.request(
            user_id,
            "POST",
            "/messages/send",
            json={"raw": base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")},
        )
        return {"id": data["id"], "threadId": data.get("threadId")}
