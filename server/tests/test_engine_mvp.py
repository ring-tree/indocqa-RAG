"""P7 MVP 缺口：增量入库、替换/撤销、失败策略与构建可复现。"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.adapters.keyword.bm25 import Bm25KeywordAdapter
from app.adapters.object_store.local import LocalObjectStore
from app.adapters.vector.chroma import ChromaVectorAdapter
from app.api.v1 import router as v1_router
from app.contracts.embedding import EmbeddingModelDescriptor
from app.contracts.types import AdapterHealth, AdapterStatus
from app.control.db import ControlPlane
from app.core.exceptions import BuildBlockedError
from app.engine import ingest as engine_ingest
from app.engine.ingest import (
    ingest_corpus,
    ingest_upload,
    replace_document,
    undo_document,
)
from app.engine.pipeline import EnginePipeline
from app.main import create_app


class FakeEmbedder:
    def __init__(self, model: str = "fake-2d"):
        self.descriptor = EmbeddingModelDescriptor(
            provider="fake",
            model=model,
            revision="1",
            dimension=2,
            runtime="test",
        )

    def embed_documents(self, texts):
        return [[1.0, 0.0] if "智能网关" in text else [0.0, 1.0] for text in texts]

    def embed_query(self, texts):
        return [[1.0, 0.0] if "智能网关" in text else [0.0, 1.0] for text in texts]

    def health(self):
        return AdapterHealth(status=AdapterStatus.OK)


def _runtime(tmp_path):
    base = tmp_path / "engine"
    plane = ControlPlane(base / "control.db")
    engine = EnginePipeline(
        plane,
        FakeEmbedder(),
        ChromaVectorAdapter(base / "chroma"),
        Bm25KeywordAdapter(base / "bm25"),
    )
    return SimpleNamespace(
        plane=plane,
        engine=engine,
        object_store=LocalObjectStore(base / "objects"),
        pack_registry=None,
    )


def _active_build(runtime):
    kb = runtime.plane.create_kb("MVP 测试库", kb_id="kb_mvp")
    build = runtime.engine.create_build(kb)
    runtime.plane.update_build_status(build, "ready", health_status="ok")
    runtime.plane.activate_build(kb, build)
    return kb, build


def test_incremental_upload_and_search(tmp_path):
    runtime = _runtime(tmp_path)
    kb, build = _active_build(runtime)

    result = ingest_upload(
        runtime,
        kb,
        build,
        "faq.md",
        "# 维护\n\n智能网关滤芯更换周期为 4000 小时。\n".encode(),
    )

    assert result["corpusRevision"] == 1
    assert result["chunks"] >= 1
    hits = runtime.engine.search(build, "智能网关", top_k=3)
    assert hits and hits[0].chunk_id


def test_replace_and_undo_document(tmp_path):
    runtime = _runtime(tmp_path)
    kb, build = _active_build(runtime)
    first = ingest_upload(
        runtime,
        kb,
        build,
        "a.md",
        "# 维护\n\n智能网关滤芯更换周期为 4000 小时。\n".encode(),
    )
    doc_id = first["docId"]

    replaced = replace_document(
        runtime,
        kb,
        build,
        doc_id,
        "a.md",
        "# 维护\n\n缓存模块排水阀每周检查一次。\n".encode(),
    )
    assert replaced["undoId"]
    assert runtime.engine.search(build, "缓存模块", top_k=3)

    undone = undo_document(runtime, kb, build, doc_id)
    assert undone["chunks"] >= 1
    assert runtime.engine.search(build, "智能网关", top_k=3)


def test_partial_failure_marks_warning(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    kb = runtime.plane.create_kb("部分失败库", kb_id="kb_partial")
    build = runtime.engine.create_build(kb)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "good.md").write_text(
        "# 维护\n\n智能网关滤芯更换周期为 4000 小时。\n", encoding="utf-8"
    )
    (corpus / "bad.md").write_text("# 坏文档\n\n无法解析\n", encoding="utf-8")

    original = engine_ingest.load_document

    def flaky(path):
        if path.name == "bad.md":
            raise RuntimeError("解析失败")
        return original(path)

    monkeypatch.setattr(engine_ingest, "load_document", flaky)
    stats = ingest_corpus(runtime, kb, build, corpus)

    assert stats.documents == 1
    assert stats.failed and stats.failed[0]["file"] == "bad.md"
    row = runtime.plane.get_build(build)
    assert row["status"] == "ready"
    assert row["health_status"] == "warning"


def test_zero_chunks_blocks_build(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    kb = runtime.plane.create_kb("空分块库", kb_id="kb_blocked")
    build = runtime.engine.create_build(kb)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "empty.md").write_text("# 空\n", encoding="utf-8")
    monkeypatch.setattr(engine_ingest, "build_chunks", lambda doc: ([], []))

    with pytest.raises(BuildBlockedError):
        ingest_corpus(runtime, kb, build, corpus)

    assert runtime.plane.get_build(build)["status"] == "blocked"


def test_same_corpus_builds_are_reproducible(tmp_path):
    runtime = _runtime(tmp_path)
    kb = runtime.plane.create_kb("可复现库", kb_id="kb_repro")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text(
        "# 维护\n\n智能网关滤芯更换周期为 4000 小时。\n", encoding="utf-8"
    )
    first = runtime.engine.create_build(kb)
    second = runtime.engine.create_build(kb)
    ingest_corpus(runtime, kb, first, corpus)
    ingest_corpus(runtime, kb, second, corpus)

    left = runtime.engine.search(first, "智能网关", top_k=3)
    right = runtime.engine.search(second, "智能网关", top_k=3)
    assert [hit.chunk_id for hit in left] == [hit.chunk_id for hit in right]
    assert [round(hit.score, 6) for hit in left] == [round(hit.score, 6) for hit in right]


def test_incremental_and_replace_api(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    monkeypatch.setattr(v1_router, "get_runtime", lambda: runtime)
    client = TestClient(create_app())
    assert client.post(
        "/api/v1/kbs", json={"name": "MVP API 库", "kbId": "kb_mvp_api"}
    ).status_code == 201
    build = client.post(
        "/api/v1/kbs/kb_mvp_api/builds", json={"preset": "balanced@1"}
    ).json()["data"]["buildId"]
    assert client.post(f"/api/v1/builds/{build}/activate").status_code == 200
    actions = [
        row["action"]
        for row in runtime.plane._conn.execute(
            "SELECT action FROM audit_log WHERE target_id=?", (build,)
        )
    ]
    assert "build.activate" in actions

    uploaded = client.post(
        "/api/v1/kbs/kb_mvp_api/documents/ingest",
        files={"file": ("faq.md", "# 维护\n\n智能网关滤芯更换周期为 4000 小时。\n", "text/markdown")},
    )
    assert uploaded.status_code == 201
    doc_id = uploaded.json()["data"]["docId"]
    assert uploaded.json()["data"]["corpusRevision"] == 1

    replaced = client.post(
        f"/api/v1/kbs/kb_mvp_api/documents/{doc_id}/replace",
        files={"file": ("faq.md", "# 维护\n\n缓存模块排水阀每周检查一次。\n", "text/markdown")},
    )
    assert replaced.status_code == 200
    assert replaced.json()["data"]["undoId"]

    undone = client.post(
        f"/api/v1/kbs/kb_mvp_api/documents/{doc_id}/undo", json={"buildId": build}
    )
    assert undone.status_code == 200
    assert undone.json()["data"]["chunks"] >= 1
