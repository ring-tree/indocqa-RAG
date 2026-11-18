"""引擎运行时组合根：按部署配置装配控制面与适配器。

新引擎只服务 `/api/v1`，与旧 `/api` 链路并行；数据目录独立。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.adapters.embedding.http import OllamaEmbeddingAdapter
from app.adapters.keyword.bm25 import Bm25KeywordAdapter
from app.adapters.object_store.local import LocalObjectStore
from app.adapters.vector.chroma import ChromaVectorAdapter
from app.contracts.embedding import EmbeddingAdapter
from app.control.db import ControlPlane
from app.core.config import Settings, get_settings
from app.engine.pipeline import EnginePipeline
from app.packs.loader import PackLoader
from app.packs.registry import PackRegistry


@dataclass
class EngineRuntime:
    settings: Settings
    plane: ControlPlane
    embedder: EmbeddingAdapter
    vector_index: ChromaVectorAdapter
    keyword_index: Bm25KeywordAdapter
    object_store: LocalObjectStore
    engine: EnginePipeline
    pack_loader: PackLoader
    pack_registry: PackRegistry


@lru_cache
def get_runtime() -> EngineRuntime:
    settings = get_settings()
    base = Path(settings.engine_data_dir)
    base.mkdir(parents=True, exist_ok=True)
    plane = ControlPlane(base / "control.db")
    embedder = OllamaEmbeddingAdapter(
        settings.ollama_base_url,
        settings.ollama_embed_model,
        settings.engine_embed_dimension,
        revision="latest",
        runtime="ollama",
    )
    vector_index = ChromaVectorAdapter(base / "chroma")
    keyword_index = Bm25KeywordAdapter(base / "bm25")
    object_store = LocalObjectStore(base / "objects")
    engine = EnginePipeline(plane, embedder, vector_index, keyword_index)
    pack_loader = PackLoader(settings.packs_dir, engine_version=settings.version)
    pack_registry = PackRegistry(
        plane, pack_loader, engine_version=settings.version
    )
    return EngineRuntime(
        settings=settings,
        plane=plane,
        embedder=embedder,
        vector_index=vector_index,
        keyword_index=keyword_index,
        object_store=object_store,
        engine=engine,
        pack_loader=pack_loader,
        pack_registry=pack_registry,
    )


def reset_runtime() -> None:
    get_runtime.cache_clear()
