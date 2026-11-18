"""把构建链路 active Build 的检索结果转换为问答链路的 Hit。"""

from __future__ import annotations

from app.engine.runtime import get_runtime
from app.indexing.folders import FolderPathError, normalize_folder
from app.retrieval.intents import classify
from app.retrieval.searcher import Hit


def active_build_id(kb_id: str | None) -> str | None:
    if not kb_id:
        return None
    kb = get_runtime().plane.get_kb(kb_id)
    return (kb or {}).get("active_build_id")


def _scopes(folder: str | None, folders: list[str] | None) -> list[str]:
    values = ([folder] if folder else []) + list(folders or [])
    scopes: list[str] = []
    for item in values:
        try:
            path = normalize_folder(item, default=None)
        except FolderPathError:
            path = ""
        if path and path not in scopes:
            scopes.append(path)
    return scopes


def _in_scope(path: str, scopes: list[str]) -> bool:
    return not scopes or any(path == scope or path.startswith(scope + "/") for scope in scopes)


def retrieve_from_engine(
    query: str,
    settings,
    kb_id: str | None,
    top_k: int | None = None,
    folder: str | None = None,
    folders: list[str] | None = None,
) -> dict:
    """返回与 run_retrieval 同结构的字典：intent/hits/trace/plan/entities。"""
    intent = classify(query)
    if intent.intent == "chitchat":
        return {
            "intent": "chitchat",
            "hits": [],
            "trace": [{"stage": "route", "result": "寒暄，无需检索"}],
            "plan": {"skip_retrieval": True, "strategy": "engine"},
            "entities": intent.entities,
        }

    runtime = get_runtime()
    kb = runtime.plane.get_kb(kb_id or "")
    build_id = (kb or {}).get("active_build_id")
    if not build_id:
        raise LookupError(f"知识库 {kb_id} 没有 active Build")

    top = top_k or settings.top_k_default
    found = runtime.engine.search(build_id, query, top_k=max(top * 3, top), preset=None)
    chunks = {
        row["chunk_id"]: row
        for row in runtime.plane.get_chunks(build_id, [hit.chunk_id for hit in found])
    }
    folder_names = {
        item["id"]: item["path"] for item in runtime.plane.list_folders(kb_id or "")
    }
    scopes = _scopes(folder, folders)
    hits: list[Hit] = []
    for hit in found:
        chunk = chunks.get(hit.chunk_id)
        if not chunk:
            continue
        doc = runtime.plane.get_document(chunk["doc_id"]) or {}
        folder_path = folder_names.get(doc.get("folder_id"), "")
        if not _in_scope(folder_path, scopes):
            continue
        hits.append(
            Hit(
                chunk_id=hit.chunk_id,
                rrf=hit.score,
                sim=None,
                text=chunk.get("text", ""),
                doc_id=chunk.get("doc_id", ""),
                doc_name=doc.get("filename", ""),
                section=chunk.get("section", ""),
                page=chunk.get("page"),
                kind=chunk.get("kind", "text"),
                table_no=chunk.get("table_no"),
                parent_text=chunk.get("text", ""),
                folder=folder_path,
            )
        )
        if len(hits) >= top:
            break

    return {
        "intent": intent.intent,
        "hits": hits,
        "trace": [
            {
                "stage": "retrieve",
                "strategy": "engine",
                "buildId": build_id,
                "hits": len(hits),
            }
        ],
        "plan": {"skip_retrieval": False, "multihop": False, "strategy": "engine"},
        "entities": intent.entities,
    }
