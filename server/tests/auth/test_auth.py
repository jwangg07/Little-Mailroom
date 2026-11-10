"""Exercise OAuth boundaries without sending real credentials to Google."""

from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from app import application as main
from app.auth import google_oauth as auth
from app.auth import router as auth_router
from app.auth.security import SESSION_COOKIE, STATE_COOKIE
from app.auth.store import DynamoDBAuthStore, MemoryAuthStore
from app.config import Settings
from integrations.google.client_secret import SecretUnavailableError


@pytest.fixture
def client():
    settings = Settings(
        _env_file=None,
        google_client_id="test-client.apps.googleusercontent.com",
        google_client_secret="test-secret",
    )
    with TestClient(
        main.create_app(settings, store=MemoryAuthStore()), follow_redirects=False
    ) as client:
        yield client


def start_login(client):
    response = client.get("/auth/google/login")
    assert response.status_code == 303
    return parse_qs(urlsplit(response.headers["location"]).query)


def complete_login(client, monkeypatch, *, claims_override=None):
    params = start_login(client)
    flow = Mock(
        credentials=SimpleNamespace(
            id_token="private-id-token",
            token="private-access-token",
            refresh_token="private-refresh-token",
            expiry=None,
            granted_scopes=None,
            scopes=auth.SCOPES,
        )
    )
    monkeypatch.setattr(auth.Flow, "from_client_config", Mock(return_value=flow))
    claims = {
        "sub": "user-123",
        "email": "tester@example.com",
        "name": "Tester",
        "email_verified": True,
        "nonce": params["nonce"][0],
    }
    claims.update(claims_override or {})
    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", Mock(return_value=claims))
    response = client.get(
        "/auth/google/callback",
        params={
            "state": params["state"][0],
            "code": "test-code",
        },
    )
    return response, params, flow


