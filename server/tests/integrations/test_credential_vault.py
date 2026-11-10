"""Credential encryption and KMS boundaries without network access."""

import base64
import json
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from app.auth.store import AuthStoreError
from integrations.google.credential_vault import CredentialVault


def test_vault_ciphertext_is_random_and_bound_to_user():
    vault = CredentialVault()
    data = {"token": "access-secret", "refresh_token": "refresh-secret"}
    encrypted = vault.encrypt("alice", data)
    assert "access-secret" not in encrypted and "refresh-secret" not in encrypted
    assert vault.encrypt("alice", data) != encrypted
    assert vault.decrypt("alice", encrypted) == data
    with pytest.raises(AuthStoreError):
        vault.decrypt("bob", encrypted)
    damaged = json.loads(encrypted)
    damaged["ciphertext"] = base64.b64encode(b"tampered").decode()
    with pytest.raises(AuthStoreError):
        vault.decrypt("alice", json.dumps(damaged))


def test_kms_envelope_uses_context_and_handles_large_tokens(monkeypatch):
    kms = Mock()
    key = b"a" * 32
    kms.generate_data_key.return_value = {
        "Plaintext": key,
        "CiphertextBlob": b"wrapped-key",
    }
    kms.decrypt.return_value = {"Plaintext": key}
    vault = CredentialVault("arn:kms:key")
    monkeypatch.setattr(vault, "_kms", lambda: kms)
    data = {"token": "x" * 8000}
    encrypted = vault.encrypt("alice", data)
    assert vault.decrypt("alice", encrypted) == data
    assert kms.generate_data_key.call_args.kwargs["EncryptionContext"] == {
        "app": "mini-mailroom",
        "user_id": "alice",
    }
    assert kms.decrypt.call_args.kwargs["KeyId"] == "arn:kms:key"
    kms.decrypt.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "secret"}}, "Decrypt"
    )
    with pytest.raises(AuthStoreError, match="Credential decryption unavailable"):
        vault.decrypt("alice", encrypted)
