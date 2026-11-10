"""Check app assembly independently of the process working directory."""

from fastapi.testclient import TestClient

from app import application
from app.auth.store import MemoryAuthStore
from app.config import Settings


def test_static_frontend_and_api_precedence_from_another_directory(
    tmp_path, monkeypatch
):
    frontend = tmp_path / "client" / "dist"
    frontend.mkdir(parents=True)
    (frontend / "index.html").write_text('<div id="root">Built frontend</div>')
    (frontend / "asset.js").write_text("console.log('frontend')")
    monkeypatch.setattr(application, "FRONTEND_DIST", frontend)
    monkeypatch.chdir(tmp_path)

    app = application.create_app(Settings(_env_file=None), store=MemoryAuthStore())
    with TestClient(app) as client:
        assert "Built frontend" in client.get("/").text
        assert client.get("/asset.js").status_code == 200
        assert client.get("/health").json() == {"status": "ok"}
        account = client.get("/auth/me")
        assert account.json() == {"user": None}
        assert account.headers["Cache-Control"] == "no-store"
        assert client.get("/api/mail/messages").status_code == 401


def test_unbuilt_frontend_does_not_block_api_startup(tmp_path, monkeypatch):
    monkeypatch.setattr(application, "FRONTEND_DIST", tmp_path / "missing")
    app = application.create_app(Settings(_env_file=None), store=MemoryAuthStore())
    with TestClient(app) as client:
        assert client.get("/").status_code == 404
        assert client.get("/health").status_code == 200
        assert client.get("/auth/me").json() == {"user": None}
