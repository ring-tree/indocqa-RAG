"""BM25 关键词适配器测试。"""

import pytest

from app.adapters.keyword.bm25 import Bm25KeywordAdapter
from app.contracts.types import Filters, KeywordRecord, Namespace


@pytest.fixture()
def namespace():
    return Namespace(kb_id="kb_test", build_id="bld_1")


@pytest.fixture()
def adapter(tmp_path):
    return Bm25KeywordAdapter(tmp_path / "bm25")


def test_search_filter_delete_and_persist(adapter, namespace):
    adapter.ensure_namespace(namespace, "zh-CN")
    adapter.upsert(
        namespace,
        [
            KeywordRecord("c1", "空压机故障代码 E-05 处理方法", {"doc_id": "d1"}),
            KeywordRecord("c2", "储气罐压力与容量说明", {"doc_id": "d2"}),
        ],
    )

    hits = adapter.search(namespace, "空压机 故障", 2)
    assert hits
    assert hits[0].chunk_id == "c1"

    filtered = adapter.search(namespace, "空压机 故障", 2, Filters(doc_ids=("d2",)))
    assert filtered == []

    reloaded = Bm25KeywordAdapter(adapter.root)
    assert reloaded.search(namespace, "空压机", 2)[0].chunk_id == "c1"

    adapter.delete(namespace, ["c1"])
    assert adapter.stats(namespace).count == 1
    assert adapter.list_chunk_ids(namespace) == ["c2"]


def test_drop_removes_namespace(adapter, namespace):
    adapter.ensure_namespace(namespace, "zh-CN")
    adapter.upsert(namespace, [KeywordRecord("c1", "空压机", {"doc_id": "d1"})])
    adapter.drop(namespace)
    assert adapter.stats(namespace).count == 0
    assert adapter.search(namespace, "空压机", 1) == []
