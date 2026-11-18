"""对象存储适配器契约。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import BinaryIO, Protocol, runtime_checkable

from app.contracts.types import (
    AdapterHealth,
    ObjectRef,
    ObjectStat,
    ObjectStoreCapabilities,
)


@runtime_checkable
class ObjectStoreAdapter(Protocol):
    def capabilities(self) -> ObjectStoreCapabilities: ...

    def put(
        self,
        key: str,
        data: bytes | BinaryIO,
        content_type: str | None = None,
    ) -> ObjectRef: ...

    def get(self, key: str) -> bytes: ...

    def open(self, key: str) -> BinaryIO: ...

    def exists(self, key: str) -> bool: ...

    def stat(self, key: str) -> ObjectStat: ...

    def delete(self, key: str) -> None: ...

    def delete_many(self, keys: Sequence[str]) -> None: ...

    def signed_url(self, key: str, ttl_seconds: int) -> str | None: ...

    def health(self) -> AdapterHealth: ...
