"""Authentication storage. AWS resources are created only on first use."""

import hashlib
import secrets
import time
from copy import deepcopy
from dataclasses import replace
from threading import Lock, local
from typing import Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.auth.models import PendingLogin, Session


class AuthStoreError(Exception):
    """Storage unavailable; contains no provider message or authentication data."""


def session_key(session_id: str) -> str:
    # A new namespace deliberately invalidates all pre-migration bearer records.
    return "session-v2#" + hashlib.sha256(session_id.encode()).hexdigest()


CREDENTIAL_TTL = 30 * 24 * 60 * 60


class AuthStore(Protocol):
    def read_credentials(self, user_id: str) -> str | None: ...
    def save_credentials(self, user_id: str, encrypted: str) -> None: ...
    def replace_credentials(self, user_id: str, old: str, encrypted: str) -> bool: ...
    def save_oauth(self, state: str, attempt: PendingLogin) -> None: ...
    def consume_oauth(self, state: str, now: int) -> PendingLogin | None: ...
    def save_session(self, session_id: str, session: Session) -> None: ...
    def read_session(self, session_id: str, now: int) -> Session | None: ...
    def delete_session(self, session_id: str) -> None: ...
    def delete_oauth(self, state: str) -> None: ...
    def invalidate_user_sessions(self, user_id: str) -> None: ...
    def delete_credentials(self, user_id: str, expected: str) -> bool: ...
    def take_rate_limit(self, key: str, limit: int, window: int, now: int) -> bool: ...


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
                "pk": session_key(session_id),
                "user": dict(session.user),
                "expires_at": int(session.expires_at),
                "generation": self._generation(session.user["id"]),
            },
            ConditionExpression="attribute_not_exists(pk)",
        )

    def read_session(self, session_id: str, now: int) -> Session | None:
        item = self._call(
            "get_item", Key={"pk": session_key(session_id)}, ConsistentRead=True
        ).get("Item")
        if not item or item["expires_at"] <= int(now):
            return None
        generation = self._generation(item["user"]["id"])
        if item.get("generation") != generation:
            return None
        return Session(dict(item["user"]), int(item["expires_at"]), generation)

    def delete_session(self, session_id: str) -> None:
        self._call("delete_item", Key={"pk": session_key(session_id)})

    def _generation(self, user_id: str) -> str:
        item = self._call(
            "get_item", Key={"pk": "session-generation#" + user_id}, ConsistentRead=True
        ).get("Item")
        return item["generation"] if item else ""

    def invalidate_user_sessions(self, user_id: str) -> None:
        # Keep this marker: expiring it could re-enable older sessions.
        self._call(
            "put_item",
            Item={
                "pk": "session-generation#" + user_id,
                "generation": secrets.token_urlsafe(32),
            },
        )

    def delete_oauth(self, state: str) -> None:
        self._call("delete_item", Key={"pk": "oauth#" + state})

    def read_credentials(self, user_id: str) -> str | None:
        item = self._call(
            "get_item", Key={"pk": "credentials#" + user_id}, ConsistentRead=True
        ).get("Item")
        if not item:
            return None
        if item.get("expires_at", 0) <= int(time.time()):
            self.delete_credentials(user_id, item["encrypted"])
            return None
        return item["encrypted"]

    def save_credentials(self, user_id: str, encrypted: str) -> None:
        self._call(
            "put_item",
            Item={
                "pk": "credentials#" + user_id,
                "encrypted": encrypted,
                "expires_at": int(time.time()) + CREDENTIAL_TTL,
            },
        )

    def replace_credentials(self, user_id: str, old: str, encrypted: str) -> bool:
        # A refresh must never overwrite a newer login or another refresh.
        try:
            self._table().put_item(
                Item={
                    "pk": "credentials#" + user_id,
                    "encrypted": encrypted,
                    "expires_at": int(time.time()) + CREDENTIAL_TTL,
                },
                ConditionExpression="encrypted = :old",
                ExpressionAttributeValues={":old": old},
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise AuthStoreError("Credential storage unavailable") from None
        except BotoCoreError:
            raise AuthStoreError("Credential storage unavailable") from None

    def delete_credentials(self, user_id: str, expected: str) -> bool:
        result = self._call(
            "delete_item",
            Key={"pk": "credentials#" + user_id},
            ConditionExpression="encrypted = :old",
            ExpressionAttributeValues={":old": expected},
            ReturnValues="ALL_OLD",
        )
        return bool(result.get("Attributes"))

    def take_rate_limit(self, key: str, limit: int, window: int, now: int) -> bool:
        bucket = now // window
        try:
            self._table().update_item(
                Key={"pk": f"rate#{key}#{bucket}"},
                UpdateExpression="SET expires_at = :ttl ADD hits :one",
                ConditionExpression="attribute_not_exists(hits) OR hits < :limit",
                ExpressionAttributeValues={
                    ":ttl": (bucket + 2) * window,
                    ":one": 1,
                    ":limit": limit,
                },
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise AuthStoreError("Rate limit storage unavailable") from None
        except BotoCoreError:
            raise AuthStoreError("Rate limit storage unavailable") from None


class MemoryAuthStore:
    """Explicit local/test store only; never a fallback for AWS errors."""

    def __init__(self, max_entries: int = 10_000):
        self._pending: dict[str, PendingLogin] = {}
        self._sessions: dict[str, Session] = {}
        self._credentials: dict[str, str] = {}
        self._credential_expiry: dict[str, int] = {}
        self._generations: dict[str, str] = {}
        self._rates: dict[str, tuple[int, int]] = {}
        self.max_entries = max_entries
        self._lock = Lock()

    def _prune(self, now: int) -> None:
        for mapping in (self._pending, self._sessions):
            for key in [
                key for key, value in mapping.items() if value.expires_at <= now
            ]:
                del mapping[key]
        for key in [
            key for key, expiry in self._credential_expiry.items() if expiry <= now
        ]:
            self._credentials.pop(key, None)
            del self._credential_expiry[key]
        for key in [key for key, (_, expiry) in self._rates.items() if expiry <= now]:
            del self._rates[key]

    def _room(self, mapping: dict, key: str) -> None:
        if key not in mapping and len(mapping) >= self.max_entries:
            raise AuthStoreError("Local authentication capacity reached")

    def save_oauth(self, state: str, attempt: PendingLogin) -> None:
        with self._lock:
            self._prune(int(time.time()))
            self._room(self._pending, state)
            if state in self._pending:
                raise AuthStoreError("Authentication identifier collision")
            self._pending[state] = deepcopy(attempt)

    def consume_oauth(self, state: str, now: int) -> PendingLogin | None:
        with self._lock:
            self._prune(now)
            attempt = self._pending.pop(state, None)
            return attempt if attempt and attempt.expires_at > int(now) else None

    def save_session(self, session_id: str, session: Session) -> None:
        with self._lock:
            self._prune(int(time.time()))
            key = session_key(session_id)
            self._room(self._sessions, key)
            if key in self._sessions:
                raise AuthStoreError("Authentication identifier collision")
            self._sessions[key] = replace(
                deepcopy(session),
                generation=self._generations.get(session.user["id"], ""),
            )

    def read_session(self, session_id: str, now: int) -> Session | None:
        with self._lock:
            self._prune(now)
            key = session_key(session_id)
            session = self._sessions.get(key)
            if session and session.generation == self._generations.get(
                session.user["id"], ""
            ):
                return deepcopy(session)
            self._sessions.pop(key, None)
            return None

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_key(session_id), None)

    def invalidate_user_sessions(self, user_id: str) -> None:
        with self._lock:
            self._room(self._generations, user_id)
            self._generations[user_id] = secrets.token_urlsafe(32)

    def delete_oauth(self, state: str) -> None:
        with self._lock:
            self._pending.pop(state, None)

    def read_credentials(self, user_id: str) -> str | None:
        with self._lock:
            self._prune(int(time.time()))
            return self._credentials.get(user_id)

    def save_credentials(self, user_id: str, encrypted: str) -> None:
        with self._lock:
            self._prune(int(time.time()))
            self._room(self._credentials, user_id)
            self._credentials[user_id] = encrypted
            self._credential_expiry[user_id] = int(time.time()) + CREDENTIAL_TTL

    def replace_credentials(self, user_id: str, old: str, encrypted: str) -> bool:
        with self._lock:
            self._prune(int(time.time()))
            if self._credentials.get(user_id) != old:
                return False
            self._credentials[user_id] = encrypted
            self._credential_expiry[user_id] = int(time.time()) + CREDENTIAL_TTL
            return True

    def delete_credentials(self, user_id: str, expected: str) -> bool:
        with self._lock:
            if self._credentials.get(user_id) != expected:
                return False
            del self._credentials[user_id]
            self._credential_expiry.pop(user_id, None)
            return True

    def take_rate_limit(self, key: str, limit: int, window: int, now: int) -> bool:
        with self._lock:
            self._prune(now)
            bucket_key = f"{key}#{now // window}"
            self._room(self._rates, bucket_key)
            count, expires = self._rates.get(
                bucket_key, (0, (now // window + 1) * window)
            )
            if count >= limit:
                return False
            self._rates[bucket_key] = (count + 1, expires)
            return True
