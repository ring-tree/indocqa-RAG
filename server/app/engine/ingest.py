"""Build 级语料入库：解析、切块、对象存储、控制面与索引写入。

供 CLI、`/api/v1` 构建、增量入库与文档替换复用。
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from app.core.exceptions import BuildBlockedError
from app.engine.pipeline import ChunkInput
from app.parsing.chunker import build_chunks
from app.parsing.loader import load_document
from app.parsing.models import ChildChunk, LoadedDoc

SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".pdf"}


@dataclass
class IngestStats:
    documents: int = 0
    chunks: int = 0
    skipped: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "documents": self.documents,
            "chunks": self.chunks,
            "skipped": list(self.skipped),
            "failed": list(self.failed),
        }


@dataclass
class Prepared:
    path: Path
    raw: bytes
    content_hash: str
    loaded: LoadedDoc
    children: list[ChildChunk]


def _prepare(path: Path) -> Prepared:
    raw = path.read_bytes()
    content_hash = hashlib.sha256(raw).hexdigest()
    loaded = load_document(path)
    children, _parents = build_chunks(loaded)
    return Prepared(path, raw, content_hash, loaded, children)


def _chunk_inputs(children: list[ChildChunk]) -> list[ChunkInput]:
    return [
        ChunkInput(
            chunk_id=child.chunk_id,
            text=child.text,
            section=child.section,
            page=child.page,
            kind=child.kind,
            table_no=child.table_no,
            parent_id=child.parent_id,
        )
        for child in children
    ]


def _store_and_index(
    runtime,
    kb_id: str,
    build_id: str,
    prepared: Prepared,
    filename: str,
    *,
    folder_id: str | None = None,
    doc_id: str | None = None,
) -> tuple[str, int]:
    object_key = f"kb/{kb_id}/blobs/{prepared.content_hash[:2]}/{prepared.content_hash}"
    runtime.object_store.put(object_key, prepared.raw)
    if doc_id is None:
        doc_id, _created = runtime.plane.register_document(
            kb_id,
            prepared.loaded.doc_id,
            prepared.content_hash,
            object_key,
            filename,
            folder_id=folder_id,
            pages=prepared.loaded.pages,
            chunks=len(prepared.children),
        )
    else:
        runtime.plane.update_document(
            doc_id,
            content_hash=prepared.content_hash,
            object_key=object_key,
            filename=filename,
            pages=prepared.loaded.pages,
            chunks=len(prepared.children),
        )
    count = runtime.engine.index_chunks(
        build_id, doc_id, _chunk_inputs(prepared.children)
    )
    return doc_id, count


def _ingest_bytes(
    runtime,
    kb_id: str,
    build_id: str,
    filename: str,
    data: bytes,
    *,
    folder_id: str | None = None,
    doc_id: str | None = None,
) -> dict:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"不支持的格式: {suffix or '无扩展名'}")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            handle.write(data)
            temporary = Path(handle.name)
        prepared = _prepare(temporary)
        if not prepared.children:
            raise ValueError("文档没有可索引内容")
        doc_id, chunks = _store_and_index(
            runtime,
            kb_id,
            build_id,
            prepared,
            filename,
            folder_id=folder_id,
            doc_id=doc_id,
        )
        revision = runtime.plane.bump_corpus_revision(build_id)
        return {"docId": doc_id, "chunks": chunks, "corpusRevision": revision}
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _corpus_files(corpus_dir: Path, skip_files: set[str]) -> list[Path]:
    if not corpus_dir.is_dir():
        raise ValueError(f"语料目录不存在: {corpus_dir}")
    return sorted(
        path
        for path in corpus_dir.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_SUFFIXES
        and path.name not in skip_files
    )


def ingest_corpus(
    runtime,
    kb_id: str,
    build_id: str,
    corpus_dir: str | Path,
    *,
    skip_files: tuple[str, ...] | list[str] = (),
    activate: bool = False,
) -> IngestStats:
    """把语料目录写入指定 Build；部分失败为 ready+warning，全量失败为 blocked。"""
    corpus = Path(corpus_dir)
    skipped_names = set(skip_files)
    files = _corpus_files(corpus, skipped_names)
    if not files:
        raise ValueError(f"语料目录为空或没有支持的文件: {corpus}")

    stats = IngestStats(skipped=sorted(skipped_names))
    for path in files:
        try:
            prepared = _prepare(path)
            if not prepared.children:
                stats.skipped.append(path.name)
                continue
            _store_and_index(runtime, kb_id, build_id, prepared, path.name)
            stats.documents += 1
            stats.chunks += len(prepared.children)
        except Exception as exc:  # noqa: BLE001 单文件失败不阻塞其余文档
            stats.failed.append(
                {"file": path.name, "error": f"{type(exc).__name__}: {exc}"}
            )

    if stats.documents == 0:
        runtime.plane.update_build_status(
            build_id,
            "blocked",
            health_status="blocked",
            stats=stats.as_dict(),
        )
        raise BuildBlockedError("语料没有可入库文档，Build 已阻塞")

    health = "warning" if stats.failed else "ok"
    runtime.plane.update_build_status(
        build_id,
        "ready",
        health_status=health,
        stats=stats.as_dict(),
    )
    if activate:
        runtime.plane.activate_build(kb_id, build_id)
    return stats


def ingest_upload(
    runtime,
    kb_id: str,
    build_id: str,
    filename: str,
    data: bytes,
    *,
    folder_id: str | None = None,
) -> dict:
    """向 active Build 增量入库单个文档；corpus_revision 自增。"""
    return _ingest_bytes(
        runtime, kb_id, build_id, filename, data, folder_id=folder_id
    )


def _restore_snapshot(runtime, build_id: str, doc_id: str, snapshot: dict) -> None:
    document = snapshot["document"]
    runtime.plane.update_document(
        doc_id,
        content_hash=document["content_hash"],
        object_key=document["object_key"],
        filename=document["filename"],
        pages=document.get("pages"),
        chunks=document.get("chunks", 0),
    )
    chunks = snapshot.get("chunks") or []
    if chunks:
        runtime.engine.index_chunks(
            build_id,
            doc_id,
            [
                ChunkInput(
                    chunk_id=item["chunk_id"],
                    text=item["text"],
                    section=item.get("section", ""),
                    page=item.get("page"),
                    kind=item.get("kind", "text"),
                    table_no=item.get("table_no"),
                    parent_id=item.get("parent_id"),
                )
                for item in chunks
            ],
        )


def replace_document(
    runtime,
    kb_id: str,
    build_id: str,
    doc_id: str,
    filename: str,
    data: bytes,
) -> dict:
    """替换文档内容：旧版本进入撤销缓存，新版本写入同一 doc_id。"""
    old = runtime.plane.get_document(doc_id)
    if not old or old["kb_id"] != kb_id or old.get("deleted_at"):
        raise ValueError(f"文档不存在: {doc_id}")
    chunk_ids = runtime.plane.list_chunk_ids(build_id, doc_id)
    old_chunks = runtime.plane.get_chunks(build_id, chunk_ids) if chunk_ids else []
    snapshot = {"document": old, "chunks": old_chunks}
    undo_id = runtime.plane.save_undo(kb_id, doc_id, snapshot)
    runtime.engine.delete_document(build_id, doc_id)
    try:
        result = _ingest_bytes(
            runtime, kb_id, build_id, filename, data, doc_id=doc_id
        )
    except Exception:
        _restore_snapshot(runtime, build_id, doc_id, snapshot)
        runtime.plane.delete_undo(undo_id)
        raise
    result["undoId"] = undo_id
    return result


def undo_document(runtime, kb_id: str, build_id: str, doc_id: str) -> dict:
    """撤销最近一次文档替换，恢复旧内容。"""
    undo = runtime.plane.get_latest_undo(kb_id, doc_id)
    if not undo:
        raise ValueError(f"没有可撤销的文档版本: {doc_id}")
    snapshot = json.loads(undo["snapshot_json"])
    runtime.engine.delete_document(build_id, doc_id)
    _restore_snapshot(runtime, build_id, doc_id, snapshot)
    runtime.plane.delete_undo(undo["id"])
    revision = runtime.plane.bump_corpus_revision(build_id)
    return {
        "docId": doc_id,
        "chunks": len(snapshot.get("chunks") or []),
        "corpusRevision": revision,
    }
