"""Authentication storage. AWS resources are created only on first use."""

from copy import deepcopy
from dataclasses import dataclass
from threading import Lock, local
from typing import Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


@dataclass(frozen=True)
class PendingLogin:
    verifier: str
    nonce: str
    expires_at: int


@dataclass(frozen=True)
class Session:
    user: dict[str, str]
    expires_at: int


class AuthStoreError(Exception):
    """Storage unavailable; contains no provider message or authentication data."""


class AuthStore(Protocol):
    def save_oauth(self, state: str, attempt: PendingLogin) -> None: ...
    def consume_oauth(self, state: str, now: int) -> PendingLogin | None: ...
    def save_session(self, session_id: str, session: Session) -> None: ...
    def read_session(self, session_id: str, now: int) -> Session | None: ...
    def delete_session(self, session_id: str) -> None: ...
    def delete_oauth(self, state: str) -> None: ...


class DynamoDBAuthStore:
    def __init__(self, table_name: str):
        self.table_name = table_name
        # Boto3 resources aren't thread safe; FastAPI sync routes use threads.
        self._thread = local()

    def _table(self):
        if not self.table_name:
            raise AuthStoreError("DYNAMODB_TABLE is required for authentication")
        if not hasattr(self._thread, "table"):
            resource = boto3.session.Session().resource(
                "dynamodb",
                config=Config(
                    connect_timeout=3,
                    read_timeout=3,
                    retries={"mode": "standard", "total_max_attempts": 2},
                ),
            )
            self._thread.table = resource.Table(self.table_name)
        return self._thread.table

    def _call(self, operation: str, **kwargs):
        try:
            return getattr(self._table(), operation)(**kwargs)
        except ClientError as exc:
            if (
                operation == "delete_item"
                and "ConditionExpression" in kwargs
                and exc.response["Error"]["Code"] == "ConditionalCheckFailedException"
            ):
                return {}
            raise AuthStoreError("Authentication storage unavailable") from None
        except BotoCoreError:
            raise AuthStoreError("Authentication storage unavailable") from None

    def save_oauth(self, state: str, attempt: PendingLogin) -> None:
        self._call(
            "put_item",
            Item={
                "pk": "oauth#" + state,
                "verifier": attempt.verifier,
                "nonce": attempt.nonce,
                "expires_at": int(attempt.expires_at),
            },
            ConditionExpression="attribute_not_exists(pk)",
        )

    def consume_oauth(self, state: str, now: int) -> PendingLogin | None:
        item = self._call(
            "delete_item",
            Key={"pk": "oauth#" + state},
            ConditionExpression="attribute_exists(pk) AND expires_at > :now",
            ExpressionAttributeValues={":now": int(now)},
            ReturnValues="ALL_OLD",
        ).get("Attributes")
        if not item or item["expires_at"] <= int(now):
            return None
        return PendingLogin(item["verifier"], item["nonce"], int(item["expires_at"]))

    def save_session(self, session_id: str, session: Session) -> None:
        self._call(
            "put_item",
            Item={
                "pk": "session#" + session_id,
                "user": dict(session.user),
                "expires_at": int(session.expires_at),
            },
            ConditionExpression="attribute_not_exists(pk)",
        )

    def read_session(self, session_id: str, now: int) -> Session | None:
        item = self._call(
            "get_item", Key={"pk": "session#" + session_id}, ConsistentRead=True
        ).get("Item")
        if not item or item["expires_at"] <= int(now):
            return None
        return Session(dict(item["user"]), int(item["expires_at"]))

    def delete_session(self, session_id: str) -> None:
        self._call("delete_item", Key={"pk": "session#" + session_id})

    def delete_oauth(self, state: str) -> None:
        self._call("delete_item", Key={"pk": "oauth#" + state})


class MemoryAuthStore:
    """Explicit local/test store only; never a fallback for AWS errors."""

    def __init__(self):
        self._pending: dict[str, PendingLogin] = {}
        self._sessions: dict[str, Session] = {}
        self._lock = Lock()

    def save_oauth(self, state: str, attempt: PendingLogin) -> None:
        with self._lock:
            if state in self._pending:
                raise AuthStoreError("Authentication identifier collision")
            self._pending[state] = deepcopy(attempt)

    def consume_oauth(self, state: str, now: int) -> PendingLogin | None:
        with self._lock:
            attempt = self._pending.pop(state, None)
            return attempt if attempt and attempt.expires_at > int(now) else None

    def save_session(self, session_id: str, session: Session) -> None:
        with self._lock:
            if session_id in self._sessions:
                raise AuthStoreError("Authentication identifier collision")
            self._sessions[session_id] = deepcopy(session)

    def read_session(self, session_id: str, now: int) -> Session | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session and session.expires_at > int(now):
                return deepcopy(session)
            self._sessions.pop(session_id, None)
            return None

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def delete_oauth(self, state: str) -> None:
        with self._lock:
            self._pending.pop(state, None)
