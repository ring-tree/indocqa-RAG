"""向量索引与关键词索引适配器契约。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.contracts.types import (
    AdapterHealth,
    ChunkId,
    Filters,
    KeywordCapabilities,
    KeywordHit,
    KeywordRecord,
    Namespace,
    NamespaceStats,
    VectorCapabilities,
    VectorHit,
    VectorRecord,
)


@runtime_checkable
class VectorIndexAdapter(Protocol):
    def capabilities(self) -> VectorCapabilities: ...

    def ensure_namespace(self, namespace: Namespace, dimension: int, metric: str = "cosine") -> None: ...

    def upsert(self, namespace: Namespace, records: Sequence[VectorRecord]) -> None: ...

    def delete(self, namespace: Namespace, chunk_ids: Sequence[ChunkId]) -> None: ...

    def search(
        self,
        namespace: Namespace,
        vector: Sequence[float],
        top_k: int,
        filters: Filters | None = None,
    ) -> list[VectorHit]: ...

    def stats(self, namespace: Namespace) -> NamespaceStats: ...

    def list_chunk_ids(self, namespace: Namespace) -> list[ChunkId]: ...

    def drop(self, namespace: Namespace) -> None: ...

    def health(self) -> AdapterHealth: ...


@runtime_checkable
class KeywordIndexAdapter(Protocol):
    def capabilities(self) -> KeywordCapabilities: ...

    def ensure_namespace(self, namespace: Namespace, language_profile: str) -> None: ...

    def upsert(self, namespace: Namespace, records: Sequence[KeywordRecord]) -> None: ...

    def delete(self, namespace: Namespace, chunk_ids: Sequence[ChunkId]) -> None: ...

    def search(
        self,
        namespace: Namespace,
        query: str,
        top_k: int,
        filters: Filters | None = None,
    ) -> list[KeywordHit]: ...

    def stats(self, namespace: Namespace) -> NamespaceStats: ...

    def list_chunk_ids(self, namespace: Namespace) -> list[ChunkId]: ...

    def drop(self, namespace: Namespace) -> None: ...

    def health(self) -> AdapterHealth: ...