def test_authorization_uses_configured_scopes_pkce_and_private_cookie(client):
    response = client.get("/auth/google/login")
    params = parse_qs(urlsplit(response.headers["location"]).query)
    assert params["redirect_uri"] == ["http://localhost:8000/auth/google/callback"]
    assert params["code_challenge_method"] == ["S256"]
    assert set(params["scope"][0].split()) == set(auth.SCOPES)
    assert params["nonce"] and params["state"]
    assert "test-secret" not in response.headers["location"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "no-store"


def test_missing_configuration_redirects_with_safe_error():
    app = main.create_app(Settings(_env_file=None))
    with TestClient(app, follow_redirects=False) as client:
        assert "not_configured" in client.get("/auth/google/login").headers["location"]


def test_callback_requires_matching_browser_state(client, monkeypatch):
    fetch = Mock()
    monkeypatch.setattr(auth.Flow, "fetch_token", fetch)
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
    auth.id_token.verify_oauth2_token.assert_called_once()
    assert (
        auth.id_token.verify_oauth2_token.call_args.args[2]
        == "test-client.apps.googleusercontent.com"
    )
    assert client.get("/auth/me").json()["user"]["email"] == "tester@example.com"
    assert "private-id-token" not in str(client.cookies)
    session_id = client.cookies.get(SESSION_COOKIE)
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
    client.cookies.set(SESSION_COOKIE, session_id)
    assert client.get("/auth/me").json() == {"user": None}


@pytest.mark.parametrize("claims", [{"nonce": "wrong"}, {"email_verified": False}])
def test_invalid_identity_does_not_create_session(client, monkeypatch, claims):
    response, _, _ = complete_login(client, monkeypatch, claims_override=claims)
    assert "failed" in response.headers["location"]
    assert client.get("/auth/me").json() == {"user": None}


def test_provider_failure_does_not_expose_secrets(client, monkeypatch, caplog):
    params = start_login(client)
    monkeypatch.setattr(
        auth.Flow, "fetch_token", Mock(side_effect=ValueError("private-token"))
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
    assert "private-token" not in caplog.text
    assert "stage=token_exchange exception_type=ValueError" in caplog.text
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
    now = auth_router.time.time()
    monkeypatch.setattr(
        auth_router.time, "time", lambda: now + auth_router.LOGIN_TTL + 1
    )
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
    now = auth_router.time.time()
    monkeypatch.setattr(
        auth_router.time, "time", lambda: now + auth_router.SESSION_TTL + 1
    )
    assert client.get("/auth/me").json() == {"user": None}


def test_smoke_routes_do_not_initialize_aws(monkeypatch):
    factory = Mock(side_effect=AssertionError("AWS initialization attempted"))
    monkeypatch.setattr("app.auth.store.boto3.session.Session", factory)
    app = main.create_app(
        Settings(
            _env_file=None,
            dynamodb_table="auth-table",
            google_client_secret_arn="arn:test",
            credential_kms_key_id="arn:kms:test",
            cookie_secure=True,
        )
    )
    with TestClient(app) as browser:
        assert browser.get("/health").json() == {"status": "ok"}
        assert browser.get("/auth/me").json() == {"user": None}
    factory.assert_not_called()


def test_shared_store_survives_app_replacement_and_logout(monkeypatch):
    store = MemoryAuthStore()
    settings = Settings(
        _env_file=None,
        google_client_id="test-client.apps.googleusercontent.com",
        google_client_secret="test-secret",
    )
    first_app = main.create_app(settings, store=store)
    second_app = main.create_app(settings, store=store)
    with TestClient(first_app, follow_redirects=False) as first:
        params = start_login(first)
        flow = Mock(
            credentials=SimpleNamespace(
                id_token="private-id-token",
                token="private-access-token",
                refresh_token="private-refresh-token",
                expiry=None,
                granted_scopes=None,
                scopes=auth.SCOPES,
            )
        )
        monkeypatch.setattr(auth.Flow, "from_client_config", Mock(return_value=flow))
        monkeypatch.setattr(
            auth.id_token,
            "verify_oauth2_token",
            Mock(
                return_value={
                    "sub": "user",
                    "email": "user@example.com",
                    "name": "User",
                    "nonce": params["nonce"][0],
                    "email_verified": True,
                }
            ),
        )
        with TestClient(second_app, follow_redirects=False) as second:
            second.cookies.update(first.cookies)
            result = second.get(
                "/auth/google/callback",
                params={"state": params["state"][0], "code": "test-code"},
            )
            assert "auth_error" not in result.headers["location"]
            first.cookies.update(second.cookies)
            assert first.get("/auth/me").json()["user"]["id"] == "user"
            assert (
                second.post(
                    "/auth/logout", headers={"X-Mailroom-Request": "1"}
                ).status_code
                == 200
            )
            assert first.get("/auth/me").json() == {"user": None}


def test_storage_failure_returns_safe_503_without_fallback(monkeypatch, caplog):
    table = Mock()
    store = DynamoDBAuthStore("auth-table")
    monkeypatch.setattr(store, "_table", lambda: table)
    table.get_item.side_effect = ClientError(
        {
            "Error": {
                "Code": "AccessDeniedException",
                "Message": "private-provider-data",
            }
        },
        "GetItem",
    )
    with TestClient(main.create_app(Settings(_env_file=None), store=store)) as browser:
        browser.cookies.set(SESSION_COOKIE, "private-session")
        response = browser.get("/auth/me")
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert "private-provider-data" not in response.text + caplog.text
    assert "private-session" not in caplog.text
    assert all(
        record.exc_info is None
        for record in caplog.records
        if record.name == "app.http"
    )


@pytest.mark.parametrize(
    "settings",
    [
        {"auth_store": "memory", "dynamodb_table": "auth-table"},
        {"dynamodb_table": ""},
    ],
)
def test_lambda_rejects_memory_or_missing_table(monkeypatch, settings):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "mini-mailroom")
    with pytest.raises(RuntimeError, match="Lambda requires"):
        main.create_app(Settings(_env_file=None, **settings))


def test_lambda_configures_dynamodb_lazily(monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "mini-mailroom")
    factory = Mock(side_effect=AssertionError("AWS initialization attempted"))
    monkeypatch.setattr("app.auth.store.boto3.session.Session", factory)
    app = main.create_app(
        Settings(
            _env_file=None,
            dynamodb_table="auth-table",
            google_client_secret_arn="arn:test",
            credential_kms_key_id="arn:kms:test",
            cookie_secure=True,
        )
    )
    with TestClient(app) as browser:
        assert browser.get("/health").status_code == 200
    factory.assert_not_called()


def test_mismatched_cookie_never_consumes_oauth(monkeypatch):
    store = Mock()
    with TestClient(
        main.create_app(Settings(_env_file=None), store=store),
        follow_redirects=False,
    ) as browser:
        browser.cookies.set(STATE_COOKIE, "browser-state")
        response = browser.get(
            "/auth/google/callback",
            params={"state": "other-state", "code": "private-code"},
        )
    assert "invalid_state" in response.headers["location"]
    store.consume_oauth.assert_not_called()


def test_new_login_revokes_previous_session(client, monkeypatch):
    complete_login(client, monkeypatch)
    old_id = client.cookies.get(SESSION_COOKIE)
    # Let the second login generate a fresh real authorization URL.
    monkeypatch.undo()
    complete_login(client, monkeypatch)
    assert client.cookies.get(SESSION_COOKIE) != old_id
    with TestClient(client.app) as old_browser:
        old_browser.cookies.set(SESSION_COOKIE, old_id)
        assert old_browser.get("/auth/me").json() == {"user": None}


def test_deployed_cookie_flags(monkeypatch):
    settings = Settings(
        _env_file=None,
        cookie_secure=True,
        frontend_url="https://mailroom.example",
        google_redirect_uri="https://mailroom.example/auth/google/callback",
        google_client_id="test-client.apps.googleusercontent.com",
        google_client_secret="test-secret",
    )
    with TestClient(
        main.create_app(settings, store=MemoryAuthStore()),
        base_url="https://mailroom.example",
        follow_redirects=False,
    ) as browser:
        response, _, _ = complete_login(browser, monkeypatch)
        cookie = next(
            value
            for value in response.headers.get_list("set-cookie")
            if value.startswith(SESSION_COOKIE + "=")
        )
        assert all(flag in cookie for flag in ["HttpOnly", "SameSite=lax", "Secure"])


def test_secret_arn_is_lazy_and_supplies_extracted_value(monkeypatch):
    settings = Settings(
        _env_file=None,
        google_client_id="test-client.apps.googleusercontent.com",
        google_client_secret_arn="arn:test",
        credential_kms_key_id="arn:kms:test",
    )
    getter = Mock(return_value="aws-secret-value")
    monkeypatch.setattr(main.GoogleClientSecret, "get", getter)
    flow_factory = Mock(wraps=auth.Flow.from_client_config)
    monkeypatch.setattr(auth.Flow, "from_client_config", flow_factory)
    with TestClient(
        main.create_app(settings, store=MemoryAuthStore()), follow_redirects=False
    ) as browser:
        assert browser.get("/health").status_code == 200
        assert browser.get("/auth/me").json() == {"user": None}
        getter.assert_not_called()
        start_login(browser)
    assert flow_factory.call_args.args[0]["web"]["client_secret"] == "aws-secret-value"


def test_secret_lookup_failure_does_not_start_login(monkeypatch, caplog):
    settings = Settings(
        _env_file=None,
        google_client_id="test-client.apps.googleusercontent.com",
        google_client_secret_arn="arn:test",
        credential_kms_key_id="arn:kms:test",
    )
    store = Mock()
    monkeypatch.setattr(
        main.GoogleClientSecret,
        "get",
        Mock(side_effect=SecretUnavailableError("Google credentials unavailable")),
    )
    with TestClient(
        main.create_app(settings, store=store), follow_redirects=False
    ) as browser:
        response = browser.get("/auth/google/login")
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert "Google credential lookup unavailable" in caplog.text
    store.save_oauth.assert_not_called()


@pytest.mark.parametrize(
    "overrides",
    [
        {"google_client_secret_arn": ""},
        {"cookie_secure": False},
    ],
)
def test_lambda_requires_secret_arn_and_secure_cookies(monkeypatch, overrides):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "mini-mailroom")
    fields = {
        "_env_file": None,
        "dynamodb_table": "auth-table",
        "google_client_secret_arn": "arn:test",
        "cookie_secure": True,
    }
    fields.update(overrides)
    with pytest.raises(RuntimeError, match="Lambda requires GOOGLE_CLIENT_SECRET_ARN"):
        main.create_app(Settings(**fields))
