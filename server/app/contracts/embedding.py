"""嵌入适配器契约。

指纹必须覆盖 provider / runtime / model / revision / dimension / normalize / 前缀，
用于判断两个 Build 是否处于同一向量空间（D22）。仅更换部署副本、指纹相同才允许复用向量。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Protocol, runtime_checkable

from app.contracts.types import AdapterHealth


@dataclass(frozen=True)
class EmbeddingModelDescriptor:
    provider: str
    model: str
    revision: str
    dimension: int
    runtime: str = ""
    normalize: bool = True
    query_prefix: str = ""
    doc_prefix: str = ""

    def __post_init__(self) -> None:
        if not self.provider or not self.model or not self.revision:
            raise ValueError("provider / model / revision 不能为空")
        if self.dimension <= 0:
            raise ValueError("dimension 必须为正整数")

    def payload(self) -> dict[str, object]:
        data = asdict(self)
        return {key: data[key] for key in sorted(data)}

    def fingerprint(self) -> str:
        canonical = json.dumps(self.payload(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@runtime_checkable
class EmbeddingAdapter(Protocol):
    @property
    def descriptor(self) -> EmbeddingModelDescriptor: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, texts: Sequence[str]) -> list[list[float]]: ...

    def health(self) -> AdapterHealth: ...
