"""Domain Pack 加载、注册、diff 与 Build 快照测试。"""

import json

import pytest

from app.contracts.pack import DomainPack
from app.control.db import ControlPlane
from app.packs.diff import diff_packs
from app.packs.loader import (
    PackIncompatible,
    PackInvalid,
    PackLoader,
    version_satisfies,
)
from app.packs.registry import PackInUseError, PackRegistry


def _write_pack(root, pack_id="demo-pack", version="1.0.0", **overrides):
    path = root / pack_id / version
    path.mkdir(parents=True)
    manifest = {
        "id": pack_id,
        "version": version,
        "domain": "demo",
        "languages": ["zh"],
        "engine_range": overrides.get("engine_range", ">=0.1,<1.0"),
        "description": overrides.get("description", "演示包"),
    }
    (path / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (path / "intent.json").write_text(
        json.dumps(overrides.get("intent", {"keywords": {"term": ["空压机"]}}), ensure_ascii=False),
        encoding="utf-8",
    )
    (path / "ui.json").write_text(
        json.dumps(overrides.get("ui", {"subtitle": "演示"}), ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_version_range():
    assert version_satisfies("0.1.0", ">=0.1,<1.0")
    assert not version_satisfies("1.0.0", ">=0.1,<1.0")
    assert version_satisfies("0.1.0", "==0.1.0")
    with pytest.raises(PackInvalid):
        version_satisfies("0.1.0", "latest")


def test_discover_and_load(tmp_path):
    _write_pack(tmp_path)
    loader = PackLoader(tmp_path, engine_version="0.1.0")
    items = loader.discover()
    assert len(items) == 1 and items[0]["valid"] is True
    loaded = loader.load("demo-pack", "1.0.0")
    assert loaded.pack.manifest.id == "demo-pack"
    assert loaded.content_hash
    assert "manifest.json" in loaded.files


def test_rejects_executable_and_oversize(tmp_path):
    path = _write_pack(tmp_path)
    (path / "evil.py").write_text("print('x')", encoding="utf-8")
    loader = PackLoader(tmp_path, engine_version="0.1.0")
    with pytest.raises(PackInvalid):
        loader.load_path(path)

    (path / "evil.py").unlink()
    (path / "big.bin").write_bytes(b"x" * 128)
    small = PackLoader(tmp_path, engine_version="0.1.0", max_file_bytes=64)
    with pytest.raises(PackInvalid):
        small.load_path(path)


def test_rejects_invalid_regex_html_and_range(tmp_path):
    bad_regex = _write_pack(tmp_path, pack_id="bad-regex")
    (bad_regex / "classification.json").write_text(
        json.dumps({"filename_rules": [{"pattern": "([", "folder": "x"}]}), encoding="utf-8"
    )
    loader = PackLoader(tmp_path, engine_version="0.1.0")
    with pytest.raises(PackInvalid):
        loader.load_path(bad_regex)

    bad_ui = _write_pack(tmp_path, pack_id="bad-ui", ui={"subtitle": "<script>x</script>"})
    with pytest.raises(PackInvalid):
        loader.load_path(bad_ui)

    bad_range = _write_pack(tmp_path, pack_id="bad-range", engine_range=">=9.0")
    with pytest.raises(PackIncompatible):
        loader.load_path(bad_range)


def test_registry_install_bind_uninstall_and_immutability(tmp_path):
    _write_pack(tmp_path)
    plane = ControlPlane(tmp_path / "control.db")
    loader = PackLoader(tmp_path, engine_version="0.1.0")
    registry = PackRegistry(plane, loader, engine_version="0.1.0")

    registry.install("demo-pack", "1.0.0")
    registry.install("demo-pack", "1.0.0")
    assert len(registry.list_installed()) == 1

    plane.create_kb("测试库", kb_id="kb_test")
    registry.bind_kb("kb_test", "demo-pack", "1.0.0")
    kb = plane.get_kb("kb_test")
    assert kb["domain_pack_id"] == "demo-pack"
    assert kb["domain_pack_version"] == "1.0.0"

    with pytest.raises(PackInUseError):
        registry.uninstall("demo-pack", "1.0.0")

    registry.unbind_kb("kb_test")
    registry.uninstall("demo-pack", "1.0.0")
    assert registry.list_installed() == []
    plane.close()


def test_same_version_content_is_immutable(tmp_path):
    path = _write_pack(tmp_path)
    plane = ControlPlane(tmp_path / "control.db")
    registry = PackRegistry(
        plane, PackLoader(tmp_path, engine_version="0.1.0"), engine_version="0.1.0"
    )
    registry.install("demo-pack", "1.0.0")

    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    manifest["description"] = "被修改"
    (path / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PackInvalid):
        registry.install("demo-pack", "1.0.0")
    plane.close()


def test_diff_and_build_snapshot(tmp_path):
    _write_pack(tmp_path)
    loader = PackLoader(tmp_path, engine_version="0.1.0")
    old = loader.load("demo-pack", "1.0.0")
    new_data = old.pack.model_dump()
    new_data["intent"]["keywords"]["term"] = ["空压机", "储气罐"]
    new_data["ui"]["subtitle"] = "新副标题"
    new_data["folders"]["default"] = "通用"
    new = DomainPack.model_validate(new_data)

    diff = diff_packs(old.pack, new)
    assert diff.requires_new_build is True
    assert diff.rebuild_mode is not None
    assert diff.rebuild_mode.value == "config-only"
    assert "ui" in diff.profiles
    assert diff.ingest_policy_changed is True

    plane = ControlPlane(tmp_path / "control.db")
    kb = plane.create_kb("测试库", kb_id="kb_test")
    build = plane.create_build(
        kb,
        domain_pack_id="demo-pack",
        domain_pack_version="1.0.0",
        pack_snapshot_json=old.pack.model_dump_json(),
    )
    snapshot = plane.get_pack_snapshot(build)
    assert snapshot and "demo-pack" in snapshot
    plane.close()


def test_schema_has_pack_columns(tmp_path):
    plane = ControlPlane(tmp_path / "control.db")
    kb_columns = {
        row["name"] for row in plane.connection.execute("PRAGMA table_info(knowledge_bases)")
    }
    build_columns = {
        row["name"] for row in plane.connection.execute("PRAGMA table_info(builds)")
    }
    assert "domain_pack_version" in kb_columns
    assert "pack_snapshot_json" in build_columns
    plane.close()


def test_builtin_industrial_pack_loads():
    from app.core.config import SERVER_ROOT

    loader = PackLoader(SERVER_ROOT / "packs", engine_version="0.1.0")
    loaded = loader.load("industrial-compressor", "1.0.0")
    assert loaded.pack.manifest.domain == "industrial"
    assert loaded.pack.manifest.languages == ["zh"]


def test_builtin_customer_service_pack_loads():
    from app.core.config import SERVER_ROOT

    loader = PackLoader(SERVER_ROOT / "packs", engine_version="0.1.0")
    loaded = loader.load("customer-service-faq", "1.0.0")
    assert loaded.pack.manifest.domain == "customer-service"
    assert loaded.pack.manifest.languages == ["zh"]
    assert "7天无理由" in loaded.pack.terms.clusters
    assert loaded.pack.ui.subtitle
    assert loaded.pack.evaluation.dataset == "eval/dataset-support.jsonl"
