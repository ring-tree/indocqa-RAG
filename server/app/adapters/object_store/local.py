"""本地文件系统对象存储适配器。

key 使用相对 POSIX 路径（如 `kb/{kb}/blobs/{hash[:2]}/{hash}`）；
写入采用临时文件 + 原子替换，避免半写文件被读取。
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from app.contracts.types import (
    AdapterHealth,
    AdapterStatus,
    ObjectRef,
    ObjectStat,
    ObjectStoreCapabilities,
)


class InvalidObjectKey(ValueError):
    """对象 key 非法（绝对路径、穿越、空段）。"""


class LocalObjectStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def capabilities(self) -> ObjectStoreCapabilities:
        return ObjectStoreCapabilities(stream=True, signed_url=False)

    def _path(self, key: str) -> Path:
        if not key or key.startswith("/") or "\\" in key:
            raise InvalidObjectKey(f"非法对象 key: {key!r}")
        parts = [part for part in key.split("/") if part]
        if any(part in {"", ".", ".."} for part in parts):
            raise InvalidObjectKey(f"非法对象 key: {key!r}")
        target = (self.root / Path(*parts)).resolve()
        if self.root != target and self.root not in target.parents:
            raise InvalidObjectKey(f"对象 key 越界: {key!r}")
        return target

    def put(
        self,
        key: str,
        data: bytes | BinaryIO,
        content_type: str | None = None,
    ) -> ObjectRef:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = data if isinstance(data, bytes) else data.read()
        digest = hashlib.sha256(payload).hexdigest()
        with self._lock:
            fd, temp_name = tempfile.mkstemp(prefix=".tmp-", dir=target.parent)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload)
                os.replace(temp_name, target)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
        return ObjectRef(key=key, size=len(payload), content_type=content_type, etag=digest)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def open(self, key: str) -> BinaryIO:
        return self._path(key).open("rb")

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def stat(self, key: str) -> ObjectStat:
        path = self._path(key)
        info = path.stat()
        return ObjectStat(
            key=key,
            size=info.st_size,
            modified_at=datetime.fromtimestamp(info.st_mtime, UTC).isoformat(
                timespec="seconds"
            ),
        )

    def delete(self, key: str) -> None:
        path = self._path(key)
        with self._lock:
            path.unlink(missing_ok=True)

    def delete_many(self, keys: Sequence[str]) -> None:
        for key in keys:
            self.delete(key)

    def signed_url(self, key: str, ttl_seconds: int) -> str | None:
        return None

    def health(self) -> AdapterHealth:
        probe = self.root / f".health-{uuid.uuid4().hex}"
        try:
            probe.write_bytes(b"ok")
            probe.unlink()
            return AdapterHealth(status=AdapterStatus.OK)
        except OSError as exc:
            return AdapterHealth(status=AdapterStatus.DOWN, message=str(exc))
