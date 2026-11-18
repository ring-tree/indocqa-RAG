"""Pack 管理 API：安装、绑定、Build 快照、diff 与卸载。"""

import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.engine.runtime import get_runtime, reset_runtime
from app.main import create_app


def _write_pack(root, version="1.0.0", **overrides):
    path = root / "demo-pack" / version
    path.mkdir(parents=True)
    manifest = {
        "id": "demo-pack",
        "version": version,
        "domain": "demo",
        "languages": ["zh"],
        "engine_range": ">=0.1,<1.0",
    }
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (path / "intent.json").write_text(
        json.dumps(overrides.get("intent", {"keywords": {"term": ["智能网关"]}}), ensure_ascii=False),
        encoding="utf-8",
    )
    (path / "ui.json").write_text(
        json.dumps(overrides.get("ui", {"subtitle": "演示"}), ensure_ascii=False),
        encoding="utf-8",
    )


@pytest.fixture()
def client(tmp_path, monkeypatch):
    packs = tmp_path / "packs"
    _write_pack(packs)
    _write_pack(
        packs,
        "1.1.0",
        intent={"keywords": {"term": ["智能网关", "缓存模块"]}},
        ui={"subtitle": "新版"},
    )
    monkeypatch.setenv("INDOCQA_PACKS_DIR", str(packs))
    monkeypatch.setenv("INDOCQA_ENGINE_DATA_DIR", str(tmp_path / "engine"))
    monkeypatch.setenv("INDOCQA_SESSION_DB", str(tmp_path / "sessions.db"))
    get_settings.cache_clear()
    reset_runtime()
    yield TestClient(create_app())
    reset_runtime()
    get_settings.cache_clear()


def test_pack_install_bind_build_and_uninstall(client):
    listing = client.get("/api/v1/packs").json()["data"]
    assert len(listing["discovered"]) == 2
    assert listing["installed"] == []

    installed = client.post(
        "/api/v1/packs/install", json={"packId": "demo-pack", "version": "1.0.0"}
    )
    assert installed.status_code == 200
    assert installed.json()["data"]["id"] == "demo-pack"

    client.post("/api/v1/kbs", json={"name": "Pack 测试库", "kbId": "kb_pack_test"})
    bound = client.post(
        "/api/v1/kbs/kb_pack_test/pack",
        json={"packId": "demo-pack", "version": "1.0.0"},
    )
    assert bound.status_code == 200
    pack = client.get("/api/v1/kbs/kb_pack_test/pack").json()["data"]
    assert pack["version"] == "1.0.0"

    build = client.post(
        "/api/v1/kbs/kb_pack_test/builds",
        json={"preset": "balanced", "domainPackId": "demo-pack", "domainPackVersion": "1.0.0"},
    )
    assert build.status_code == 202
    build_id = build.json()["data"]["buildId"]
    snapshot = get_runtime().plane.get_pack_snapshot(build_id)
    assert snapshot and "demo-pack" in snapshot

    diff = client.get(
        "/api/v1/packs/diff",
        params={"packId": "demo-pack", "fromVersion": "1.0.0", "toVersion": "1.1.0"},
    ).json()["data"]
    assert diff["requiresNewBuild"] is True
    assert diff["rebuildMode"] == "config-only"
    assert "ui" in diff["profiles"]

    blocked = client.post(
        "/api/v1/packs/uninstall", json={"packId": "demo-pack", "version": "1.0.0"}
    )
    assert blocked.status_code == 400

    client.delete("/api/v1/kbs/kb_pack_test/pack")
    removed = client.post(
        "/api/v1/packs/uninstall", json={"packId": "demo-pack", "version": "1.0.0"}
    )
    assert removed.status_code == 200
    assert client.get("/api/v1/packs").json()["data"]["installed"] == []


def test_pack_errors_are_reported(client):
    missing = client.post(
        "/api/v1/packs/install", json={"packId": "missing", "version": "1.0.0"}
    )
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == 40001
