"""Security regressions with synthetic users and no live Google/AWS requests."""

import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
import requests
from botocore.exceptions import ClientError
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import application
from app.auth.google_oauth import SCOPES
from app.auth.models import PendingLogin, Session
from app.auth.security import SESSION_COOKIE
from app.auth.store import (
    CREDENTIAL_TTL,
    AuthStoreError,
    DynamoDBAuthStore,
    MemoryAuthStore,
    session_key,
)
from app.config import Settings
from app.mail.credentials import GmailCredentials
from app.mail.formatting import mail_message
from app.mail.gmail_client import MAX_RESPONSE_BYTES, GmailClient
from integrations.google.credential_vault import CredentialVault


def session(user="alice"):
    return Session(
        {"id": user, "email": f"{user}@example.com", "name": user},
        int(time.time()) + 600,
    )


def test_database_digest_cannot_be_replayed_and_legacy_key_is_not_read(monkeypatch):
    table = Mock()
    items = {
        "session#old-cookie": {
            "user": session().user,
            "expires_at": int(time.time()) + 600,
        }
    }
    table.get_item.side_effect = lambda **kw: (
        {"Item": items[kw["Key"]["pk"]]} if kw["Key"]["pk"] in items else {}
    )
    table.put_item.side_effect = lambda **kw: items.update(
        {kw["Item"]["pk"]: kw["Item"]}
    )
    store = DynamoDBAuthStore("auth")
    monkeypatch.setattr(store, "_table", lambda: table)
    store.save_session("original-cookie", session())
    stored = items[session_key("original-cookie")]
    assert "original-cookie" not in json.dumps(stored)
    assert store.read_session("original-cookie", int(time.time())).user["id"] == "alice"
    assert store.read_session(stored["pk"].split("#")[1], int(time.time())) is None
    assert store.read_session("old-cookie", int(time.time())) is None
    store.invalidate_user_sessions("alice")
    assert store.read_session("original-cookie", int(time.time())) is None


def test_memory_cleanup_and_capacity_fail_closed(monkeypatch):
    monkeypatch.setattr("app.auth.store.time.time", lambda: 100)
    store = MemoryAuthStore(max_entries=1)
    store.save_oauth("abandoned", PendingLogin("v", "n", 101))
    with pytest.raises(AuthStoreError):
        store.save_oauth("excess", PendingLogin("v", "n", 1000))
    monkeypatch.setattr("app.auth.store.time.time", lambda: 102)
    store.save_oauth("new", PendingLogin("v", "n", 200))
    assert store.consume_oauth("abandoned", 102) is None
    assert store.consume_oauth("new", 102) is not None


def test_memory_rate_limit_is_atomic_and_window_expires():
    store = MemoryAuthStore()
    with ThreadPoolExecutor(max_workers=12) as pool:
        allowed = list(
            pool.map(lambda _: store.take_rate_limit("source", 5, 60, 120), range(50))
        )
    assert sum(allowed) == 5
    assert store.take_rate_limit("different-source", 5, 60, 120)
    assert store.take_rate_limit("source", 5, 60, 180)


def test_dynamodb_quota_uses_atomic_condition_and_fails_closed(monkeypatch):
    store, table = DynamoDBAuthStore("auth"), Mock()
    monkeypatch.setattr(store, "_table", lambda: table)
    assert store.take_rate_limit("hash", 5, 60, 120)
    args = table.update_item.call_args.kwargs
    assert args["Key"] == {"pk": "rate#hash#2"}
    assert args["ConditionExpression"] == "attribute_not_exists(hits) OR hits < :limit"
    assert args["ExpressionAttributeValues"][":limit"] == 5
    table.update_item.side_effect = ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem"
    )
    assert not store.take_rate_limit("hash", 5, 60, 120)
    table.update_item.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "private"}}, "UpdateItem"
    )
    with pytest.raises(AuthStoreError, match="Rate limit storage unavailable"):
        store.take_rate_limit("hash", 5, 60, 120)


