"""ISSUE-06：Build 自动入库与 active Build 检索桥接。"""

import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.adapters.keyword.bm25 import Bm25KeywordAdapter
from app.adapters.object_store.local import LocalObjectStore
from app.adapters.vector.chroma import ChromaVectorAdapter
from app.api.v1 import router as v1_router
from app.contracts.embedding import EmbeddingModelDescriptor
from app.contracts.types import AdapterHealth, AdapterStatus
from app.control.db import ControlPlane
from app.core.config import get_settings
from app.engine import retrieval_bridge
from app.engine.ingest import ingest_corpus
from app.engine.pipeline import ChunkInput, EnginePipeline
from app.engine.runtime import reset_runtime
from app.graph import pipeline as graph_pipeline
from app.main import create_app
from app.retrieval.searcher import Hit


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
        return [[1.0, 0.0] for _ in texts]

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


def _seed_build(tmp_path):
    runtime = _runtime(tmp_path)
    kb = runtime.plane.create_kb("测试库", kb_id="kb_build")
    build = runtime.engine.create_build(kb)
    runtime.plane.register_document(kb, "doc-a", "hash-a", "key-a", "faq.md")
    runtime.engine.index_chunks(
        build,
        "doc-a",
        [ChunkInput("c1", "智能网关滤芯更换周期为 4000 小时", section="维护")],
    )
    runtime.plane.update_build_status(build, "ready", health_status="ok")
    runtime.plane.activate_build(kb, build)
    return runtime, kb, build


def test_build_embedding_model_switch(tmp_path):
    runtime = _runtime(tmp_path)
    requested: list[str] = []

    def factory(model: str) -> FakeEmbedder:
        requested.append(model)
        return FakeEmbedder(model)

    runtime.engine.embedder_factory = factory
    kb = runtime.plane.create_kb("模型切换库", kb_id="kb_models")
    first = runtime.engine.create_build(kb, embedding_model="fake-a")
    second = runtime.engine.create_build(kb, embedding_model="fake-b")

    assert requested == ["fake-a", "fake-b"]
    first_fp = runtime.plane.get_build(first)["embedding_fingerprint"]
    second_fp = runtime.plane.get_build(second)["embedding_fingerprint"]
    assert first_fp != second_fp
    assert runtime.engine.registry.get_by_fingerprint(first_fp)["model"] == "fake-a"
    assert runtime.engine.registry.get_by_fingerprint(second_fp)["model"] == "fake-b"


def test_ingest_corpus_builds_and_searches(tmp_path):
    runtime = _runtime(tmp_path)
    kb = runtime.plane.create_kb("测试库", kb_id="kb_ingest")
    build = runtime.engine.create_build(kb)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "faq.md").write_text(
        "# 维护\n\n智能网关滤芯更换周期为 4000 小时。\n", encoding="utf-8"
    )

    stats = ingest_corpus(runtime, kb, build, corpus, activate=True)

    assert stats.documents == 1
    assert stats.chunks >= 1
    assert runtime.plane.get_build(build)["status"] == "active"
    assert runtime.engine.search(build, "智能网关", top_k=3)


def test_build_api_auto_ingest(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    corpus_root = tmp_path / "data" / "corpus"
    corpus_root.mkdir(parents=True)
    (corpus_root / "faq.md").write_text(
        "# 维护\n\n智能网关滤芯更换周期为 4000 小时。\n", encoding="utf-8"
    )
    monkeypatch.setenv("INDOCQA_ENGINE_DATA_DIR", str(tmp_path / "engine"))
    monkeypatch.setenv("INDOCQA_CORPUS_DIR", str(corpus_root))
    monkeypatch.setenv("INDOCQA_SESSION_DB", str(tmp_path / "sessions.db"))
    get_settings.cache_clear()
    reset_runtime()
    monkeypatch.setattr(v1_router, "get_runtime", lambda: runtime)

    client = TestClient(create_app())
    assert client.post(
        "/api/v1/kbs", json={"name": "API 构建库", "kbId": "kb_api_build"}
    ).status_code == 201

    created = client.post(
        "/api/v1/kbs/kb_api_build/builds",
        json={"preset": "balanced@1", "corpusDir": "corpus", "activate": True},
    )
    assert created.status_code == 202
    data = created.json()["data"]
    assert data["status"] == "active"
    assert data["stats"]["documents"] == 1
    assert data["stats"]["chunks"] >= 1

    result = client.post(
        "/api/v1/search",
        json={"kbId": "kb_api_build", "query": "智能网关", "topK": 3},
    ).json()["data"]
    assert result["items"]


def test_retrieval_bridge_converts_hits(tmp_path, monkeypatch):
    runtime, kb, _build = _seed_build(tmp_path)
    monkeypatch.setattr(retrieval_bridge, "get_runtime", lambda: runtime)
    settings = get_settings()

    result = retrieval_bridge.retrieve_from_engine("智能网关", settings, kb, top_k=3)

    assert result["plan"]["strategy"] == "engine"
    assert result["hits"]
    assert result["hits"][0].doc_name == "faq.md"
    assert result["hits"][0].sim is None


def test_chat_uses_engine_hits(tmp_path, monkeypatch):
    runtime, kb, build = _seed_build(tmp_path)
    runtime.plane.update_build_status(build, "ready", health_status="ok")
    runtime.plane.activate_build(kb, build)
    monkeypatch.setenv("INDOCQA_ANSWER_BACKEND", "engine")
    monkeypatch.setenv("INDOCQA_SESSION_DB", str(tmp_path / "sessions.db"))
    get_settings.cache_clear()
    settings = get_settings()

    hit = Hit(
        chunk_id="c1",
        rrf=0.03,
        sim=None,
        text="智能网关滤芯更换周期为 4000 小时",
        doc_id="doc-a",
        doc_name="faq.md",
        section="维护",
        page=None,
        kind="text",
        table_no=None,
        parent_text="智能网关滤芯更换周期为 4000 小时",
        folder="",
    )

    def fake_retrieve(*args, **kwargs):
        return {
            "intent": "param",
            "hits": [hit],
            "trace": [{"stage": "retrieve", "strategy": "engine", "hits": 1}],
            "plan": {"skip_retrieval": False, "strategy": "engine"},
            "entities": [],
        }

    monkeypatch.setattr(graph_pipeline, "retrieve_from_engine", fake_retrieve)

    async def collect():
        return [
            event
            async for event in graph_pipeline.chat_stream(
                "智能网关 4000 小时", "kb", settings=settings, kb_id=kb
            )
        ]

    events = asyncio.run(collect())
    kinds = [event["event"] for event in events]
    assert "error" not in kinds
    sources = next(event for event in events if event["event"] == "sources")["data"]["items"]
    assert sources and sources[0]["docName"] == "faq.md"
    assert "done" in kinds
