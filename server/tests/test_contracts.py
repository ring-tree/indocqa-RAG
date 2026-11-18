"""契约层测试：值对象、指纹、预设、过滤器与能力协商。"""

import pytest

from app.contracts.embedding import EmbeddingModelDescriptor
from app.contracts.retrieval import (
    effective_threshold,
    effective_top_k,
    normalize_preset,
)
from app.contracts.types import (
    Filters,
    Namespace,
    VectorCapabilities,
    VectorRecord,
)


def _descriptor(**overrides) -> EmbeddingModelDescriptor:
    base = {
        "provider": "ollama",
        "model": "bge-m3",
        "revision": "latest",
        "dimension": 1024,
        "runtime": "ollama-0.5",
        "normalize": True,
        "query_prefix": "",
        "doc_prefix": "",
    }
    base.update(overrides)
    return EmbeddingModelDescriptor(**base)


def test_namespace_names_are_build_scoped():
    ns = Namespace(tenant_id="default", kb_id="kb_demo", build_id="bld_01")
    assert ns.vector_name == "t_default_kb_kb_demo_b_bld_01"
    assert ns.keyword_name == "kw_t_default_kb_kb_demo_b_bld_01"


def test_namespace_rejects_illegal_characters():
    with pytest.raises(ValueError):
        Namespace(kb_id="bad id")


def test_fingerprint_is_stable_and_runtime_sensitive():
    assert _descriptor().fingerprint() == _descriptor().fingerprint()
    assert _descriptor().fingerprint() != _descriptor(runtime="ollama-0.6").fingerprint()
    assert _descriptor().fingerprint() != _descriptor(dimension=768).fingerprint()
    assert _descriptor().fingerprint() != _descriptor(doc_prefix="passage:").fingerprint()


def test_presets_only_adjust_budget_and_threshold():
    assert normalize_preset("precision").id == "precision@1"
    assert normalize_preset("balanced").id == "balanced@1"
    assert normalize_preset(None).id == "balanced@1"
    with pytest.raises(ValueError):
        normalize_preset("unknown")

    assert effective_top_k(6, "precision@1") == 4
    assert effective_top_k(6, "balanced@1") == 6
    assert effective_top_k(6, "recall@1") == 9
    assert effective_top_k(20, "recall@1") == 20
    assert effective_top_k(6, "recall@1", max_top_k=8) == 8

    assert effective_threshold(0.50, "balanced@1") == 0.50
    assert effective_threshold(0.79, "precision@1") == 0.80
    assert effective_threshold(0.21, "recall@1") == 0.20


def test_filters_match_minimal_metadata():
    filters = Filters(doc_ids=("doc-1",), kinds=("text",))
    assert filters.matches({"doc_id": "doc-1", "kind": "text"})
    assert not filters.matches({"doc_id": "doc-2", "kind": "text"})
    assert not filters.matches({"doc_id": "doc-1", "kind": "table"})
    assert Filters().is_empty()


def test_vector_record_requires_content():
    with pytest.raises(ValueError):
        VectorRecord(chunk_id="c1", vector=())


def test_capabilities_report_missing_required():
    caps = VectorCapabilities(namespace_isolation=False, persistent=False)
    assert caps.missing_required() == ["namespace_isolation", "persistent"]
