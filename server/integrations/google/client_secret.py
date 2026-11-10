"""Lazy, cached JSON secret loading without exposing provider errors."""

import json
import time
from threading import Lock

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import SecretStr


class SecretUnavailableError(Exception):
    """Safe error suitable for handling without logging credentials."""


class GoogleClientSecret:
    def __init__(self, secret_arn: str, cache_seconds: int = 300):
        self._arn = secret_arn
        self._cache_seconds = cache_seconds
        self._value = SecretStr("")
        self._expires_at = 0.0
        self._lock = Lock()

    def get(self) -> str:
        # Only login/callback needs this value; health/static routes never call AWS.
        with self._lock:
            if self._value.get_secret_value() and time.monotonic() < self._expires_at:
                return self._value.get_secret_value()
            try:
                client = boto3.session.Session().client(
                    "secretsmanager",
                    config=Config(
                        connect_timeout=3,
                        read_timeout=3,
                        retries={"mode": "standard", "total_max_attempts": 2},
                    ),
                )
                response = client.get_secret_value(SecretId=self._arn)
                data = json.loads(response["SecretString"])
                value = data["GOOGLE_CLIENT_SECRET"]
                if not isinstance(value, str) or not value.strip():
                    raise ValueError
            except (BotoCoreError, ClientError, ValueError, TypeError, KeyError):
                # No stale-value fallback after cache expiration or provider text.
                raise SecretUnavailableError("Google credentials unavailable") from None
            self._value = SecretStr(value)
            self._expires_at = time.monotonic() + self._cache_seconds
            return value
