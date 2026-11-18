"""ISSUE-05：Pack 作用域按 KB 解析，UI Profile 与问答链路不再依赖进程级单 Pack。"""

from fastapi.testclient import TestClient

from app.api import chat as chat_api
from app.control.db import ControlPlane
from app.core.config import get_settings
from app.core.runtime_overrides import (
    RuntimeOverrides,
    clear_overrides,
    get_overrides,
    set_overrides,
    use_overrides,
)
from app.engine.runtime import reset_runtime
from app.main import create_app


def _prepare_control_plane(tmp_path):
    plane = ControlPlane(tmp_path / "engine" / "control.db")
    plane.create_kb(
        "示例库",
        kb_id="kb_demo",
        domain_pack_id="example-minimal",
        domain_pack_version="1.0.0",
    )
    plane.create_kb(
        "客服库",
        kb_id="kb_cs",
        domain_pack_id="customer-service-faq",
        domain_pack_version="1.0.0",
    )
    plane.close()


def test_use_overrides_scope_isolation():
    first = RuntimeOverrides(ui={"subtitle": "示例"})
    support = RuntimeOverrides(ui={"subtitle": "客服"})
    set_overrides(first)
    try:
        assert get_overrides() is first
        with use_overrides(support):
            assert get_overrides() is support
            with use_overrides(None):
                assert get_overrides() is None
        assert get_overrides() is first
    finally:
        clear_overrides()


def test_ui_profile_resolves_per_kb(tmp_path, monkeypatch):
    monkeypatch.setenv("INDOCQA_ENGINE_DATA_DIR", str(tmp_path / "engine"))
    monkeypatch.setenv("INDOCQA_SESSION_DB", str(tmp_path / "sessions.db"))
    get_settings.cache_clear()
    reset_runtime()
    _prepare_control_plane(tmp_path)
    try:
        client = TestClient(create_app())
        demo = client.get("/api/v1/ui-profile?kbId=kb_demo").json()["data"]
        support = client.get("/api/v1/ui-profile?kbId=kb_cs").json()["data"]
        assert demo["subtitle"] == "示例知识库"
        assert support["subtitle"] == "客服 FAQ 智能问答"
        assert support["folderExample"] == "售后支持/退换货"
        assert client.get("/api/v1/ui-profile?kbId=missing").status_code == 404
    finally:
        reset_runtime()
        get_settings.cache_clear()


def test_chat_applies_kb_pack_scope(monkeypatch):
    captured = {}
    support = RuntimeOverrides(ui={"subtitle": "客服"})

    async def fake_chat_stream(*args, **kwargs):
        captured["overrides"] = get_overrides()
        yield {
            "event": "done",
            "data": {
                "confidence": "high",
                "sessionId": None,
                "messageId": None,
                "boundary": None,
            },
        }

    monkeypatch.setattr(chat_api, "overrides_for_kb", lambda kb, settings: support)
    monkeypatch.setattr(chat_api, "active_build_id", lambda kb: None)
    monkeypatch.setattr(chat_api.pipeline, "kb_is_empty", lambda settings: False)
    monkeypatch.setattr(chat_api.pipeline, "chat_stream", fake_chat_stream)

    client = TestClient(create_app())
    response = client.post(
        "/api/chat", json={"message": "你好", "mode": "kb", "kbId": "kb_cs"}
    )
    assert response.status_code == 200
    assert captured["overrides"] is support
