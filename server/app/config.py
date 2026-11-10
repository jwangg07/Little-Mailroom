"""Backend settings loaded from server/.env and environment variables."""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIST = SERVER_ROOT.parent / "client" / "dist"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=SERVER_ROOT / ".env", extra="ignore")
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
