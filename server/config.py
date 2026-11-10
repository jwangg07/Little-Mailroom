"""Backend settings loaded from server/.env and environment variables."""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).with_name(".env"), extra="ignore"
    )
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"
    frontend_url: str = "http://localhost:5173"
    cookie_secure: bool = False
    dynamodb_table: str = ""
    google_client_secret_arn: str = ""
    auth_store: Literal["dynamodb", "memory"] = "dynamodb"
    credential_kms_key_id: str = ""
    credential_encryption_key: SecretStr = SecretStr("")
