"""最小引擎编排：Build 创建、分块索引与 RRF 混合检索。

P2 只负责骨架与接口装配，不迁移现有解析/生成逻辑；P4 再由工业 Pack 接入。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.contracts.embedding import EmbeddingAdapter
from app.contracts.index import KeywordIndexAdapter, VectorIndexAdapter
from app.contracts.retrieval import effective_top_k, normalize_preset
from app.contracts.types import (
    KeywordRecord,
    Namespace,
    VectorRecord,
)
from app.control.db import ControlPlane
from app.engine.model_registry import ModelRegistry

RRF_K = 60
EXPAND = 3


@dataclass(frozen=True)
class ChunkInput:
    chunk_id: str
    text: str
    section: str = ""
    page: int | None = None
    kind: str = "text"
    table_no: str | None = None
    parent_id: str | None = None


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    score: float
    metadata: dict = field(default_factory=dict)


class EnginePipeline:
    def __init__(
        self,
        plane: ControlPlane,
        embedder: EmbeddingAdapter,
        vector_index: VectorIndexAdapter,
        keyword_index: KeywordIndexAdapter,
    ):
        self.plane = plane
        self.embedder = embedder
        self.vector_index = vector_index
        self.keyword_index = keyword_index
        self.registry = ModelRegistry(plane)

    def _namespace(self, build: dict) -> Namespace:
        kb = self.plane.get_kb(build["kb_id"])
        if kb is None:
            raise KeyError(f"知识库不存在: {build['kb_id']}")
        return Namespace(
            tenant_id=kb["tenant_id"], kb_id=build["kb_id"], build_id=build["id"]
        )

    def create_build(
        self,
        kb_id: str,
        *,
        preset: str = "balanced@1",
        language_profile: str = "zh-CN",
        pipeline_version: str = "",
        domain_pack_id: str = "",
        domain_pack_version: str = "",
    ) -> str:
        descriptor = self.embedder.descriptor
        model_id = self.registry.register(descriptor)
        self.registry.require_verified(model_id)
        build_id = self.plane.create_build(
            kb_id,
            pipeline_version=pipeline_version,
            embedding_fingerprint=descriptor.fingerprint(),
            domain_pack_id=domain_pack_id,
            domain_pack_version=domain_pack_version,
            language_profile=language_profile,
            retrieval_preset=normalize_preset(preset).id,
        )
        build = self.plane.get_build(build_id)
        namespace = self._namespace(build)
        self.vector_index.ensure_namespace(namespace, descriptor.dimension)
        self.keyword_index.ensure_namespace(namespace, language_profile)
        return build_id

    def index_chunks(self, build_id: str, doc_id: str, chunks: list[ChunkInput]) -> int:
        build = self.plane.get_build(build_id)
        if build is None:
            raise KeyError(f"Build 不存在: {build_id}")
        namespace = self._namespace(build)
        payload = [
            {
                "chunk_id": chunk.chunk_id,
                "parent_id": chunk.parent_id,
                "text": chunk.text,
                "section": chunk.section,
                "page": chunk.page,
                "kind": chunk.kind,
                "table_no": chunk.table_no,
            }
            for chunk in chunks
        ]
        self.plane.add_chunks(build_id, doc_id, payload)
        vectors = self.embedder.embed_documents([chunk.text for chunk in chunks])
        self.vector_index.upsert(
            namespace,
            [
                VectorRecord(
                    chunk_id=chunk.chunk_id,
                    vector=tuple(vector),
                    metadata={
                        "doc_id": doc_id,
                        "kind": chunk.kind,
                        "page": chunk.page,
                        "table_no": chunk.table_no,
                    },
                )
                for chunk, vector in zip(chunks, vectors)
            ],
        )
        self.keyword_index.upsert(
            namespace,
            [
                KeywordRecord(
                    chunk_id=chunk.chunk_id,
                    text=chunk.text,
                    metadata={"doc_id": doc_id, "kind": chunk.kind},
                )
                for chunk in chunks
            ],
        )
        return len(chunks)

    def search(
        self,
        build_id: str,
        query: str,
        *,
        top_k: int = 6,
        preset: str | None = None,
    ) -> list[SearchHit]:
        build = self.plane.get_build(build_id)
        if build is None:
            raise KeyError(f"Build 不存在: {build_id}")
        namespace = self._namespace(build)
        spec = normalize_preset(preset or build["retrieval_preset"])
        budget = effective_top_k(top_k, spec)
        vector = self.embedder.embed_query([query])[0]
        vector_hits = self.vector_index.search(namespace, vector, budget * EXPAND)
        keyword_hits = self.keyword_index.search(namespace, query, budget * EXPAND)

        scores: dict[str, float] = {}
        metadata: dict[str, dict] = {}
        for rank, hit in enumerate(vector_hits, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (RRF_K + rank)
            metadata.setdefault(hit.chunk_id, hit.metadata)
        for rank, hit in enumerate(keyword_hits, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (RRF_K + rank)
            metadata.setdefault(hit.chunk_id, hit.metadata)

        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:budget]
        return [
            SearchHit(chunk_id=chunk_id, score=score, metadata=metadata.get(chunk_id, {}))
            for chunk_id, score in ranked
        ]
