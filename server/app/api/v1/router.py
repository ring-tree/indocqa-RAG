"""API v1：并行提供的新引擎入口。

本模块不替换旧 `/api`；旧链路行为保持不变，P4 完成等价验证后再切换。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field, ValidationError

from app.config.models import EngineDefaults, KBConfig
from app.contracts.config import ConfigLayer, analyze_impact, flatten_config, merge_layers
from app.contracts.types import ContractError
from app.core.config import SERVER_ROOT, get_settings
from app.core.exceptions import (
    BuildNotFoundError,
    BuildStateConflictError,
    ConfigInvalidError,
    KnowledgeBaseNotFoundError,
    ParamError,
)
from app.core.llm import LLMGateway
from app.core.runtime_overrides import get_overrides
from app.engine.pipeline import ChunkInput
from app.engine.runtime import get_runtime
from app.packs.diff import diff_packs
from app.packs.loader import PackError

router = APIRouter(prefix="/api/v1")
SCHEMA_DIR = SERVER_ROOT / "config" / "schemas"


class KBCreate(BaseModel):
    name: str = Field(default="默认知识库", min_length=1, max_length=100)
    kbId: str | None = None
    languageProfile: str = "zh-CN"
    domainPackId: str | None = None
    domainPackVersion: str | None = None


class PackRef(BaseModel):
    packId: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=32)


class BuildCreate(BaseModel):
    preset: str = "balanced@1"
    pipelineVersion: str = ""
    domainPackId: str = ""
    domainPackVersion: str = ""


class RollbackRequest(BaseModel):
    buildId: str = Field(min_length=1)


class DocumentCreate(BaseModel):
    docId: str
    filename: str
    contentHash: str
    objectKey: str | None = None
    pages: int | None = None


class ChunkBody(BaseModel):
    chunkId: str
    text: str
    section: str = ""
    page: int | None = None
    kind: str = "text"
    tableNo: str | None = None
    parentId: str | None = None


class ChunksCreate(BaseModel):
    docId: str
    chunks: list[ChunkBody]


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    buildId: str | None = None
    kbId: str | None = None
    topK: int = Field(default=6, ge=1, le=20)
    preset: str | None = None


def _kb_or_404(kb_id: str) -> dict:
    kb = get_runtime().plane.get_kb(kb_id)
    if kb is None:
        raise KnowledgeBaseNotFoundError(f"知识库不存在: {kb_id}")
    return kb


def _build_or_404(build_id: str) -> dict:
    build = get_runtime().plane.get_build(build_id)
    if build is None:
        raise BuildNotFoundError(f"Build 不存在: {build_id}")
    return build


def _build_payload(row: dict) -> dict:
    return {
        "buildId": row["id"],
        "kbId": row["kb_id"],
        "status": row["status"],
        "health": row["health_status"],
        "preset": row["retrieval_preset"],
        "embeddingFingerprint": row["embedding_fingerprint"],
        "stats": json.loads(row["stats_json"] or "{}"),
        "createdAt": row["created_at"],
        "activatedAt": row["activated_at"],
    }


def _current_config(plane, kb: dict) -> tuple[dict, int, str]:
    row = plane.connection.execute(
        "SELECT config_json, version, config_hash FROM kb_config_versions "
        "WHERE kb_id=? ORDER BY version DESC LIMIT 1",
        (kb["id"],),
    ).fetchone()
    if row:
        return json.loads(row["config_json"]), int(row["version"]), row["config_hash"]
    config = KBConfig(
        kb_id=kb["id"],
        name=kb["name"],
        language_profile=kb["language_profile"],
        domain_pack_id=kb["domain_pack_id"],
        embedding_model=kb["embedding_model_id"] or "",
    )
    return config.model_dump(), 0, ""


def _merge(base: dict, patch: dict) -> dict:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _changed_paths(old: dict, new: dict) -> list[str]:
    old_flat = flatten_config(old)
    new_flat = flatten_config(new)
    return [path for path, value in new_flat.items() if old_flat.get(path) != value]


def _save_config(plane, kb_id: str, config: dict, version: int) -> str:
    payload = json.dumps(config, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    with plane.transaction() as conn:
        conn.execute(
            "INSERT INTO kb_config_versions(id, kb_id, version, config_json, config_hash, "
            "created_at) VALUES (?,?,?,?,?,datetime('now'))",
            (f"cfg_{uuid.uuid4().hex[:12]}", kb_id, version + 1, payload, digest),
        )
        conn.execute(
            "UPDATE knowledge_bases SET config_version=?, updated_at=datetime('now') WHERE id=?",
            (version + 1, kb_id),
        )
    return digest


def _validate_config(candidate: dict) -> dict:
    try:
        return KBConfig.model_validate(candidate).model_dump()
    except ValidationError as exc:
        errors = [
            {"path": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
            for item in exc.errors()
        ]
        raise ConfigInvalidError("配置校验失败", detail=errors) from exc


@router.get("/health")
def health() -> dict:
    runtime = get_runtime()
    plane = runtime.plane
    builds = plane.connection.execute("SELECT COUNT(*) FROM builds").fetchone()[0]
    return {
        "status": "ok",
        "version": get_settings().version,
        "engine": {
            "dataDir": runtime.settings.engine_data_dir,
            "embedding": {
                "backend": "ollama",
                "model": runtime.embedder.descriptor.model,
                "dimension": runtime.embedder.descriptor.dimension,
            },
            "adapters": {"vector": "chroma", "keyword": "bm25", "object": "local"},
            "builds": builds,
        },
    }


@router.get("/models")
def models() -> dict:
    return {"data": LLMGateway(get_settings()).profiles()}


@router.get("/ui-profile")
def ui_profile() -> dict:
    overrides = get_overrides()
    ui = overrides.ui if overrides and overrides.ui else {}
    return {
        "data": {
            "subtitle": ui.get("subtitle") or "企业知识库智能问答",
            "examples": ui.get("examples")
            or ["这个系统能做什么？", "支持哪些文档格式？", "如何提高检索命中率？"],
            "folderExample": ui.get("folder_example") or "",
        }
    }


@router.get("/embedding-models")
def embedding_models() -> dict:
    return {"data": get_runtime().engine.registry.list_verified()}


@router.get("/config/schemas")
def config_schemas() -> dict:
    data: dict[str, Any] = {}
    for path in sorted(SCHEMA_DIR.glob("*.json")):
        data[path.stem.replace(".schema", "")] = json.loads(path.read_text(encoding="utf-8"))
    return {"data": data}


@router.get("/config/schemas/{scope}")
def config_schema(scope: str) -> dict:
    path = SCHEMA_DIR / f"{scope}.schema.json"
    if not path.exists():
        raise ParamError(f"未知配置 scope: {scope}")
    return {"data": json.loads(path.read_text(encoding="utf-8"))}


@router.get("/kbs")
def list_kbs() -> dict:
    items = []
    for kb in get_runtime().plane.list_kbs():
        items.append(
            {
                "kbId": kb["id"],
                "name": kb["name"],
                "languageProfile": kb["language_profile"],
                "domainPackId": kb["domain_pack_id"],
                "domainPackVersion": kb["domain_pack_version"],
                "activeBuildId": kb["active_build_id"],
                "configVersion": kb["config_version"],
            }
        )
    return {"data": {"items": items, "nextCursor": None}}


@router.post("/kbs", status_code=201)
def create_kb(body: KBCreate) -> dict:
    plane = get_runtime().plane
    try:
        kb_id = plane.create_kb(
            body.name,
            kb_id=body.kbId,
            language_profile=body.languageProfile,
            domain_pack_id=body.domainPackId,
            domain_pack_version=body.domainPackVersion,
        )
    except Exception as exc:
        raise ParamError(f"知识库创建失败: {exc}") from exc
    kb = plane.get_kb(kb_id)
    return {
        "data": {
            "kbId": kb_id,
            "name": kb["name"],
            "languageProfile": kb["language_profile"],
            "activeBuildId": None,
            "configVersion": 0,
        }
    }


@router.get("/kbs/{kb_id}")
def get_kb(kb_id: str) -> dict:
    kb = _kb_or_404(kb_id)
    return {
        "data": {
            "kbId": kb["id"],
            "name": kb["name"],
            "languageProfile": kb["language_profile"],
            "domainPackId": kb["domain_pack_id"],
            "domainPackVersion": kb["domain_pack_version"],
            "activeBuildId": kb["active_build_id"],
            "previousBuildId": kb["previous_build_id"],
            "configVersion": kb["config_version"],
        }
    }


@router.get("/kbs/{kb_id}/config")
def get_config(kb_id: str) -> dict:
    kb = _kb_or_404(kb_id)
    plane = get_runtime().plane
    current, version, digest = _current_config(plane, kb)
    merged = merge_layers(
        {ConfigLayer.ENGINE: EngineDefaults().model_dump(), ConfigLayer.KB: current}
    )
    return {
        "data": {
            "config": current,
            "effective": merged.flat(),
            "sources": merged.sources(),
            "version": version,
            "etag": f'"cfg-{version}"' if version else "",
            "hash": digest,
            "impact": {
                "requiresNewBuild": False,
                "rebuildMode": None,
                "profiles": [],
                "affected": [],
            },
        }
    }


@router.patch("/kbs/{kb_id}/config")
def patch_config(kb_id: str, body: dict) -> dict:
    kb = _kb_or_404(kb_id)
    plane = get_runtime().plane
    current, version, _ = _current_config(plane, kb)
    merged = _merge(current, body)
    validated = _validate_config(merged)
    impact = analyze_impact(_changed_paths(current, validated))
    digest = _save_config(plane, kb_id, validated, version)
    return {
        "data": {
            "config": validated,
            "version": version + 1,
            "etag": f'"cfg-{version + 1}"',
            "hash": digest,
            "impact": {
                "requiresNewBuild": impact.requires_new_build,
                "rebuildMode": impact.rebuild_mode.value if impact.rebuild_mode else None,
                "profiles": list(impact.profiles),
                "affected": list(impact.affected),
                "unknown": list(impact.unknown),
            },
        }
    }


@router.post("/kbs/{kb_id}/config/validate")
def validate_config(kb_id: str, body: dict) -> dict:
    kb = _kb_or_404(kb_id)
    plane = get_runtime().plane
    current, _, _ = _current_config(plane, kb)
    candidate = body.get("config", body)
    merged = _merge(current, candidate)
    errors: list[dict] = []
    try:
        validated = KBConfig.model_validate(merged).model_dump()
    except ValidationError as exc:
        errors = [
            {"path": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
            for item in exc.errors()
        ]
        return {
            "data": {
                "valid": False,
                "errors": errors,
                "impact": None,
                "warnings": [],
            }
        }
    impact = analyze_impact(_changed_paths(current, validated))
    return {
        "data": {
            "valid": True,
            "errors": [],
            "impact": {
                "requiresNewBuild": impact.requires_new_build,
                "rebuildMode": impact.rebuild_mode.value if impact.rebuild_mode else None,
                "profiles": list(impact.profiles),
                "affected": list(impact.affected),
                "unknown": list(impact.unknown),
            },
            "warnings": [],
        }
    }


@router.get("/packs")
def list_packs() -> dict:
    runtime = get_runtime()
    return {
        "data": {
            "installed": runtime.pack_registry.list_installed(),
            "discovered": runtime.pack_loader.discover(),
        }
    }


@router.post("/packs/validate")
def validate_pack(body: PackRef) -> dict:
    try:
        loaded = get_runtime().pack_loader.load(body.packId, body.version)
    except PackError as exc:
        raise ParamError(str(exc)) from exc
    return {
        "data": {
            "valid": True,
            "packId": loaded.pack.manifest.id,
            "version": loaded.pack.manifest.version,
            "contentHash": loaded.content_hash,
            "files": list(loaded.files),
        }
    }


@router.post("/packs/install")
def install_pack(body: PackRef) -> dict:
    try:
        record = get_runtime().pack_registry.install(body.packId, body.version)
    except ContractError as exc:
        raise ParamError(str(exc)) from exc
    return {"data": record}


@router.post("/packs/uninstall")
def uninstall_pack(body: PackRef) -> dict:
    try:
        get_runtime().pack_registry.uninstall(body.packId, body.version)
    except ContractError as exc:
        raise ParamError(str(exc)) from exc
    return {"data": True}


@router.get("/packs/diff")
def pack_diff(packId: str, fromVersion: str, toVersion: str) -> dict:
    runtime = get_runtime()
    try:
        old = runtime.pack_loader.load(packId, fromVersion)
        new = runtime.pack_loader.load(packId, toVersion)
    except PackError as exc:
        raise ParamError(str(exc)) from exc
    diff = diff_packs(old.pack, new.pack)
    return {
        "data": {
            "packId": packId,
            "fromVersion": fromVersion,
            "toVersion": toVersion,
            "added": list(diff.added),
            "removed": list(diff.removed),
            "changed": list(diff.changed),
            "requiresNewBuild": diff.requires_new_build,
            "rebuildMode": diff.rebuild_mode.value if diff.rebuild_mode else None,
            "profiles": list(diff.profiles),
            "ingestPolicyChanged": diff.ingest_policy_changed,
            "unknown": list(diff.unknown),
        }
    }


@router.post("/kbs/{kb_id}/pack")
def bind_pack(kb_id: str, body: PackRef) -> dict:
    try:
        data = get_runtime().pack_registry.bind_kb(kb_id, body.packId, body.version)
    except ContractError as exc:
        raise ParamError(str(exc)) from exc
    return {"data": data}


@router.get("/kbs/{kb_id}/pack")
def get_kb_pack(kb_id: str) -> dict:
    kb = _kb_or_404(kb_id)
    return {
        "data": {
            "packId": kb["domain_pack_id"],
            "version": kb["domain_pack_version"],
        }
    }


@router.delete("/kbs/{kb_id}/pack")
def unbind_pack(kb_id: str) -> dict:
    _kb_or_404(kb_id)
    get_runtime().pack_registry.unbind_kb(kb_id)
    return {"data": True}


@router.post("/kbs/{kb_id}/builds", status_code=202)
def create_build(kb_id: str, body: BuildCreate) -> dict:
    runtime = get_runtime()
    _kb_or_404(kb_id)
    snapshot = None
    if body.domainPackId:
        try:
            loaded = runtime.pack_registry.load_for_build(
                body.domainPackId, body.domainPackVersion
            )
        except ContractError as exc:
            raise ParamError(str(exc)) from exc
        snapshot = loaded.pack.model_dump_json()
    build_id = runtime.engine.create_build(
        kb_id,
        preset=body.preset,
        pipeline_version=body.pipelineVersion,
        domain_pack_id=body.domainPackId,
        domain_pack_version=body.domainPackVersion,
    )
    if snapshot:
        runtime.plane.attach_pack_snapshot(build_id, snapshot)
    return {
        "data": {
            "buildId": build_id,
            "status": "draft",
            "statusUrl": f"/api/v1/builds/{build_id}",
        }
    }


@router.get("/builds/{build_id}")
def get_build(build_id: str) -> dict:
    return {"data": _build_payload(_build_or_404(build_id))}


@router.post("/builds/{build_id}/activate")
def activate_build(build_id: str) -> dict:
    plane = get_runtime().plane
    build = _build_or_404(build_id)
    if build["status"] == "active":
        return {"data": {"buildId": build_id, "status": "active", "noop": True}}
    if build["status"] not in {"draft", "ready", "archived"}:
        raise BuildStateConflictError(
            f"Build 状态为 {build['status']}，不允许激活"
        )
    plane.activate_build(build["kb_id"], build_id)
    action = "build.rollback" if build["status"] == "archived" else "build.activate"
    plane.write_audit(action, "build", build_id, kb_id=build["kb_id"])
    return {"data": {"buildId": build_id, "status": "active", "noop": False}}


@router.post("/kbs/{kb_id}/rollback")
def rollback_build(kb_id: str, body: RollbackRequest) -> dict:
    """回滚到指定历史 Build（archived/ready）；当前 active 转为 archived。"""
    _kb_or_404(kb_id)
    build = _build_or_404(body.buildId)
    if build["kb_id"] != kb_id:
        raise ParamError("Build 不属于该知识库")
    if build["status"] == "active":
        return {"data": {"buildId": build["id"], "status": "active", "noop": True}}
    if build["status"] not in {"archived", "ready"}:
        raise BuildStateConflictError(
            f"Build 状态为 {build['status']}，不允许回滚"
        )
    plane = get_runtime().plane
    plane.activate_build(kb_id, build["id"])
    plane.write_audit("build.rollback", "build", build["id"], kb_id=kb_id)
    return {"data": {"buildId": build["id"], "status": "active", "noop": False}}


@router.post("/builds/{build_id}/reconcile")
def reconcile(build_id: str) -> dict:
    from app.control.reconcile import reconcile_build

    runtime = get_runtime()
    report = reconcile_build(
        runtime.plane, build_id, runtime.vector_index, runtime.keyword_index
    )
    return {
        "data": {
            "buildId": report.build_id,
            "controlChunks": report.control_chunks,
            "vectorChunks": report.vector_chunks,
            "keywordChunks": report.keyword_chunks,
            "orphanDeleted": report.orphan_deleted,
            "missingEnqueued": report.missing_enqueued,
            "skipped": list(report.skipped),
        }
    }


@router.post("/kbs/{kb_id}/documents", status_code=201)
def register_document(kb_id: str, body: DocumentCreate) -> dict:
    plane = get_runtime().plane
    _kb_or_404(kb_id)
    object_key = body.objectKey or f"kb/{kb_id}/blobs/{body.contentHash[:2]}/{body.contentHash}"
    doc_id, created = plane.register_document(
        kb_id,
        body.docId,
        body.contentHash,
        object_key,
        body.filename,
        pages=body.pages,
    )
    return {"data": {"docId": doc_id, "created": created}}


@router.post("/builds/{build_id}/chunks", status_code=202)
def index_chunks(build_id: str, body: ChunksCreate) -> dict:
    _build_or_404(build_id)
    chunks = [
        ChunkInput(
            chunk_id=item.chunkId,
            text=item.text,
            section=item.section,
            page=item.page,
            kind=item.kind,
            table_no=item.tableNo,
            parent_id=item.parentId,
        )
        for item in body.chunks
    ]
    count = get_runtime().engine.index_chunks(build_id, body.docId, chunks)
    return {"data": {"buildId": build_id, "indexed": count}}


@router.post("/search")
def search(body: SearchRequest) -> dict:
    runtime = get_runtime()
    build_id = body.buildId
    if build_id is None:
        if body.kbId is None:
            raise ParamError("buildId 与 kbId 至少提供一个")
        kb = _kb_or_404(body.kbId)
        build_id = kb["active_build_id"]
        if not build_id:
            raise BuildNotFoundError(f"知识库 {body.kbId} 没有 active Build")
    hits = runtime.engine.search(
        build_id, body.query, top_k=body.topK, preset=body.preset
    )
    return {
        "data": {
            "buildId": build_id,
            "items": [
                {"chunkId": hit.chunk_id, "score": hit.score, "metadata": hit.metadata}
                for hit in hits
            ],
        }
    }
