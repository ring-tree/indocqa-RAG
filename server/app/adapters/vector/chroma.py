"""Chroma 向量索引适配器：每个 Build 一个独立 collection。"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.contracts.types import (
    AdapterHealth,
    AdapterStatus,
    ChunkId,
    DimensionMismatchError,
    Filters,
    Namespace,
    NamespaceStats,
    VectorCapabilities,
    VectorHit,
    VectorRecord,
)


def _matches(metadata: dict | None) -> dict:
    if not metadata:
        return {}
    return {key: value for key, value in metadata.items() if value is not None}


def _where(filters: Filters | None) -> dict | None:
    if filters is None or filters.is_empty():
        return None
    clauses: list[dict] = []
    if filters.doc_ids is not None:
        clauses.append({"doc_id": {"$in": list(filters.doc_ids)}})
    if filters.kinds is not None:
        clauses.append({"kind": {"$in": list(filters.kinds)}})
    if filters.page is not None:
        clauses.append({"page": filters.page})
    if filters.table_no is not None:
        clauses.append({"table_no": filters.table_no})
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


class ChromaVectorAdapter:
    def __init__(self, path: str | Path, *, metric: str = "cosine"):
        self.path = str(path)
        self.metric = metric
        self._client = chromadb.PersistentClient(
            path=self.path, settings=ChromaSettings(anonymized_telemetry=False)
        )

    def capabilities(self) -> VectorCapabilities:
        return VectorCapabilities(bulk_upsert=True, list_ids=True, metric=self.metric)

    @staticmethod
    def _name(namespace: Namespace) -> str:
        name = namespace.vector_name
        if len(name) <= 63:
            return name
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
        return f"{name[:54]}_{digest}"

    def _collection(self, namespace: Namespace, dimension: int | None = None):
        metadata = {"hnsw:space": self.metric}
        if dimension is not None:
            metadata["dimension"] = dimension
        return self._client.get_or_create_collection(
            name=self._name(namespace),
            metadata=metadata,
        )

    def ensure_namespace(
        self, namespace: Namespace, dimension: int, metric: str = "cosine"
    ) -> None:
        collection = self._collection(namespace, dimension)
        metadata = collection.metadata or {}
        existing = metadata.get("dimension")
        if existing is not None and int(existing) != dimension:
            raise DimensionMismatchError(
                f"命名空间 {self._name(namespace)} 维度为 {existing}，期望 {dimension}"
            )
        if existing is None:
            # 旧集合补维度；不传入 hnsw:space，Chroma 不允许创建后修改距离函数
            collection.modify(metadata={"dimension": dimension})

    def upsert(self, namespace: Namespace, records: Sequence[VectorRecord]) -> None:
        if not records:
            return
        self._collection(namespace).upsert(
            ids=[record.chunk_id for record in records],
            embeddings=[list(record.vector) for record in records],
            metadatas=[_matches(record.metadata) for record in records],
        )

    def delete(self, namespace: Namespace, chunk_ids: Sequence[ChunkId]) -> None:
        if not chunk_ids:
            return
        self._collection(namespace).delete(ids=list(chunk_ids))

    def search(
        self,
        namespace: Namespace,
        vector: Sequence[float],
        top_k: int,
        filters: Filters | None = None,
    ) -> list[VectorHit]:
        collection = self._collection(namespace)
        total = collection.count()
        if total == 0 or top_k <= 0:
            return []
        result = collection.query(
            query_embeddings=[list(vector)],
            n_results=min(top_k, total),
            where=_where(filters),
            include=["metadatas", "distances"],
        )
        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        hits: list[VectorHit] = []
        for index, chunk_id in enumerate(ids):
            distance = float(distances[index]) if index < len(distances) else 1.0
            metadata = metadatas[index] if index < len(metadatas) else None
            hits.append(
                VectorHit(
                    chunk_id=chunk_id,
                    score=1.0 - distance,
                    metadata=_matches(metadata),
                )
            )
        return hits

    def stats(self, namespace: Namespace) -> NamespaceStats:
        collection = self._collection(namespace)
        dimension = (collection.metadata or {}).get("dimension")
        return NamespaceStats(
            count=collection.count(),
            dimension=int(dimension) if dimension is not None else None,
        )

    def list_chunk_ids(self, namespace: Namespace) -> list[ChunkId]:
        return list(self._collection(namespace).get(include=[])["ids"])

    def drop(self, namespace: Namespace) -> None:
        try:
            self._client.delete_collection(self._name(namespace))
        except Exception:  # noqa: BLE001 集合不存在时忽略
            return

    def health(self) -> AdapterHealth:
        try:
            self._client.heartbeat()
            return AdapterHealth(status=AdapterStatus.OK)
        except Exception as exc:  # noqa: BLE001 适配器健康检查不应抛出
            return AdapterHealth(status=AdapterStatus.DOWN, message=str(exc))
