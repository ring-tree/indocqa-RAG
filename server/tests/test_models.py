"""输出层模型清单与 profile 选择。"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.llm import LLMGateway
from app.main import create_app


def _set_primary(monkeypatch):
    monkeypatch.setenv("INDOCQA_LLM_BASE_URL", "http://primary.example/v1")
    monkeypatch.setenv("INDOCQA_LLM_API_KEY", "sk-test-primary")
    monkeypatch.setenv("INDOCQA_LLM_MODEL", "model-primary")


def _set_profile(monkeypatch, index: int, model: str, key: str | None = None):
    monkeypatch.setenv(f"INDOCQA_LLM_PROFILE_{index}_BASE_URL", f"http://profile{index}.example/v1")
    monkeypatch.setenv(f"INDOCQA_LLM_PROFILE_{index}_API_KEY", key or f"sk-test-profile{index}")
    monkeypatch.setenv(f"INDOCQA_LLM_PROFILE_{index}_MODEL", model)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("INDOCQA_SESSION_DB", str(tmp_path / "sessions.db"))
    _set_primary(monkeypatch)
    _set_profile(monkeypatch, 2, "model-profile2")
    _set_profile(monkeypatch, 3, "model-profile3")
    get_settings.cache_clear()
    yield TestClient(create_app())
    get_settings.cache_clear()


def test_models_endpoint_lists_profiles_without_secrets(client):
    data = client.get("/api/models").json()["data"]
    assert [p["id"] for p in data] == ["primary", "profile2", "profile3"]
    assert data[0]["model"] == "model-primary"
    body = str(data)
    assert "sk-test" not in body and "example" not in body


def test_gateway_profile_order(monkeypatch):
    _set_primary(monkeypatch)
    _set_profile(monkeypatch, 2, "model-profile2")
    _set_profile(monkeypatch, 3, "model-profile3")
    get_settings.cache_clear()
    try:
        gateway = LLMGateway(get_settings(), "profile3")
        assert [p["id"] for p in gateway._ordered()] == ["profile3", "primary", "profile2"]
    finally:
        get_settings.cache_clear()


def test_placeholder_profile_is_not_listed(tmp_path, monkeypatch):
    monkeypatch.setenv("INDOCQA_SESSION_DB", str(tmp_path / "sessions.db"))
    _set_primary(monkeypatch)
    _set_profile(monkeypatch, 2, "model-profile2", key="sk-your-key-here")
    get_settings.cache_clear()
    try:
        c = TestClient(create_app())
        data = c.get("/api/models").json()["data"]
        assert [p["id"] for p in data] == ["primary"]
    finally:
        get_settings.cache_clear()


def test_chat_rejects_unknown_profile(client):
    r = client.post(
        "/api/chat",
        json={"message": "你好", "mode": "direct", "profile": "nope"},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == 40001


def test_chat_rejects_removed_backup_profile(client):
    """备用供应商标识已移除，旧调用方必须改用 primary / profileN。"""
    r = client.post(
        "/api/chat",
        json={"message": "你好", "mode": "direct", "profile": "backup"},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == 40001


def test_models_endpoint_empty_when_not_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("INDOCQA_LLM_BASE_URL", "http://disabled.invalid/v1")
    monkeypatch.setenv("INDOCQA_LLM_API_KEY", "x")
    monkeypatch.setenv("INDOCQA_LLM_MODEL", "disabled")
    monkeypatch.setenv("INDOCQA_SESSION_DB", str(tmp_path / "sessions.db"))
    get_settings.cache_clear()
    try:
        c = TestClient(create_app())
        assert c.get("/api/models").json()["data"] == []
        r = c.post("/api/chat", json={"message": "你好", "mode": "direct"})
        assert r.status_code == 502 and r.json()["error"]["code"] == 42901
    finally:
        get_settings.cache_clear()
