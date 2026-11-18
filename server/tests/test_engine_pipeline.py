"""最小引擎编排测试：Build 创建、索引与 RRF 检索。"""

from app.adapters.keyword.bm25 import Bm25KeywordAdapter
from app.adapters.vector.chroma import ChromaVectorAdapter
from app.contracts.embedding import EmbeddingModelDescriptor
from app.contracts.types import AdapterHealth, AdapterStatus
from app.control.db import ControlPlane
from app.engine.pipeline import ChunkInput, EnginePipeline


class FakeEmbedder:
    descriptor = EmbeddingModelDescriptor(
        provider="fake",
        model="fake-2d",
        revision="1",
        dimension=2,
        runtime="test",
    )

    def embed_documents(self, texts):
        return [[1.0, 0.0] if "空压机" in text else [0.0, 1.0] for text in texts]

    def embed_query(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def health(self):
        return AdapterHealth(status=AdapterStatus.OK)


def test_create_index_and_search(tmp_path):
    plane = ControlPlane(tmp_path / "control.db")
    kb = plane.create_kb("测试库", kb_id="kb_test")
    plane.register_document(kb, "doc-a", "hash-a", "key-a", "a.pdf")

    engine = EnginePipeline(
        plane,
        FakeEmbedder(),
        ChromaVectorAdapter(tmp_path / "chroma"),
        Bm25KeywordAdapter(tmp_path / "bm25"),
    )
    build = engine.create_build(kb)
    assert plane.get_build(build)["embedding_fingerprint"]

    engine.index_chunks(
        build,
        "doc-a",
        [
            ChunkInput("c1", "空压机故障代码 E-05"),
            ChunkInput("c2", "储气罐压力说明"),
        ],
    )

    hits = engine.search(build, "空压机", top_k=2)
    assert hits
    assert hits[0].chunk_id == "c1"
    assert plane.get_chunks(build, ["c1"])[0]["text"].startswith("空压机")

    other = engine.create_build(kb)
    assert engine.search(other, "空压机", top_k=2) == []
    plane.close()