def test_login_quota_ignores_spoofed_forwarding_headers(monkeypatch):
    settings = Settings(
        _env_file=None,
        auth_store="memory",
        google_client_id="fixture",
        google_client_secret="fixture",
        login_requests_per_minute=2,
    )
    store = MemoryAuthStore()
    monkeypatch.setattr(
        "app.auth.google_oauth.GoogleOAuth.authorization_url",
        lambda *args: "https://accounts.google.com/",
    )
    with TestClient(
        application.create_app(settings, store=store), follow_redirects=False
    ) as client:
        assert client.get("/auth/google/login").status_code == 303
        assert client.get("/auth/google/login").status_code == 303
        response = client.get(
            "/auth/google/login", headers={"X-Forwarded-For": "203.0.113.99"}
        )
        assert response.status_code == 429
        assert 1 <= int(response.headers["Retry-After"]) <= 60
        assert response.headers["Cache-Control"] == "no-store"
        assert len(store._pending) == 2


def test_mail_quota_is_per_user_and_send_has_separate_quota(monkeypatch):
    from app.mail.service import GmailService

    settings = Settings(
        _env_file=None, mail_requests_per_minute=1, send_requests_per_minute=1
    )
    store = MemoryAuthStore()
    store.save_session("alice", session())
    store.save_session("bob", session("bob"))
    monkeypatch.setattr(GmailService, "inbox", lambda *args: {"messages": []})
    monkeypatch.setattr(GmailService, "send", lambda *args: {"id": "synthetic"})
    with TestClient(application.create_app(settings, store=store)) as client:
        client.cookies.set(SESSION_COOKIE, "alice")
        assert client.get("/api/mail/messages").status_code == 200
        assert client.get("/api/mail/messages").status_code == 429
        draft = {"to": "bob@example.com", "subject": "Hi", "body": "Hello"}
        assert client.post("/api/mail/send", json=draft).status_code == 403
        assert (
            client.post(
                "/api/mail/send", json=draft, headers={"X-Mailroom-Request": "1"}
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/mail/send", json=draft, headers={"X-Mailroom-Request": "1"}
            ).status_code
            == 429
        )
        client.cookies.set(SESSION_COOKIE, "bob")
        assert client.get("/api/mail/messages").status_code == 200


@pytest.fixture
def connection(monkeypatch):
    store, vault = MemoryAuthStore(), CredentialVault()
    credentials = GmailCredentials(store, vault, "fixture", lambda: "fixture")
    encrypted = vault.encrypt(
        "alice", {"refresh_token": "fixture-refresh", "token": "fixture-access"}
    )
    store.save_credentials("alice", encrypted)
    store.save_session("first", session())
    store.save_session("second", session())
    store.save_session("bob", session("bob"))
    http = Mock()
    http.__enter__ = Mock(return_value=http)
    http.__exit__ = Mock(return_value=False)
    http.post.return_value = Mock(status_code=200)
    monkeypatch.setattr("app.mail.credentials.requests.Session", lambda: http)
    monkeypatch.setattr(application, "CredentialVault", lambda *args: vault)
    return store, credentials, http


