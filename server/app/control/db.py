"""控制面 SQLite：唯一事实源。

本模块只负责：
- schema 与迁移（PRAGMA user_version）；
- KB / Build / 文档 / 分块 / 目录 / Outbox / 审计的最小仓储；
- 事务边界。

索引面（向量、关键词、对象存储）通过适配器写入，不在本模块内。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
DEFAULT_TENANT = "default"
DEFAULT_KB = "kb_default"

_DDL = """
CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_tokens (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'admin',
    expires_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_bases (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    language_profile TEXT NOT NULL DEFAULT 'zh-CN',
    domain_pack_id TEXT,
    domain_pack_version TEXT,
    embedding_model_id TEXT,
    active_build_id TEXT,
    previous_build_id TEXT,
    config_version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kb_config_versions (
    id TEXT PRIMARY KEY,
    kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    config_json TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (kb_id, version)
);

CREATE TABLE IF NOT EXISTS domain_packs (
    id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    engine_range TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'installed',
    installed_at TEXT NOT NULL,
    PRIMARY KEY (id, version)
);

CREATE TABLE IF NOT EXISTS language_profiles (
    id TEXT NOT NULL,
    version TEXT NOT NULL,
    locale TEXT NOT NULL,
    config_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id, version)
);

CREATE TABLE IF NOT EXISTS embedding_models (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    revision TEXT NOT NULL,
    runtime TEXT NOT NULL DEFAULT '',
    dimension INTEGER NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'verified',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS builds (
    id TEXT PRIMARY KEY,
    kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'draft',
    health_status TEXT NOT NULL DEFAULT 'ok',
    engine_version TEXT NOT NULL DEFAULT '',
    pipeline_version TEXT NOT NULL DEFAULT '',
    parser_version TEXT NOT NULL DEFAULT '',
    chunker_version TEXT NOT NULL DEFAULT '',
    index_schema_version TEXT NOT NULL DEFAULT '',
    embedding_fingerprint TEXT NOT NULL DEFAULT '',
    domain_pack_id TEXT NOT NULL DEFAULT '',
    domain_pack_version TEXT NOT NULL DEFAULT '',
    language_profile TEXT NOT NULL DEFAULT '',
    retrieval_preset TEXT NOT NULL DEFAULT 'balanced@1',
    config_hash TEXT NOT NULL DEFAULT '',
    pack_snapshot_json TEXT NOT NULL DEFAULT '{}',
    corpus_revision INTEGER NOT NULL DEFAULT 0,
    stats_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    activated_at TEXT,
    archived_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_builds_kb_status ON builds(kb_id, status);

CREATE TABLE IF NOT EXISTS folders (
    id TEXT PRIMARY KEY,
    kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    parent_path TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (kb_id, path)
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    content_hash TEXT NOT NULL,
    object_key TEXT NOT NULL,
    filename TEXT NOT NULL,
    folder_id TEXT REFERENCES folders(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'ready',
    pages INTEGER,
    chunks INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (kb_id, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_documents_kb_folder ON documents(kb_id, folder_id);

CREATE TABLE IF NOT EXISTS build_documents (
    build_id TEXT NOT NULL REFERENCES builds(id) ON DELETE CASCADE,
    doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'indexing',
    chunk_count INTEGER NOT NULL DEFAULT 0,
    indexed_at TEXT,
    PRIMARY KEY (build_id, doc_id)
);

CREATE TABLE IF NOT EXISTS chunks (
    build_id TEXT NOT NULL REFERENCES builds(id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    parent_id TEXT,
    text TEXT NOT NULL,
    section TEXT NOT NULL DEFAULT '',
    page INTEGER,
    kind TEXT NOT NULL DEFAULT 'text',
    table_no TEXT,
    content_hash TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (build_id, chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_chunks_build_doc ON chunks(build_id, doc_id);

CREATE TABLE IF NOT EXISTS parent_chunks (
    build_id TEXT NOT NULL REFERENCES builds(id) ON DELETE CASCADE,
    parent_id TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    text TEXT NOT NULL,
    section TEXT NOT NULL DEFAULT '',
    page INTEGER,
    PRIMARY KEY (build_id, parent_id)
);

CREATE TABLE IF NOT EXISTS index_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    build_id TEXT NOT NULL REFERENCES builds(id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL,
    op TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    available_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_index_tasks_status ON index_tasks(status, available_at);

CREATE TABLE IF NOT EXISTS document_undo_cache (
    id TEXT PRIMARY KEY,
    kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    doc_id TEXT NOT NULL,
    previous_doc_id TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    kb_id TEXT,
    actor TEXT NOT NULL DEFAULT 'system',
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_kb_created ON audit_log(kb_id, created_at);
"""


_MIGRATION_V2 = """
ALTER TABLE knowledge_bases ADD COLUMN domain_pack_version TEXT;
ALTER TABLE builds ADD COLUMN pack_snapshot_json TEXT NOT NULL DEFAULT '{}';
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class ControlPlane:
    """控制面仓储。所有写操作通过事务串行化。"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self.migrate()

    def close(self) -> None:
        self._conn.close()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._conn:
            yield self._conn

    def migrate(self) -> None:
        with self._lock:
            current = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if current > SCHEMA_VERSION:
                raise RuntimeError(f"数据库版本 {current} 高于引擎支持的 {SCHEMA_VERSION}")
            if current == 0:
                self._conn.executescript(_DDL)
                self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                self._conn.commit()
            elif current < SCHEMA_VERSION:
                self._conn.executescript(_MIGRATION_V2)
                self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                self._conn.commit()
            self._conn.execute(
                "INSERT OR IGNORE INTO tenants(id, name, status, created_at) VALUES (?,?,?,?)",
                (DEFAULT_TENANT, "默认租户", "active", _now()),
            )
            self._conn.commit()

    # ---------- 知识库 ----------

    def create_kb(
        self,
        name: str = "默认知识库",
        kb_id: str | None = None,
        tenant_id: str = DEFAULT_TENANT,
        language_profile: str = "zh-CN",
        domain_pack_id: str | None = None,
        domain_pack_version: str | None = None,
        embedding_model_id: str | None = None,
    ) -> str:
        kb_id = kb_id or DEFAULT_KB
        now = _now()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_bases(
                    id, tenant_id, name, language_profile, domain_pack_id,
                    domain_pack_version, embedding_model_id, config_version,
                    created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,0,?,?)
                """,
                (
                    kb_id,
                    tenant_id,
                    name,
                    language_profile,
                    domain_pack_id,
                    domain_pack_version,
                    embedding_model_id,
                    now,
                    now,
                ),
            )
        return kb_id

    def get_kb(self, kb_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM knowledge_bases WHERE id=?", (kb_id,)).fetchone()
        return dict(row) if row else None

    def list_kbs(self, tenant_id: str = DEFAULT_TENANT) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM knowledge_bases WHERE tenant_id=? ORDER BY created_at", (tenant_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    # ---------- Build ----------

    def create_build(
        self,
        kb_id: str,
        build_id: str | None = None,
        *,
        engine_version: str = "",
        pipeline_version: str = "",
        parser_version: str = "",
        chunker_version: str = "",
        index_schema_version: str = "",
        embedding_fingerprint: str = "",
        domain_pack_id: str = "",
        domain_pack_version: str = "",
        language_profile: str = "",
        retrieval_preset: str = "balanced@1",
        config_hash: str = "",
        pack_snapshot_json: str = "{}",
    ) -> str:
        build_id = build_id or _id("bld")
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO builds(
                    id, kb_id, status, health_status, engine_version, pipeline_version,
                    parser_version, chunker_version, index_schema_version,
                    embedding_fingerprint, domain_pack_id, domain_pack_version,
                    language_profile, retrieval_preset, config_hash, pack_snapshot_json,
                    corpus_revision, stats_json, created_at
                ) VALUES (?,?,'draft','ok',?,?,?,?,?,?,?,?,?,?,?,?,0,'{}',?)
                """,
                (
                    build_id,
                    kb_id,
                    engine_version,
                    pipeline_version,
                    parser_version,
                    chunker_version,
                    index_schema_version,
                    embedding_fingerprint,
                    domain_pack_id,
                    domain_pack_version,
                    language_profile,
                    retrieval_preset,
                    config_hash,
                    pack_snapshot_json,
                    _now(),
                ),
            )
        return build_id

    def get_build(self, build_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM builds WHERE id=?", (build_id,)).fetchone()
        return dict(row) if row else None

    def list_builds(self, kb_id: str, status: str | None = None) -> list[dict]:
        if status is None:
            rows = self._conn.execute(
                "SELECT * FROM builds WHERE kb_id=? ORDER BY created_at DESC", (kb_id,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM builds WHERE kb_id=? AND status=? ORDER BY created_at DESC",
                (kb_id, status),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_build_status(
        self,
        build_id: str,
        status: str,
        *,
        health_status: str | None = None,
        stats: dict | None = None,
    ) -> None:
        fields = ["status=?"]
        values: list[Any] = [status]
        if health_status is not None:
            fields.append("health_status=?")
            values.append(health_status)
        if stats is not None:
            fields.append("stats_json=?")
            values.append(_json(stats))
        values.append(build_id)
        with self.transaction() as conn:
            conn.execute(f"UPDATE builds SET {', '.join(fields)} WHERE id=?", values)

    def activate_build(self, kb_id: str, build_id: str) -> None:
        """原子切换 active/previous，并把旧 active 归档。"""
        now = _now()
        with self.transaction() as conn:
            kb = conn.execute(
                "SELECT active_build_id FROM knowledge_bases WHERE id=?", (kb_id,)
            ).fetchone()
            if kb is None:
                raise KeyError(f"知识库不存在: {kb_id}")
            previous = kb["active_build_id"]
            conn.execute(
                "UPDATE knowledge_bases SET active_build_id=?, previous_build_id=?, updated_at=? "
                "WHERE id=?",
                (build_id, previous, now, kb_id),
            )
            if previous and previous != build_id:
                conn.execute(
                    "UPDATE builds SET status='archived', archived_at=? WHERE id=?",
                    (now, previous),
                )
            conn.execute(
                "UPDATE builds SET status='active', activated_at=? WHERE id=?",
                (now, build_id),
            )

    def bind_pack(self, kb_id: str, pack_id: str, version: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE knowledge_bases SET domain_pack_id=?, domain_pack_version=?, "
                "updated_at=? WHERE id=?",
                (pack_id, version, _now(), kb_id),
            )

    def unbind_pack(self, kb_id: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE knowledge_bases SET domain_pack_id=NULL, domain_pack_version=NULL, "
                "updated_at=? WHERE id=?",
                (_now(), kb_id),
            )

    def list_kb_bindings(self, pack_id: str, version: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT id FROM knowledge_bases WHERE domain_pack_id=? AND domain_pack_version=?",
            (pack_id, version),
        ).fetchall()
        return [row["id"] for row in rows]

    def attach_pack_snapshot(self, build_id: str, snapshot_json: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE builds SET pack_snapshot_json=? WHERE id=?",
                (snapshot_json, build_id),
            )

    def get_pack_snapshot(self, build_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT pack_snapshot_json FROM builds WHERE id=?", (build_id,)
        ).fetchone()
        return None if row is None else row["pack_snapshot_json"]

    # ---------- 文档与目录 ----------
    def create_folder(self, kb_id: str, path: str, name: str | None = None) -> str:
        folder_id = _id("fld")
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO folders(id, kb_id, path, parent_path, name, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (folder_id, kb_id, path, parent, name or path.rsplit("/", 1)[-1], _now()),
            )
        return folder_id

    def list_folders(self, kb_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM folders WHERE kb_id=? ORDER BY path", (kb_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def register_document(
        self,
        kb_id: str,
        doc_id: str,
        content_hash: str,
        object_key: str,
        filename: str,
        *,
        folder_id: str | None = None,
        pages: int | None = None,
        chunks: int = 0,
        status: str = "ready",
    ) -> tuple[str, bool]:
        """按内容哈希幂等注册；返回 (doc_id, 是否新建)。"""
        existing = self._conn.execute(
            "SELECT id FROM documents WHERE kb_id=? AND content_hash=?",
            (kb_id, content_hash),
        ).fetchone()
        if existing:
            return existing["id"], False
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO documents(
                    id, kb_id, content_hash, object_key, filename, folder_id,
                    status, pages, chunks, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    doc_id,
                    kb_id,
                    content_hash,
                    object_key,
                    filename,
                    folder_id,
                    status,
                    pages,
                    chunks,
                    _now(),
                ),
            )
        return doc_id, True

    def get_document(self, doc_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        return dict(row) if row else None

    def list_documents(self, kb_id: str, include_deleted: bool = False) -> list[dict]:
        sql = "SELECT * FROM documents WHERE kb_id=?"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        sql += " ORDER BY created_at DESC"
        rows = self._conn.execute(sql, (kb_id,)).fetchall()
        return [dict(row) for row in rows]

    def soft_delete_document(self, doc_id: str) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE documents SET deleted_at=? WHERE id=?", (_now(), doc_id))

    def move_document(self, doc_id: str, folder_id: str | None) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE documents SET folder_id=? WHERE id=?", (folder_id, doc_id))

    # ---------- 分块 ----------

    def add_chunks(
        self,
        build_id: str,
        doc_id: str,
        chunks: Sequence[dict],
        parents: Sequence[dict] = (),
    ) -> int:
        """写入 Build 级分块；重复写入按主键忽略，保证 Outbox 重试幂等。"""
        now = _now()
        with self.transaction() as conn:
            for parent in parents:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO parent_chunks(
                        build_id, parent_id, doc_id, text, section, page
                    ) VALUES (?,?,?,?,?,?)
                    """,
                    (
                        build_id,
                        parent["parent_id"],
                        doc_id,
                        parent.get("text", ""),
                        parent.get("section", ""),
                        parent.get("page"),
                    ),
                )
            for chunk in chunks:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO chunks(
                        build_id, chunk_id, doc_id, parent_id, text, section,
                        page, kind, table_no, content_hash
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        build_id,
                        chunk["chunk_id"],
                        doc_id,
                        chunk.get("parent_id"),
                        chunk.get("text", ""),
                        chunk.get("section", ""),
                        chunk.get("page"),
                        chunk.get("kind", "text"),
                        chunk.get("table_no"),
                        chunk.get("content_hash", ""),
                    ),
                )
            conn.execute(
                """
                INSERT INTO build_documents(build_id, doc_id, status, chunk_count, indexed_at)
                VALUES (?,?,'indexed',?,?)
                ON CONFLICT(build_id, doc_id) DO UPDATE SET
                    status='indexed', chunk_count=excluded.chunk_count, indexed_at=excluded.indexed_at
                """,
                (build_id, doc_id, len(chunks), now),
            )
        return len(chunks)

    def get_chunks(self, build_id: str, chunk_ids: Sequence[str]) -> list[dict]:
        if not chunk_ids:
            return []
        placeholders = ",".join("?" for _ in chunk_ids)
        rows = self._conn.execute(
            f"SELECT * FROM chunks WHERE build_id=? AND chunk_id IN ({placeholders})",
            (build_id, *chunk_ids),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_parent_chunk(self, build_id: str, parent_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM parent_chunks WHERE build_id=? AND parent_id=?",
            (build_id, parent_id),
        ).fetchone()
        return dict(row) if row else None

    # ---------- Outbox ----------

    def enqueue_index_task(
        self,
        build_id: str,
        chunk_id: str,
        op: str,
        payload: dict | None = None,
    ) -> int:
        now = _now()
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO index_tasks(
                    build_id, chunk_id, op, payload_json, status, attempts,
                    available_at, created_at, updated_at
                ) VALUES (?,?,?,?,'pending',0,?,?,?)
                """,
                (build_id, chunk_id, op, _json(payload or {}), now, now, now),
            )
            return int(cursor.lastrowid)

    def claim_index_tasks(self, limit: int = 50) -> list[dict]:
        """认领到期任务，单事务内改为 running，避免重复消费。"""
        now = _now()
        with self.transaction() as conn:
            rows = conn.execute(
                """
                SELECT * FROM index_tasks
                WHERE status='pending' AND available_at <= ?
                ORDER BY id LIMIT ?
                """,
                (now, limit),
            ).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                conn.execute(
                    f"UPDATE index_tasks SET status='running', updated_at=? "
                    f"WHERE id IN ({placeholders})",
                    (now, *ids),
                )
        return [dict(row) | {"status": "running"} for row in rows]

    def complete_index_task(self, task_id: int) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE index_tasks SET status='done', last_error=NULL, updated_at=? "
                "WHERE id=?",
                (_now(), task_id),
            )

    def fail_index_task(
        self,
        task_id: int,
        error: str,
        *,
        backoff_seconds: int = 30,
        max_attempts: int = 5,
    ) -> None:
        now = datetime.now(UTC)
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT attempts FROM index_tasks WHERE id=?", (task_id,)
            ).fetchone()
            if row is None:
                return
            attempts = int(row["attempts"]) + 1
            status = "failed" if attempts >= max_attempts else "pending"
            available = (now + timedelta(seconds=backoff_seconds)).isoformat(timespec="seconds")
            conn.execute(
                "UPDATE index_tasks SET status=?, attempts=?, last_error=?, "
                "available_at=?, updated_at=? WHERE id=?",
                (status, attempts, error[:2000], available, now.isoformat(timespec="seconds"), task_id),
            )

    def pending_task_count(self, build_id: str | None = None) -> int:
        if build_id:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM index_tasks WHERE status IN ('pending','running') "
                "AND build_id=?",
                (build_id,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM index_tasks WHERE status IN ('pending','running')"
            ).fetchone()
        return int(row[0])

    # ---------- 审计 ----------

    def write_audit(
        self,
        action: str,
        target_type: str,
        target_id: str,
        *,
        tenant_id: str = DEFAULT_TENANT,
        kb_id: str | None = None,
        actor: str = "system",
        detail: dict | None = None,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO audit_log(
                    tenant_id, kb_id, actor, action, target_type, target_id,
                    detail_json, created_at
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (tenant_id, kb_id, actor, action, target_type, target_id, _json(detail or {}), _now()),
            )

    def list_audit(self, kb_id: str | None = None, limit: int = 100) -> list[dict]:
        if kb_id:
            rows = self._conn.execute(
                "SELECT * FROM audit_log WHERE kb_id=? ORDER BY id DESC LIMIT ?",
                (kb_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]
