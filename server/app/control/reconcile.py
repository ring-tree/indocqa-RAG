"""Outbox 对账：清理索引孤儿，补齐缺失分块。

控制面是事实源；索引中出现但控制面不存在的 chunk 视为孤儿并删除；
控制面存在但索引缺失的 chunk 重新入队，由 Outbox 重试。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.contracts.index import KeywordIndexAdapter, VectorIndexAdapter
from app.contracts.types import Namespace
from app.control.db import ControlPlane


@dataclass(frozen=True)
class ReconcileReport:
    build_id: str
    control_chunks: int
    vector_chunks: int
    keyword_chunks: int
    orphan_deleted: int
    missing_enqueued: int
    skipped: tuple[str, ...] = field(default_factory=tuple)


def _namespace(plane: ControlPlane, build: dict) -> Namespace:
    kb = plane.get_kb(build["kb_id"])
    if kb is None:
        raise KeyError(f"知识库不存在: {build['kb_id']}")
    return Namespace(
        tenant_id=kb["tenant_id"], kb_id=build["kb_id"], build_id=build["id"]
    )


def reconcile_build(
    plane: ControlPlane,
    build_id: str,
    vector_index: VectorIndexAdapter,
    keyword_index: KeywordIndexAdapter,
) -> ReconcileReport:
    build = plane.get_build(build_id)
    if build is None:
        raise KeyError(f"Build 不存在: {build_id}")
    namespace = _namespace(plane, build)

    rows = plane.connection.execute(
        "SELECT chunk_id FROM chunks WHERE build_id=?", (build_id,)
    ).fetchall()
    control_ids = {row["chunk_id"] for row in rows}
    pending_rows = plane.connection.execute(
        "SELECT DISTINCT chunk_id FROM index_tasks WHERE build_id=? "
        "AND status IN ('pending','running')",
        (build_id,),
    ).fetchall()
    pending_ids = {row["chunk_id"] for row in pending_rows}

    orphan_deleted = 0
    missing: set[str] = set()
    skipped: list[str] = []
    vector_count = 0
    keyword_count = 0

    for name, adapter in (("vector", vector_index), ("keyword", keyword_index)):
        capabilities = adapter.capabilities()
        if not capabilities.list_ids:
            skipped.append(name)
            continue
        index_ids = set(adapter.list_chunk_ids(namespace))
        if name == "vector":
            vector_count = len(index_ids)
        else:
            keyword_count = len(index_ids)
        orphans = sorted(index_ids - control_ids)
        if orphans:
            adapter.delete(namespace, orphans)
            orphan_deleted += len(orphans)
        missing |= control_ids - index_ids

    enqueued = 0
    for chunk_id in sorted(missing - pending_ids):
        plane.enqueue_index_task(build_id, chunk_id, "upsert")
        enqueued += 1

    plane.write_audit(
        "build.reconcile",
        "build",
        build_id,
        kb_id=build["kb_id"],
        detail={
            "control_chunks": len(control_ids),
            "orphan_deleted": orphan_deleted,
            "missing_enqueued": enqueued,
            "skipped": skipped,
        },
    )
    return ReconcileReport(
        build_id=build_id,
        control_chunks=len(control_ids),
        vector_chunks=vector_count,
        keyword_chunks=keyword_count,
        orphan_deleted=orphan_deleted,
        missing_enqueued=enqueued,
        skipped=tuple(skipped),
    )
