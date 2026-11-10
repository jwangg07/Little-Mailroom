"""Authenticated encryption for per-user OAuth credentials; no plaintext storage."""

import base64
import json
import os

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from auth_store import AuthStoreError


def encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


class CredentialVault:
    def __init__(self, kms_key_id: str = "", local_key: str = ""):
        self.kms_key_id = kms_key_id
        self.local_key = (
            base64.b64decode(local_key, validate=True) if local_key else os.urandom(32)
        )
        if len(self.local_key) != 32:
            raise ValueError("CREDENTIAL_ENCRYPTION_KEY must encode 32 bytes")

    def _kms(self):
        return boto3.session.Session().client(
            "kms",
            config=Config(
                connect_timeout=3,
                read_timeout=3,
                retries={"mode": "standard", "total_max_attempts": 2},
            ),
        )

    def encrypt(self, user_id: str, data: dict) -> str:
        context = {"app": "mini-mailroom", "user_id": user_id}
        try:
            envelope = {"version": 1, "mode": "kms" if self.kms_key_id else "local"}
            key = self.local_key
            if self.kms_key_id:
                response = self._kms().generate_data_key(
                    KeyId=self.kms_key_id, KeySpec="AES_256", EncryptionContext=context
                )
                key = response["Plaintext"]
                envelope["key"] = encode(response["CiphertextBlob"])
            nonce = os.urandom(12)
            envelope["nonce"] = encode(nonce)
            envelope["ciphertext"] = encode(
                AESGCM(key).encrypt(
                    nonce,
                    json.dumps(data).encode(),
                    json.dumps(context, sort_keys=True).encode(),
                )
            )
            return json.dumps(envelope)
        except (BotoCoreError, ClientError, ValueError, KeyError, TypeError):
            raise AuthStoreError("Credential encryption unavailable") from None

    def decrypt(self, user_id: str, encrypted: str) -> dict:
        context = {"app": "mini-mailroom", "user_id": user_id}
        try:
            envelope = json.loads(encrypted)
            if envelope["version"] != 1 or envelope["mode"] != (
                "kms" if self.kms_key_id else "local"
            ):
                raise ValueError
            key = self.local_key
            if self.kms_key_id:
                key = self._kms().decrypt(
                    KeyId=self.kms_key_id,
                    CiphertextBlob=base64.b64decode(envelope["key"], validate=True),
                    EncryptionContext=context,
                )["Plaintext"]
            return json.loads(
                AESGCM(key).decrypt(
                    base64.b64decode(envelope["nonce"], validate=True),
                    base64.b64decode(envelope["ciphertext"], validate=True),
                    json.dumps(context, sort_keys=True).encode(),
                )
            )
        except (
            BotoCoreError,
            ClientError,
            InvalidTag,
            ValueError,
            KeyError,
            TypeError,
        ):
            raise AuthStoreError("Credential decryption unavailable") from None
