"""Secret extraction/cache behavior without credentials or network calls."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from integrations.google.client_secret import GoogleClientSecret, SecretUnavailableError

ARN = "arn:aws:secretsmanager:us-east-2:123456789012:secret:test-abcdef"


@pytest.fixture
def sdk(monkeypatch):
    client = Mock()
    client.get_secret_value.return_value = {
        "SecretString": '{"GOOGLE_CLIENT_SECRET":"test-secret"}'
    }
    session = Mock()
    session.client.return_value = client
    factory = Mock(return_value=session)
    monkeypatch.setattr(
        "integrations.google.client_secret.boto3.session.Session", factory
    )
    return factory, client


def test_lookup_is_lazy_and_uses_bare_arn(sdk):
    factory, client = sdk
    secret = GoogleClientSecret(ARN)
    factory.assert_not_called()
    assert secret.get() == "test-secret"
    client.get_secret_value.assert_called_once_with(SecretId=ARN)


def test_concurrent_reads_share_one_cached_lookup(sdk):
    _, client = sdk
    secret = GoogleClientSecret(ARN)
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: secret.get(), range(16)))
    assert values == ["test-secret"] * 16
    client.get_secret_value.assert_called_once()


def test_expired_cache_refreshes_and_never_serves_stale_on_failure(sdk, monkeypatch):
    _, client = sdk
    now = [1.0]
    monkeypatch.setattr(
        "integrations.google.client_secret.time.monotonic", lambda: now[0]
    )
    secret = GoogleClientSecret(ARN, cache_seconds=300)
    assert secret.get() == "test-secret"
    now[0] = 302.0
    client.get_secret_value.side_effect = EndpointConnectionError(
        endpoint_url="https://private.example"
    )
    with pytest.raises(SecretUnavailableError):
        secret.get()
    client.get_secret_value.side_effect = None
    client.get_secret_value.return_value = {
        "SecretString": '{"GOOGLE_CLIENT_SECRET":"rotated-secret"}'
    }
    assert secret.get() == "rotated-secret"
    assert client.get_secret_value.call_count == 3


@pytest.mark.parametrize(
    "response",
    [
        {"SecretString": "raw-secret-is-not-json"},
        {"SecretString": "{}"},
        {"SecretString": "[]"},
        {"SecretString": '{"GOOGLE_CLIENT_SECRET":null}'},
        {"SecretString": '{"GOOGLE_CLIENT_SECRET":"  "}'},
        {"SecretString": '{"GOOGLE_CLIENT_SECRET":123}'},
        {},
    ],
)
def test_invalid_secret_fails_safely(sdk, response):
    _, client = sdk
    client.get_secret_value.return_value = response
    with pytest.raises(SecretUnavailableError) as caught:
        GoogleClientSecret(ARN).get()
    assert str(caught.value) == "Google credentials unavailable"
    assert caught.value.__suppress_context__


def test_aws_error_message_is_not_exposed(sdk):
    _, client = sdk
    client.get_secret_value.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "private-secret-data"}},
        "GetSecretValue",
    )
    with pytest.raises(SecretUnavailableError) as caught:
        GoogleClientSecret(ARN).get()
    assert "private-secret-data" not in str(caught.value)
    assert caught.value.__suppress_context__