def test_logout_revokes_google_deletes_access_and_same_users_sessions(connection):
    store, _, http = connection
    with TestClient(
        application.create_app(Settings(_env_file=None), store=store)
    ) as client:
        client.cookies.set(SESSION_COOKIE, "first", domain="testserver.local", path="/")
        assert client.post("/auth/logout").status_code == 403
        assert (
            client.post(
                "/auth/logout",
                headers={"X-Mailroom-Request": "1", "Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        http.post.assert_not_called()
        response = client.post("/auth/logout", headers={"X-Mailroom-Request": "1"})
        assert response.json() == {"user": None, "revoked": True}
    assert store.read_credentials("alice") is None
    assert store.read_session("first", int(time.time())) is None
    assert store.read_session("second", int(time.time())) is None
    assert store.read_session("bob", int(time.time())) is not None
    args = http.post.call_args
    assert args.args == ("https://oauth2.googleapis.com/revoke",)
    assert args.kwargs["data"] == {"token": "fixture-refresh"}
    assert args.kwargs["allow_redirects"] is False


def test_logout_failure_can_be_retried_without_losing_session(connection):
    store, _, http = connection
    http.post.return_value.status_code = 503
    with TestClient(
        application.create_app(Settings(_env_file=None), store=store)
    ) as client:
        client.cookies.set(SESSION_COOKIE, "first", domain="testserver.local", path="/")
        headers = {"X-Mailroom-Request": "1"}
        response = client.post("/auth/logout", headers=headers)
        assert response.status_code == 503
        assert "Sign-out is incomplete" in response.json()["detail"]
        assert client.get("/auth/me").json()["user"]["id"] == "alice"
        assert store.read_credentials("alice") is not None
        http.post.return_value.status_code = 200
        assert client.post("/auth/logout", headers=headers).json()["revoked"] is True
        assert client.get("/auth/me").json() == {"user": None}
        assert SESSION_COOKIE not in client.cookies
        # Repeating sign-out is harmless and does not claim another revocation.
        assert client.post("/auth/logout", headers=headers).json() == {
            "user": None,
            "revoked": False,
        }
        assert http.post.call_count == 2


def test_logout_without_saved_access_invalidates_other_sessions(connection):
    store, _, http = connection
    store.delete_credentials("alice", store.read_credentials("alice"))
    with TestClient(
        application.create_app(Settings(_env_file=None), store=store)
    ) as client:
        client.cookies.set(SESSION_COOKIE, "first", domain="testserver.local", path="/")
        response = client.post("/auth/logout", headers={"X-Mailroom-Request": "1"})
        assert response.json() == {"user": None, "revoked": False}
        assert SESSION_COOKIE not in client.cookies
    assert store.read_session("second", int(time.time())) is None
    assert store.read_session("bob", int(time.time())) is not None
    http.post.assert_not_called()


def test_logout_with_expired_session_cannot_revoke_another_account(connection):
    store, _, http = connection
    with TestClient(
        application.create_app(Settings(_env_file=None), store=store)
    ) as client:
        client.cookies.set(
            SESSION_COOKIE, "expired-session", domain="testserver.local", path="/"
        )
        response = client.post(
            "/auth/logout",
            headers={"X-Mailroom-Request": "1"},
            json={"user_id": "alice"},
        )
        assert response.json() == {"user": None, "revoked": False}
        assert SESSION_COOKIE not in client.cookies
    assert store.read_credentials("alice") is not None
    http.post.assert_not_called()


@pytest.mark.parametrize("failure", ["timeout", "server", "bad_json"])
def test_failed_revocation_preserves_retryable_connection(connection, failure):
    store, credentials, http = connection
    if failure == "timeout":
        http.post.side_effect = requests.Timeout("private-provider-detail")
    elif failure == "server":
        http.post.return_value.status_code = 503
    else:
        http.post.return_value.status_code = 400
        http.post.return_value.json.side_effect = ValueError("private-provider-detail")
    with pytest.raises(HTTPException) as caught:
        credentials.disconnect("alice")
    assert caught.value.status_code == 503
    assert "private" not in caught.value.detail
    assert store.read_credentials("alice") is not None
    assert store.read_session("second", int(time.time())) is not None


def test_already_revoked_grant_is_disconnected(connection):
    store, credentials, http = connection
    http.post.return_value.status_code = 400
    http.post.return_value.json.return_value = {"error": "invalid_token"}
    assert credentials.disconnect("alice")
    assert store.read_credentials("alice") is None


def test_disconnect_does_not_delete_a_concurrently_changed_connection(connection):
    store, credentials, http = connection

    def concurrent_login(*args, **kwargs):
        store.save_credentials("alice", "new-encrypted-connection")
        return Mock(status_code=200)

    http.post.side_effect = concurrent_login
    with pytest.raises(HTTPException) as caught:
        credentials.disconnect("alice")
    assert caught.value.status_code == 409
    assert store.read_credentials("alice") == "new-encrypted-connection"


def test_credential_retention_and_expired_disconnect(connection, monkeypatch):
    store, credentials, http = connection
    now = int(time.time())
    monkeypatch.setattr("app.auth.store.time.time", lambda: now + CREDENTIAL_TTL + 1)
    assert store.read_credentials("alice") is None
    assert credentials.disconnect("alice") is False
    http.post.assert_not_called()


def production_settings(**changes):
    fields = {
        "_env_file": None,
        "app_env": "production",
        "auth_store": "dynamodb",
        "dynamodb_table": "auth",
        "google_client_secret_arn": "fixture-arn",
        "credential_kms_key_id": "fixture-kms",
        "cookie_secure": True,
        "frontend_url": "https://mailroom.example",
        "google_redirect_uri": "https://mailroom.example/auth/google/callback",
    }
    fields.update(changes)
    return Settings(**fields)


@pytest.mark.parametrize(
    "changes",
    [
        {"cookie_secure": False},
        {"auth_store": "memory"},
        {"dynamodb_table": ""},
        {"credential_kms_key_id": ""},
        {"google_client_secret_arn": ""},
        {"frontend_url": "http://mailroom.example"},
        {"google_redirect_uri": "https://other.example/auth/google/callback"},
        {"frontend_url": "https://user:password@mailroom.example"},
    ],
)
def test_production_rejects_unsafe_configuration_outside_lambda(changes):
    with pytest.raises(RuntimeError, match="Production requires"):
        application.create_app(production_settings(**changes))


def test_production_headers_and_docs_do_not_need_cloud_access(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text('<div id="root"></div>')
    monkeypatch.setattr(application, "FRONTEND_DIST", tmp_path)
    with TestClient(application.create_app(production_settings())) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "script-src 'self'" in response.headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Strict-Transport-Security"] == "max-age=31536000"
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_modify_scope_and_host_cookie_in_production(monkeypatch):
    assert {scope for scope in SCOPES if "/auth/gmail." in scope} == {
        "https://www.googleapis.com/auth/gmail.modify"
    }
    store = Mock(spec=DynamoDBAuthStore)
    monkeypatch.setattr(
        "app.auth.google_oauth.GoogleOAuth.authorization_url",
        lambda *args: "https://accounts.google.com/",
    )
    with TestClient(
        application.create_app(
            production_settings(google_client_id="fixture"), store=store
        ),
        base_url="https://mailroom.example",
        follow_redirects=False,
    ) as client:
        response = client.get("/auth/google/login")
        assert response.status_code == 303
        cookie = response.headers["set-cookie"]
        assert cookie.startswith("__Host-mailroom_oauth_state=")
        assert "Secure" in cookie and "HttpOnly" in cookie and "Domain=" not in cookie


def test_large_request_is_rejected_before_parsing():
    with TestClient(
        application.create_app(Settings(_env_file=None), store=MemoryAuthStore())
    ) as client:
        response = client.post(
            "/api/mail/send",
            content=b"x" * (512 * 1024 + 1),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413
        assert response.headers["Cache-Control"] == "no-store"


def test_message_ignores_attachments_html_and_handles_unknown_charset():
    encoded = base64.urlsafe_b64encode(b"Safe text").decode()
    message = mail_message(
        {
            "id": "abc",
            "payload": {
                "mimeType": "multipart/mixed",
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "filename": "attachment.txt",
                        "body": {"data": "invalid"},
                    },
                    {"mimeType": "text/html", "body": {"data": "invalid"}},
                    {
                        "mimeType": "text/plain",
                        "headers": [
                            {
                                "name": "Content-Type",
                                "value": "text/plain; charset=made-up",
                            }
                        ],
                        "body": {"data": encoded},
                    },
                ],
            },
        }
    )
    assert message["body"] == "Safe text"


def test_oversize_provider_response_stops_reading_and_closes(monkeypatch):
    credentials = Mock()
    credentials.load.return_value = (
        "encrypted",
        {
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
            "token": "fixture",
            "expiry": "2099-01-01T00:00:00",
        },
    )
    response = Mock(status_code=200)
    response.iter_content.return_value = iter([b"x" * MAX_RESPONSE_BYTES, b"x"])
    http = Mock()
    http.__enter__ = Mock(return_value=http)
    http.__exit__ = Mock(return_value=False)
    http.request.return_value = response
    monkeypatch.setattr("app.mail.gmail_client.requests.Session", lambda: http)
    with pytest.raises(HTTPException) as caught:
        GmailClient(credentials).request("alice", "GET", "/messages/abc")
    assert caught.value.status_code == 413
    response.close.assert_called_once()


def test_deeply_nested_message_is_rejected():
    root = {"mimeType": "multipart/mixed", "parts": []}
    node = root
    for _ in range(22):
        child = {"mimeType": "multipart/mixed", "parts": []}
        node["parts"].append(child)
        node = child
    with pytest.raises(HTTPException) as caught:
        mail_message({"id": "abc", "payload": root})
    assert caught.value.status_code == 413
