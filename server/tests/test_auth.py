"""Exercise OAuth boundaries without sending real credentials to Google."""

from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture
def client():
    settings = main.Settings(
        _env_file=None,
        google_client_id="test-client.apps.googleusercontent.com",
        google_client_secret="test-secret",
    )
    with TestClient(main.create_app(settings), follow_redirects=False) as client:
        yield client


def start_login(client):
    response = client.get("/auth/google/login")
    assert response.status_code == 303
    return parse_qs(urlsplit(response.headers["location"]).query)


def complete_login(client, monkeypatch, *, claims_override=None):
    params = start_login(client)
    flow = Mock(credentials=SimpleNamespace(id_token="private-id-token"))
    monkeypatch.setattr(main.Flow, "from_client_config", Mock(return_value=flow))
    claims = {
        "sub": "user-123",
        "email": "tester@example.com",
        "name": "Tester",
        "email_verified": True,
        "nonce": params["nonce"][0],
    }
    claims.update(claims_override or {})
    monkeypatch.setattr(main.id_token, "verify_oauth2_token", Mock(return_value=claims))
    response = client.get(
        "/auth/google/callback",
        params={
            "state": params["state"][0],
            "code": "test-code",
        },
    )
    return response, params, flow


def test_authorization_uses_identity_scopes_pkce_and_private_cookie(client):
    response = client.get("/auth/google/login")
    params = parse_qs(urlsplit(response.headers["location"]).query)
    assert params["redirect_uri"] == ["http://localhost:8000/auth/google/callback"]
    assert params["code_challenge_method"] == ["S256"]
    assert "gmail" not in params["scope"][0]
    assert params["nonce"] and params["state"]
    assert "test-secret" not in response.headers["location"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "no-store"


def test_missing_configuration_redirects_with_safe_error():
    app = main.create_app(main.Settings(_env_file=None))
    with TestClient(app, follow_redirects=False) as client:
        assert "not_configured" in client.get("/auth/google/login").headers["location"]


def test_callback_requires_matching_browser_state(client, monkeypatch):
    fetch = Mock()
    monkeypatch.setattr(main.Flow, "fetch_token", fetch)
    params = start_login(client)
    other_browser = TestClient(client.app, follow_redirects=False)
    response = other_browser.get(
        "/auth/google/callback",
        params={
            "state": params["state"][0],
            "code": "stolen-code",
        },
    )
    assert "invalid_state" in response.headers["location"]
    fetch.assert_not_called()


def test_success_session_replay_and_logout(client, monkeypatch):
    response, params, flow = complete_login(client, monkeypatch)
    assert response.headers["location"] == "http://localhost:5173/"
    flow.fetch_token.assert_called_once_with(code="test-code", timeout=15)
    main.id_token.verify_oauth2_token.assert_called_once()
    assert (
        main.id_token.verify_oauth2_token.call_args.args[2]
        == "test-client.apps.googleusercontent.com"
    )
    assert client.get("/auth/me").json()["user"]["email"] == "tester@example.com"
    assert "private-id-token" not in str(client.cookies)
    session_id = client.cookies.get(main.SESSION_COOKIE)
    replay = client.get(
        "/auth/google/callback",
        params={
            "state": params["state"][0],
            "code": "test-code",
        },
    )
    assert "invalid_state" in replay.headers["location"]
    assert client.post("/auth/logout").status_code == 403
    assert (
        client.post(
            "/auth/logout",
            headers={
                "X-Mailroom-Request": "1",
                "Origin": "https://foreign.example",
            },
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/auth/logout",
            headers={
                "X-Mailroom-Request": "1",
                "Origin": "http://localhost:5173",
            },
        ).status_code
        == 200
    )
    client.cookies.set(main.SESSION_COOKIE, session_id)
    assert client.get("/auth/me").json() == {"user": None}


@pytest.mark.parametrize("claims", [{"nonce": "wrong"}, {"email_verified": False}])
def test_invalid_identity_does_not_create_session(client, monkeypatch, claims):
    response, _, _ = complete_login(client, monkeypatch, claims_override=claims)
    assert "failed" in response.headers["location"]
    assert client.get("/auth/me").json() == {"user": None}


def test_provider_failure_does_not_expose_secrets(client, monkeypatch):
    params = start_login(client)
    monkeypatch.setattr(
        main.Flow, "fetch_token", Mock(side_effect=ValueError("private-token"))
    )
    response = client.get(
        "/auth/google/callback",
        params={
            "state": params["state"][0],
            "code": "test-code",
        },
    )
    assert "failed" in response.headers["location"]
    assert "private-token" not in response.text + str(response.headers)
    assert client.get("/auth/me").json() == {"user": None}


def test_cancellation(client):
    params = start_login(client)
    response = client.get(
        "/auth/google/callback",
        params={
            "state": params["state"][0],
            "error": "access_denied",
        },
    )
    assert "access_denied" in response.headers["location"]


def test_expired_pending_login(client, monkeypatch):
    params = start_login(client)
    now = main.time.time()
    monkeypatch.setattr(main.time, "time", lambda: now + main.LOGIN_TTL + 1)
    response = client.get(
        "/auth/google/callback",
        params={
            "state": params["state"][0],
            "code": "test-code",
        },
    )
    assert "invalid_state" in response.headers["location"]


def test_expired_session(client, monkeypatch):
    complete_login(client, monkeypatch)
    now = main.time.time()
    monkeypatch.setattr(main.time, "time", lambda: now + main.SESSION_TTL + 1)
    assert client.get("/auth/me").json() == {"user": None}
