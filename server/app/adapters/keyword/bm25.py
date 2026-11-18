"""BM25 关键词索引适配器：每个 Build 一个 JSON 侧车。"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path

import jieba
from rank_bm25 import BM25Okapi

from app.contracts.types import (
    AdapterHealth,
    AdapterStatus,
    ChunkId,
    Filters,
    KeywordCapabilities,
    KeywordHit,
    KeywordRecord,
    Namespace,
    NamespaceStats,
)

_TOKEN = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]+")


def _tokenize(text: str) -> list[str]:
    return [
        token.lower()
        for token in jieba.lcut(text)
        if token.strip() and _TOKEN.search(token)
    ]


class _NamespaceData:
    def __init__(self, language_profile: str = "zh-CN"):
        self.language_profile = language_profile
        self.docs: dict[str, dict] = {}
        self.bm25: BM25Okapi | None = None
        self.order: list[str] = []
        self.tokens: dict[str, set[str]] = {}

    def rebuild(self) -> None:
        self.order = list(self.docs)
        corpus = [_tokenize(self.docs[chunk_id]["text"]) for chunk_id in self.order]
        self.tokens = {
            chunk_id: set(tokens) for chunk_id, tokens in zip(self.order, corpus)
        }
        self.bm25 = BM25Okapi(corpus) if corpus else None


class Bm25KeywordAdapter:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, _NamespaceData] = {}

    def capabilities(self) -> KeywordCapabilities:
        return KeywordCapabilities(list_ids=True)

    def _path(self, namespace: Namespace) -> Path:
        return self.root / f"{namespace.keyword_name}.json"

    def _load(self, namespace: Namespace) -> _NamespaceData:
        if namespace.keyword_name in self._cache:
            return self._cache[namespace.keyword_name]
        path = self._path(namespace)
        data = _NamespaceData()
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            data.language_profile = payload.get("language_profile", "zh-CN")
            data.docs = payload.get("docs", {})
            data.rebuild()
        self._cache[namespace.keyword_name] = data
        return data

    def _persist(self, namespace: Namespace, data: _NamespaceData) -> None:
        path = self._path(namespace)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "language_profile": data.language_profile,
            "docs": data.docs,
        }
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)

    def ensure_namespace(self, namespace: Namespace, language_profile: str) -> None:
        data = self._load(namespace)
        if data.language_profile != language_profile:
            data.language_profile = language_profile
            self._persist(namespace, data)

    def upsert(self, namespace: Namespace, records: Sequence[KeywordRecord]) -> None:
        data = self._load(namespace)
        for record in records:
            metadata = {
                key: value for key, value in record.metadata.items() if value is not None
            }
            data.docs[record.chunk_id] = {"text": record.text, "metadata": metadata}
        data.rebuild()
        self._persist(namespace, data)

    def delete(self, namespace: Namespace, chunk_ids: Sequence[ChunkId]) -> None:
        data = self._load(namespace)
        for chunk_id in chunk_ids:
            data.docs.pop(chunk_id, None)
        data.rebuild()
        self._persist(namespace, data)

    def search(
        self,
        namespace: Namespace,
        query: str,
        top_k: int,
        filters: Filters | None = None,
    ) -> list[KeywordHit]:
        data = self._load(namespace)
        tokens = _tokenize(query)
        if data.bm25 is None or not tokens or top_k <= 0:
            return []
        scores = data.bm25.get_scores(tokens)
        ranked = sorted(zip(data.order, scores), key=lambda item: item[1], reverse=True)
        query_tokens = set(tokens)
        hits: list[KeywordHit] = []
        for chunk_id, score in ranked:
            # 小语料下 IDF 可能为 0 或负；用词项交集判断是否真正命中
            if not (query_tokens & data.tokens.get(chunk_id, set())):
                continue
            metadata = data.docs[chunk_id].get("metadata", {})
            if filters is not None and not filters.matches(metadata):
                continue
            hits.append(KeywordHit(chunk_id=chunk_id, score=float(score), metadata=metadata))
            if len(hits) >= top_k:
                break
        return hits

    def stats(self, namespace: Namespace) -> NamespaceStats:
        return NamespaceStats(count=len(self._load(namespace).docs))

    def list_chunk_ids(self, namespace: Namespace) -> list[ChunkId]:
        return list(self._load(namespace).docs)

    def drop(self, namespace: Namespace) -> None:
        self._cache.pop(namespace.keyword_name, None)
        self._path(namespace).unlink(missing_ok=True)

    def health(self) -> AdapterHealth:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            probe = self.root / ".health"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return AdapterHealth(status=AdapterStatus.OK)
        except OSError as exc:
            return AdapterHealth(status=AdapterStatus.DOWN, message=str(exc))
