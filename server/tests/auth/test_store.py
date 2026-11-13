"""Verify DynamoDB requests without real AWS credentials or network access."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from unittest.mock import Mock

import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from botocore.stub import Stubber

from app.auth.models import PendingLogin, Session
from app.auth.store import (
    AuthStoreError,
    DynamoDBAuthStore,
    MemoryAuthStore,
    session_key,
)


@pytest.fixture
def table():
    mock = Mock()
    mock.get_item.return_value = {}
    return mock


@pytest.fixture
def store(table, monkeypatch):
    store = DynamoDBAuthStore("auth-table")
    monkeypatch.setattr(store, "_table", lambda: table)
    return store


def test_creation_is_lazy(monkeypatch):
    factory = Mock(side_effect=AssertionError("AWS initialization attempted"))
    monkeypatch.setattr("app.auth.store.boto3.session.Session", factory)
    DynamoDBAuthStore("auth-table")
    factory.assert_not_called()


def test_writes_use_distinct_keys_integer_ttl_and_no_overwrite(store, table):
    store.save_oauth("state", PendingLogin("verifier", "nonce", 601.5))
    store.save_session("session", Session({"id": "user"}, 28801.5))
    oauth, session = [call.kwargs for call in table.put_item.call_args_list]
    assert oauth["Item"] == {
        "pk": "oauth#state",
        "verifier": "verifier",
        "nonce": "nonce",
        "expires_at": 601,
    }
    assert session["Item"] == {
        "pk": session_key("session"),
        "user": {"id": "user"},
        "expires_at": 28801,
        "generation": "",
    }
    assert oauth["ConditionExpression"] == "attribute_not_exists(pk)"
    assert session["ConditionExpression"] == "attribute_not_exists(pk)"
    assert type(oauth["Item"]["expires_at"]) is int
    assert type(session["Item"]["expires_at"]) is int


def test_oauth_consumption_is_one_conditional_returning_delete(store, table):
    table.delete_item.return_value = {
        "Attributes": {
            "pk": "oauth#state",
            "verifier": "v",
            "nonce": "n",
            "expires_at": Decimal(601),
        }
    }
    assert store.consume_oauth("state", 1) == PendingLogin("v", "n", 601)
    table.delete_item.assert_called_once_with(
        Key={"pk": "oauth#state"},
        ConditionExpression="attribute_exists(pk) AND expires_at > :now",
        ExpressionAttributeValues={":now": 1},
        ReturnValues="ALL_OLD",
    )
    table.get_item.assert_not_called()


def test_expired_missing_or_replayed_oauth_returns_none(store, table):
    table.delete_item.side_effect = ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "private"}},
        "DeleteItem",
    )
    assert store.consume_oauth("state", 601) is None


@pytest.mark.parametrize("item", [None, {"user": {"id": "user"}, "expires_at": 601}])
def test_expired_or_missing_session_rejected_before_ttl_cleanup(store, table, item):
    table.get_item.return_value = {"Item": item} if item else {}
    assert store.read_session("session", 601) is None
    table.get_item.assert_called_once_with(
        Key={"pk": session_key("session")}, ConsistentRead=True
    )


def test_session_read_converts_dynamodb_number(store, table):
    table.get_item.side_effect = [
        {
            "Item": {
                "user": {"id": "user"},
                "expires_at": Decimal(601),
                "generation": "",
            }
        },
        {},
    ]
    assert store.read_session("session", 600) == Session({"id": "user"}, 601)


def test_deletes_are_scoped_by_item_type(store, table):
    store.delete_session("id")
    store.delete_oauth("id")
    assert [call.kwargs for call in table.delete_item.call_args_list] == [
        {"Key": {"pk": session_key("id")}},
        {"Key": {"pk": "oauth#id"}},
    ]


@pytest.mark.parametrize(
    "error",
    [
        ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "secret-data"}},
            "GetItem",
        ),
        EndpointConnectionError(endpoint_url="https://private.example"),
    ],
)
def test_storage_errors_are_sanitized_and_never_treated_as_missing(store, table, error):
    table.get_item.side_effect = error
    with pytest.raises(AuthStoreError) as caught:
        store.read_session("private-session", 1)
    assert str(caught.value) == "Authentication storage unavailable"
    assert caught.value.__suppress_context__


def test_write_collision_fails_instead_of_overwriting(store, table):
    table.put_item.side_effect = ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
    )
    with pytest.raises(AuthStoreError):
        store.save_oauth("state", PendingLogin("v", "n", 600))


def test_local_fake_consumes_once_under_concurrency():
    store = MemoryAuthStore()
    store.save_oauth("state", PendingLogin("v", "n", 600))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.consume_oauth("state", 1), range(16)))
    assert sum(result is not None for result in results) == 1


def test_conditional_consume_with_real_sdk_serialization(monkeypatch):
    # Explicit dummy credentials plus Stubber prevent all real AWS requests.
    resource = boto3.session.Session().resource(
        "dynamodb",
        region_name="us-east-2",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    table = resource.Table("auth-table")
    store = DynamoDBAuthStore("auth-table")
    monkeypatch.setattr(store, "_table", lambda: table)
    with Stubber(table.meta.client) as stub:
        stub.add_response(
            "delete_item",
            {
                "Attributes": {
                    "pk": {"S": "oauth#state"},
                    "verifier": {"S": "v"},
                    "nonce": {"S": "n"},
                    "expires_at": {"N": "601"},
                }
            },
        )
        stub.add_client_error(
            "delete_item", service_error_code="ConditionalCheckFailedException"
        )
        assert store.consume_oauth("state", 1) == PendingLogin("v", "n", 601)
        assert store.consume_oauth("state", 1) is None
        stub.assert_no_pending_responses()
