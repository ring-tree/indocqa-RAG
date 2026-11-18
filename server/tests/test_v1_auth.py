"""V10：/api/v1 Bearer 鉴权与健康例外。"""

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.engine.runtime import reset_runtime
from app.main import create_app


def test_v1_requires_bearer_token(monkeypatch):
    monkeypatch.setenv("INDOCQA_AUTH_TOKEN", "secret-token")
    get_settings.cache_clear()
    reset_runtime()
    try:
        client = TestClient(create_app())
        denied = client.get("/api/v1/kbs")
        assert denied.status_code == 401
        assert denied.json()["error"]["code"] == 40101
        assert denied.json()["error"]["name"] == "unauthenticated"

        allowed = client.get(
            "/api/v1/kbs", headers={"Authorization": "Bearer secret-token"}
        )
        assert allowed.status_code == 200

        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/health").status_code == 200
    finally:
        reset_runtime()
        get_settings.cache_clear()
