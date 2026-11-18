"""Chroma 向量适配器集成测试（临时目录，不使用嵌入模型）。"""

import pytest

from app.adapters.vector.chroma import ChromaVectorAdapter
from app.contracts.types import (
    DimensionMismatchError,
    Filters,
    Namespace,
    VectorRecord,
)


@pytest.fixture()
def adapter(tmp_path):
    return ChromaVectorAdapter(tmp_path / "chroma")


def test_namespace_search_filter_delete_and_drop(adapter):
    ns = Namespace(kb_id="kb_test", build_id="bld_1")
    adapter.ensure_namespace(ns, 2)
    adapter.upsert(
        ns,
        [
            VectorRecord("c1", (1.0, 0.0), {"doc_id": "d1", "kind": "text", "page": 1}),
            VectorRecord("c2", (0.0, 1.0), {"doc_id": "d2", "kind": "text", "page": 2}),
        ],
    )

    hits = adapter.search(ns, [1.0, 0.0], 2)
    assert [hit.chunk_id for hit in hits] == ["c1", "c2"]
    assert hits[0].score > hits[1].score

    filtered = adapter.search(ns, [1.0, 0.0], 2, Filters(doc_ids=("d2",)))
    assert [hit.chunk_id for hit in filtered] == ["c2"]

    stats = adapter.stats(ns)
    assert stats.count == 2
    assert stats.dimension == 2

    adapter.delete(ns, ["c1"])
    assert adapter.stats(ns).count == 1
    assert adapter.list_chunk_ids(ns) == ["c2"]

    adapter.drop(ns)
    assert adapter.stats(ns).count == 0


def test_other_build_is_isolated(adapter):
    ns_a = Namespace(kb_id="kb_test", build_id="bld_a")
    ns_b = Namespace(kb_id="kb_test", build_id="bld_b")
    adapter.ensure_namespace(ns_a, 2)
    adapter.ensure_namespace(ns_b, 2)
    adapter.upsert(ns_a, [VectorRecord("a1", (1.0, 0.0), {"doc_id": "d1"})])
    assert adapter.stats(ns_a).count == 1
    assert adapter.stats(ns_b).count == 0


def test_dimension_mismatch_is_rejected(adapter):
    ns = Namespace(kb_id="kb_test", build_id="bld_1")
    adapter.ensure_namespace(ns, 2)
    with pytest.raises(DimensionMismatchError):
        adapter.ensure_namespace(ns, 3)
