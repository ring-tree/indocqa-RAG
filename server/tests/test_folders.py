"""多级目录：路径校验、带目录入库、范围检索、移动/重命名/删除、存量迁移。"""

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.indexing import store
from app.indexing.folders import FolderPathError, migrate_folders, normalize_folder
from app.indexing.ingest import ingest_file
from app.indexing.manifest import Manifest
from app.main import create_app

CORPUS = pathlib.Path(__file__).resolve().parents[2] / "data" / "corpus"
FIXTURE = CORPUS / "fixture_术语混写_示例手册.md"


@pytest.fixture()
def tmp_settings(tmp_path, monkeypatch):
    terms = tmp_path / "terms"
    terms.mkdir()
    monkeypatch.setenv("INDOCQA_CHROMA_DIR", str(tmp_path / "chroma"))
    monkeypatch.setenv("INDOCQA_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("INDOCQA_SESSION_DB", str(tmp_path / "sessions.db"))
    monkeypatch.setenv("INDOCQA_EMBED_BACKEND", "local")
    monkeypatch.setenv("INDOCQA_TERMS_FILE", str(terms / "terms.json"))
    get_settings.cache_clear()
    store._client.cache_clear()
    yield get_settings()
    get_settings.cache_clear()
    store._client.cache_clear()


def _md(path: pathlib.Path, title: str, body: str) -> pathlib.Path:
    path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
    return path


def test_normalize_folder_rules():
    assert normalize_folder(" 售后支持 / 退换货 ") == "售后支持/退换货"
    assert normalize_folder(None) == "未分类"
    assert normalize_folder("", default=None) == ""
    with pytest.raises(FolderPathError):
        normalize_folder("设备/../秘密")
    with pytest.raises(FolderPathError):
        normalize_folder("a/b/c/d/e/f/g")


def test_ingest_with_folder_and_scoped_search(tmp_settings, tmp_path):
    from app.retrieval.searcher import search

    a = _md(tmp_path / "a.md", "智能网关维护", "FLT 更换周期为 4000 小时，使用 46 号滤芯。")
    b = _md(tmp_path / "b.md", "可靠性指标", "MTBF 平均无故障时间为 8000 小时。")
    ingest_file(a, tmp_settings, folder="售后支持/退换货")
    ingest_file(b, tmp_settings, folder="物流配送/发货与签收")

    manifest = Manifest(Manifest.path_for(tmp_settings))
    folders = manifest.folders()
    assert "售后支持" in folders and "物流配送/发货与签收" in folders
    assert all(info.get("folder") for info in manifest.documents().values())

    scoped = search("MTBF 是什么", top_k=6, settings=tmp_settings, folder="物流配送")
    assert scoped and all(h.folder == "物流配送/发货与签收" for h in scoped)

    other = search("MTBF 是什么", top_k=6, settings=tmp_settings, folder="售后支持")
    assert other and all(h.folder == "售后支持/退换货" for h in other)


def test_move_and_rename_folder(tmp_settings, tmp_path):
    import asyncio

    from app.api.kb import DocumentMove, FolderRename, move_document, rename_folder

    doc = _md(tmp_path / "a.md", "维护", "FLT 更换周期 4000 小时。")
    result = ingest_file(doc, tmp_settings, folder="售后支持/退换货")
    doc_id = result["docId"]

    asyncio.run(move_document(doc_id, DocumentMove(folder="物流配送/转运中心")))
    manifest = Manifest(Manifest.path_for(tmp_settings))
    assert manifest.get(doc_id)["folder"] == "物流配送/转运中心"

    body = FolderRename(**{"from": "物流配送/转运中心", "to": "保修服务/维修"})
    asyncio.run(rename_folder(body))
    manifest = Manifest(Manifest.path_for(tmp_settings))
    assert manifest.get(doc_id)["folder"] == "保修服务/维修"

    hits = store.query(tmp_settings, "FLT 更换周期", top_k=5, folder="保修服务")
    assert hits and all(h["folder"] == "保修服务/维修" for h in hits)


def test_delete_folder_move_mode(tmp_settings, tmp_path):
    import asyncio

    from app.api.kb import delete_folder

    doc = _md(tmp_path / "a.md", "维护", "FLT 更换周期 4000 小时。")
    result = ingest_file(doc, tmp_settings, folder="临时目录/子目录")
    asyncio.run(delete_folder("临时目录/子目录", mode="move"))
    manifest = Manifest(Manifest.path_for(tmp_settings))
    assert manifest.get(result["docId"])["folder"] == "临时目录"


def test_migration_is_idempotent(tmp_settings):
    from app.core.runtime_overrides import RuntimeOverrides, clear_overrides, set_overrides

    result = ingest_file(FIXTURE, tmp_settings)
    manifest = Manifest(Manifest.path_for(tmp_settings))
    manifest.data["documents"][result["docId"]].pop("folder", None)
    manifest.save()

    set_overrides(
        RuntimeOverrides(
            folder_migration={"fixture": "评测夹具", "术语混写": "评测夹具"},
            folder_default="未分类",
        )
    )
    try:
        first = migrate_folders(tmp_settings)
        assert first["migrated"] == 1
        manifest = Manifest(Manifest.path_for(tmp_settings))
        assert manifest.get(result["docId"])["folder"] == "评测夹具"

        second = migrate_folders(tmp_settings)
        assert second["migrated"] == 0
    finally:
        clear_overrides()


def test_gbk_markdown_loader(tmp_path):
    from app.parsing.loader import load_document

    path = tmp_path / "gbk.md"
    path.write_bytes("# 售后支持\n\n滤芯更换周期为 4000 小时。\n".encode("gb18030"))
    doc = load_document(path)
    assert "滤芯更换周期" in "\n".join(p.text for p in doc.paragraphs)


def test_chitchat_skips_retrieval(tmp_settings, tmp_path):
    import asyncio

    from app.graph.graph import run_retrieval

    doc = _md(tmp_path / "a.md", "维护", "FLT 更换周期 4000 小时。")
    ingest_file(doc, tmp_settings, folder="售后支持")
    result = asyncio.run(run_retrieval("你好", "adaptive", tmp_settings))
    assert result["hits"] == []
    retrieval_steps = [s for s in result["trace"] if s["stage"] == "retrieve"]
    assert retrieval_steps and retrieval_steps[0]["hits"] == 0


def test_folder_api_crud(tmp_settings, tmp_path):
    with open(FIXTURE, "rb") as f:
        response = TestClient(create_app()).post(
            "/api/kb/ingest",
            files={"file": (FIXTURE.name, f, "text/markdown")},
            data={"folder": "售后支持/退换货"},
        )
    assert response.status_code == 200
    assert response.json()["data"]["folder"] == "售后支持/退换货"

    client = TestClient(create_app())
    folders = client.get("/api/kb/folders").json()["data"]
    assert any(item["path"] == "售后支持/退换货" for item in folders)

    created = client.post("/api/kb/folders", json={"path": "物流配送/转运中心"}).json()["data"]
    assert created["path"] == "物流配送/转运中心"

    docs = client.get("/api/kb/documents", params={"folder": "售后支持"}).json()["data"]
    assert docs and docs[0]["folder"].startswith("售后支持/")

    doc_id = docs[0]["docId"]
    moved = client.post(f"/api/kb/documents/{doc_id}/move", json={"folder": "物流配送/转运中心"})
    assert moved.status_code == 200

    renamed = client.patch("/api/kb/folders", json={"from": "物流配送/转运中心", "to": "保修服务/换新"})
    assert renamed.status_code == 200
    docs = client.get("/api/kb/documents", params={"folder": "保修服务"}).json()["data"]
    assert any(d["docId"] == doc_id for d in docs)

    deleted = client.delete("/api/kb/folders", params={"path": "保修服务/换新", "mode": "move"})
    assert deleted.status_code == 200
    docs = client.get("/api/kb/documents").json()["data"]
    assert any(d["docId"] == doc_id and d["folder"] == "保修服务" for d in docs)


def test_manifest_folder_tree_survives_reload(tmp_settings, tmp_path):
    doc = _md(tmp_path / "a.md", "维护", "FLT 更换周期 4000 小时。")
    ingest_file(doc, tmp_settings, folder="售后支持/换货")
    raw = json.loads(Manifest(Manifest.path_for(tmp_settings)).path.read_text(encoding="utf-8"))
    assert "售后支持/换货" in raw["folders"]
    assert "售后支持" in raw["folders"]
