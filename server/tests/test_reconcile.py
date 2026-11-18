"""Outbox 对账测试：孤儿清理与缺失补队。"""

from app.adapters.keyword.bm25 import Bm25KeywordAdapter
from app.adapters.vector.chroma import ChromaVectorAdapter
from app.contracts.types import KeywordRecord, Namespace, VectorRecord
from app.control.db import ControlPlane
from app.control.reconcile import reconcile_build


def test_reconcile_removes_orphans_and_enqueues_missing(tmp_path):
    plane = ControlPlane(tmp_path / "control.db")
    kb = plane.create_kb("测试库", kb_id="kb_test")
    build = plane.create_build(kb)
    plane.register_document(kb, "doc-a", "hash-a", "key-a", "a.pdf")
    plane.add_chunks(
        build,
        "doc-a",
        [{"chunk_id": "c1", "text": "a"}, {"chunk_id": "c2", "text": "b"}],
    )

    namespace = Namespace(kb_id="kb_test", build_id=build)
    vector = ChromaVectorAdapter(tmp_path / "chroma")
    vector.ensure_namespace(namespace, 2)
    vector.upsert(
        namespace,
        [
            VectorRecord("c1", (1.0, 0.0), {"doc_id": "doc-a"}),
            VectorRecord("c9", (1.0, 0.0), {"doc_id": "doc-a"}),
        ],
    )
    keyword = Bm25KeywordAdapter(tmp_path / "bm25")
    keyword.ensure_namespace(namespace, "zh-CN")
    keyword.upsert(namespace, [KeywordRecord("c1", "智能网关", {"doc_id": "doc-a"})])

    report = reconcile_build(plane, build, vector, keyword)

    assert report.control_chunks == 2
    assert report.orphan_deleted == 1
    assert report.missing_enqueued == 1
    assert "c9" not in vector.list_chunk_ids(namespace)
    assert plane.pending_task_count(build) == 1
    plane.close()
