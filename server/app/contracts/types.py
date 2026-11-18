"""跨层共享的值对象与类型别名。

约束：
- 只依赖标准库；
- 不导入 engine、plugins 或任何具体适配器；
- 所有适配器使用同一套 ID、命名空间与能力语义。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeAlias

TenantId: TypeAlias = str
KBId: TypeAlias = str
BuildId: TypeAlias = str
DocId: TypeAlias = str
ChunkId: TypeAlias = str

DEFAULT_TENANT = "default"
DEFAULT_KB = "kb_default"
DEFAULT_BUILD = "bld_default"

Metadata: TypeAlias = dict[str, str | int | float | bool | None]


class ContractError(Exception):
    """契约层错误的基类；API 层负责映射为业务错误码。"""


class CapabilityError(ContractError):
    """适配器缺少执行该操作所必需的能力。"""


class DimensionMismatchError(ContractError):
    """向量维度与命名空间声明不一致。"""


class NamespaceMismatchError(ContractError):
    """命名空间与当前 Build 不匹配。"""


class AdapterStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass(frozen=True)
class AdapterHealth:
    status: AdapterStatus
    message: str = ""


@dataclass(frozen=True)
class Namespace:
    """Build 级不可变命名空间。"""

    tenant_id: str = DEFAULT_TENANT
    kb_id: str = DEFAULT_KB
    build_id: str = DEFAULT_BUILD

    def __post_init__(self) -> None:
        for name, value in (
            ("tenant_id", self.tenant_id),
            ("kb_id", self.kb_id),
            ("build_id", self.build_id),
        ):
            if not value or not value.replace("_", "").replace("-", "").isalnum():
                raise ValueError(f"{name} 只能包含字母、数字、下划线与连字符: {value!r}")

    @property
    def vector_name(self) -> str:
        return f"t_{self.tenant_id}_kb_{self.kb_id}_b_{self.build_id}"

    @property
    def keyword_name(self) -> str:
        return f"kw_t_{self.tenant_id}_kb_{self.kb_id}_b_{self.build_id}"


@dataclass(frozen=True)
class Filters:
    """索引侧允许的最小过滤字段；目录过滤由控制面解析为 doc_id 集合。"""

    doc_ids: tuple[DocId, ...] | None = None
    kinds: tuple[str, ...] | None = None
    page: int | None = None
    table_no: str | None = None

    def is_empty(self) -> bool:
        return not any((self.doc_ids, self.kinds, self.page is not None, self.table_no))

    def matches(self, metadata: Metadata) -> bool:
        if self.doc_ids is not None and metadata.get("doc_id") not in self.doc_ids:
            return False
        if self.kinds is not None and metadata.get("kind") not in self.kinds:
            return False
        if self.page is not None and metadata.get("page") != self.page:
            return False
        return self.table_no is None or metadata.get("table_no") == self.table_no


@dataclass(frozen=True)
class VectorRecord:
    chunk_id: ChunkId
    vector: tuple[float, ...]
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.chunk_id:
            raise ValueError("chunk_id 不能为空")
        if not self.vector:
            raise ValueError("vector 不能为空")


@dataclass(frozen=True)
class VectorHit:
    chunk_id: ChunkId
    score: float
    metadata: Metadata = field(default_factory=dict)


@dataclass(frozen=True)
class KeywordRecord:
    chunk_id: ChunkId
    text: str
    metadata: Metadata = field(default_factory=dict)


@dataclass(frozen=True)
class KeywordHit:
    chunk_id: ChunkId
    score: float
    metadata: Metadata = field(default_factory=dict)


@dataclass(frozen=True)
class NamespaceStats:
    count: int
    dimension: int | None = None


@dataclass(frozen=True)
class ObjectRef:
    key: str
    size: int
    content_type: str | None = None
    etag: str | None = None


@dataclass(frozen=True)
class ObjectStat:
    key: str
    size: int
    content_type: str | None = None
    modified_at: str | None = None


@dataclass(frozen=True)
class VectorCapabilities:
    """向量索引适配器能力清单；缺少必需项时引擎拒绝创建 Build。"""

    namespace_isolation: bool = True
    dimension_check: bool = True
    upsert: bool = True
    delete_by_chunk: bool = True
    search: bool = True
    stats: bool = True
    drop: bool = True
    persistent: bool = True
    doc_id_filter: bool = True
    bulk_upsert: bool = False
    delete_by_doc: bool = False
    list_ids: bool = False
    max_top_k: int | None = None
    metric: str = "cosine"

    def missing_required(self) -> list[str]:
        required = {
            "namespace_isolation": self.namespace_isolation,
            "dimension_check": self.dimension_check,
            "upsert": self.upsert,
            "delete_by_chunk": self.delete_by_chunk,
            "search": self.search,
            "stats": self.stats,
            "drop": self.drop,
            "persistent": self.persistent,
            "doc_id_filter": self.doc_id_filter,
        }
        return [name for name, enabled in required.items() if not enabled]


@dataclass(frozen=True)
class KeywordCapabilities:
    namespace_isolation: bool = True
    upsert: bool = True
    delete_by_chunk: bool = True
    search: bool = True
    stats: bool = True
    drop: bool = True
    persistent: bool = True
    doc_id_filter: bool = True
    list_ids: bool = False
    max_top_k: int | None = None

    def missing_required(self) -> list[str]:
        required = {
            "namespace_isolation": self.namespace_isolation,
            "upsert": self.upsert,
            "delete_by_chunk": self.delete_by_chunk,
            "search": self.search,
            "stats": self.stats,
            "drop": self.drop,
            "persistent": self.persistent,
            "doc_id_filter": self.doc_id_filter,
        }
        return [name for name, enabled in required.items() if not enabled]


@dataclass(frozen=True)
class ObjectStoreCapabilities:
    put: bool = True
    get: bool = True
    stream: bool = False
    stat: bool = True
    delete: bool = True
    signed_url: bool = False

    def missing_required(self) -> list[str]:
        required = {"put": self.put, "get": self.get, "stat": self.stat, "delete": self.delete}
        return [name for name, enabled in required.items() if not enabled]


AnyMetadata: TypeAlias = dict[str, Any]
