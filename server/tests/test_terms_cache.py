"""ISSUE-07：术语缓存记录 Pack 哈希，Pack 升级/移除时自动失效重建。"""

import json
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.core.runtime_overrides import RuntimeOverrides, clear_overrides, set_overrides
from app.retrieval import terms


@pytest.fixture()
def cache_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("INDOCQA_TERMS_FILE", str(tmp_path / "terms.json"))
    monkeypatch.setenv("INDOCQA_CHROMA_DIR", str(tmp_path / "chroma"))
    get_settings.cache_clear()
    monkeypatch.setattr(terms, "_extract_pairs", lambda settings: [])
    monkeypatch.setattr(terms, "apply_terms_to_index", lambda settings, clusters: None)
    monkeypatch.setattr(terms, "reset_terms_tags", lambda settings: 0)
    yield get_settings()
    clear_overrides()
    get_settings.cache_clear()


def _cached(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_cache_invalidates_on_pack_hash_change(cache_settings):
    set_overrides(
        RuntimeOverrides(
            pack_id="demo",
            pack_version="1.0.0",
            pack_hash="hash-a",
            terms_clusters={"旧术语": ["旧别名"]},
        )
    )
    first = terms.load_clusters(cache_settings)
    assert any(set(cluster) == {"旧术语", "旧别名"} for cluster in first)
    assert _cached(cache_settings.terms_file)["pack"]["hash"] == "hash-a"

    set_overrides(
        RuntimeOverrides(
            pack_id="demo",
            pack_version="1.1.0",
            pack_hash="hash-b",
            terms_clusters={"新术语": ["新别名"]},
        )
    )
    second = terms.load_clusters(cache_settings)
    assert any(set(cluster) == {"新术语", "新别名"} for cluster in second)
    assert not any("旧术语" in cluster for cluster in second)
    assert _cached(cache_settings.terms_file)["pack"]["hash"] == "hash-b"


def test_legacy_cache_is_stamped_without_rebuild(cache_settings, monkeypatch):
    path = Path(cache_settings.terms_file)
    path.write_text(
        json.dumps({"clusters": [["旧术语", "旧别名"]]}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        terms, "build_clusters", lambda settings=None: pytest.fail("不应重建旧缓存")
    )
    set_overrides(
        RuntimeOverrides(
            pack_id="demo",
            pack_version="1.0.0",
            pack_hash="hash-a",
        )
    )
    clusters = terms.load_clusters(cache_settings)
    assert clusters == [["旧术语", "旧别名"]]
    assert _cached(cache_settings.terms_file)["pack"]["hash"] == "hash-a"


def test_cache_rebuilds_when_pack_removed(cache_settings):
    set_overrides(
        RuntimeOverrides(
            pack_id="demo",
            pack_version="1.0.0",
            pack_hash="hash-a",
            terms_clusters={"旧术语": ["旧别名"]},
        )
    )
    assert terms.load_clusters(cache_settings)
    clear_overrides()

    clusters = terms.load_clusters(cache_settings)
    assert clusters == []
    assert "pack" not in _cached(cache_settings.terms_file)


def test_overrides_from_pack_carries_content_hash():
    from app.core.config import SERVER_ROOT
    from app.packs.legacy_adapter import overrides_from_pack
    from app.packs.loader import PackLoader

    loaded = PackLoader(SERVER_ROOT / "packs", engine_version="0.1.0").load(
        "customer-service-faq", "1.0.0"
    )
    overrides = overrides_from_pack(loaded.pack, content_hash=loaded.content_hash)
    assert overrides.pack_id == "customer-service-faq"
    assert overrides.pack_version == "1.0.0"
    assert overrides.pack_hash == loaded.content_hash


def test_build_clusters_resets_index_before_rebuild(cache_settings, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        terms, "reset_terms_tags", lambda settings: calls.append("reset") or 0
    )
    monkeypatch.setattr(
        terms, "_extract_pairs", lambda settings: calls.append("extract") or []
    )
    monkeypatch.setattr(
        terms, "apply_terms_to_index", lambda settings, clusters: calls.append("apply")
    )
    terms.build_clusters(cache_settings, reset_index=True)
    assert calls[0] == "reset"


def test_load_clusters_uses_reset_on_invalidation(cache_settings, monkeypatch):
    path = Path(cache_settings.terms_file)
    path.write_text(
        json.dumps(
            {
                "clusters": [["旧术语", "旧别名"]],
                "pack": {"id": "demo", "version": "1.0.0", "hash": "hash-a"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls: list[dict] = []
    monkeypatch.setattr(
        terms,
        "build_clusters",
        lambda settings=None, **kwargs: calls.append(kwargs) or [],
    )
    set_overrides(
        RuntimeOverrides(
            pack_id="demo",
            pack_version="1.1.0",
            pack_hash="hash-b",
        )
    )
    terms.load_clusters(cache_settings)
    assert calls and calls[0].get("reset_index") is True


def test_reset_terms_tags_removes_stale_labels(tmp_path, monkeypatch):
    monkeypatch.setenv("INDOCQA_TERMS_FILE", str(tmp_path / "terms.json"))
    monkeypatch.setenv("INDOCQA_CHROMA_DIR", str(tmp_path / "chroma"))
    get_settings.cache_clear()
    settings = get_settings()
    path = tmp_path / "chroma" / "bm25_docs.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"c1": "维护\n智能网关 同义: 旧词"}, ensure_ascii=False),
        encoding="utf-8",
    )

    from app.indexing import bm25_index, store

    class FakeCollection:
        def get(self, include=None):
            return {
                "ids": ["c1"],
                "documents": ["智能网关 4000 小时"],
                "metadatas": [{"section": "维护"}],
            }

    monkeypatch.setattr(store, "_collection", lambda s: FakeCollection())
    bm25_index._bm25_cache.clear()
    try:
        assert terms.reset_terms_tags(settings) == 1
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["c1"] == "维护\n智能网关 4000 小时"
        assert "同义:" not in data["c1"]
    finally:
        bm25_index._bm25_cache.clear()
        get_settings.cache_clear()
