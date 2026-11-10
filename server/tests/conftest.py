"""Keep unit tests independent of local and cloud runtime settings."""

import pytest

from app.config import Settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    # _env_file=None skips dotenv, but Pydantic still reads the process environment.
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
