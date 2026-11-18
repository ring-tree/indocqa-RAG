"""API v1 并行入口：错误包、配置、Build 与旧链路兼容。"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.engine.runtime import reset_runtime
from app.main import create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("INDOCQA_ENGINE_DATA_DIR", str(tmp_path / "engine"))
    monkeypatch.setenv("INDOCQA_SESSION_DB", str(tmp_path / "sessions.db"))
    get_settings.cache_clear()
    reset_runtime()
    yield TestClient(create_app())
    reset_runtime()
    get_settings.cache_clear()


def test_v1_kb_config_build_flow(client):
    created = client.post(
        "/api/v1/kbs", json={"name": "API 测试库", "kbId": "kb_api_test"}
    )
    assert created.status_code == 201
    assert created.json()["data"]["kbId"] == "kb_api_test"

    schemas = client.get("/api/v1/config/schemas").json()["data"]
    assert set(schemas) == {"engine", "kb", "answer", "ui", "pack"}

    config = client.get("/api/v1/kbs/kb_api_test/config").json()["data"]
    assert config["config"]["preset"] == "balanced@1"
    assert config["sources"]["preset"] == "kb"

    patched = client.patch(
        "/api/v1/kbs/kb_api_test/config", json={"preset": "recall"}
    ).json()["data"]
    assert patched["version"] == 1
    assert patched["impact"]["requiresNewBuild"] is True
    assert patched["impact"]["rebuildMode"] == "config-only"

    invalid = client.post(
        "/api/v1/kbs/kb_api_test/config/validate", json={"preset": "unknown"}
    ).json()["data"]
    assert invalid["valid"] is False

    build = client.post(
        "/api/v1/kbs/kb_api_test/builds", json={"preset": "balanced"}
    ).json()["data"]
    build_id = build["buildId"]
    assert build["status"] == "draft"

    activated = client.post(f"/api/v1/builds/{build_id}/activate").json()["data"]
    assert activated["status"] == "active"
    again = client.post(f"/api/v1/builds/{build_id}/activate").json()["data"]
    assert again["noop"] is True
    kb = client.get("/api/v1/kbs/kb_api_test").json()["data"]
    assert kb["activeBuildId"] == build_id


def test_v1_error_envelope_has_name_and_request_id(client):
    response = client.get("/api/v1/kbs/missing")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == 40402
    assert error["name"] == "kb_resource_not_found"
    assert error["retryable"] is False
    assert error["requestId"]

    missing_build = client.post("/api/v1/builds/missing/activate")
    assert missing_build.status_code == 404
    assert missing_build.json()["error"]["name"] == "build_not_found"


def test_legacy_api_keeps_envelope_and_health(client):
    legacy = client.post("/api/chat", json={})
    assert legacy.status_code == 400
    error = legacy.json()["error"]
    assert error["code"] == 40001
    assert error["name"] == "param_invalid"
    assert "message" in error and "detail" in error

    assert client.get("/api/health").status_code == 200
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/health").headers["x-api-version"] == "1"
