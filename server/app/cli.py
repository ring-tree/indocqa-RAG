"""命令行入口：init / validate / build / query。

约定：
- 项目目录下的 `indocqa.json` 保存 KB、引擎数据目录、Pack 与语料路径；
- CLI 只装配既有引擎能力，不复制业务逻辑；
- `query` 使用新引擎的 Build 级检索，输出命中块与来源文档。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from app.control.db import ControlPlane
from app.core.config import SERVER_ROOT, get_settings
from app.engine.pipeline import ChunkInput
from app.engine.runtime import get_runtime, reset_runtime
from app.packs.loader import PackError, PackLoader
from app.parsing.chunker import build_chunks
from app.parsing.loader import load_document

CONFIG_NAME = "indocqa.json"
SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".pdf"}
DEFAULT_PRESET = "balanced@1"


def _fail(message: str) -> int:
    print(f"错误：{message}", file=sys.stderr)
    return 1


def _parse_pack(ref: str | None) -> tuple[str, str] | None:
    if not ref:
        return None
    pack_id, _, version = ref.partition("@")
    if not pack_id:
        raise ValueError("Pack 引用格式应为 <pack-id>@<version>")
    return pack_id, version or "1.0.0"


def _project_paths(packs_dir: str | None, corpus: str | None) -> dict:
    return {
        "engineDataDir": "data/engine",
        "packsDir": str((Path(packs_dir) if packs_dir else SERVER_ROOT / "packs").resolve()),
        "corpus": corpus or "data/corpus",
        "preset": DEFAULT_PRESET,
    }


def _write_config(path: Path, data: dict) -> None:
    payload = {key: value for key, value in data.items() if not key.startswith("_")}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_config_file(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"找不到配置文件: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"配置文件不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise TypeError("配置文件根节点必须是对象")
    return data


def _resolve(project_dir: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (project_dir / path).resolve()


def _load_project(project_dir: str) -> tuple[Path, dict]:
    root = Path(project_dir).resolve()
    data = _load_config_file(root / CONFIG_NAME)
    return root, data


def _validate_config(data: dict, project_dir: Path) -> list[str]:
    errors: list[str] = []
    for key in ("kbId", "packsDir", "engineDataDir", "preset"):
        if not str(data.get(key, "")).strip():
            errors.append(f"缺少字段或为空: {key}")
    preset = str(data.get("preset", ""))
    if preset and preset not in {"precision@1", "balanced@1", "recall@1"}:
        errors.append(f"preset 不合法: {preset}（可选 precision@1 / balanced@1 / recall@1）")
    pack_ref = data.get("pack")
    if pack_ref:
        try:
            pack_id, version = _parse_pack(str(pack_ref)) or ("", "")
            packs_dir = _resolve(project_dir, str(data["packsDir"]))
            PackLoader(packs_dir, engine_version=get_settings().version).load(pack_id, version)
        except (ValueError, PackError, OSError) as exc:
            errors.append(f"Pack 不可用: {exc}")
    return errors


def _prepare_runtime(data: dict, project_dir: Path):
    engine_dir = _resolve(project_dir, str(data["engineDataDir"]))
    packs_dir = _resolve(project_dir, str(data["packsDir"]))
    engine_dir.mkdir(parents=True, exist_ok=True)
    os.environ["INDOCQA_ENGINE_DATA_DIR"] = str(engine_dir)
    os.environ["INDOCQA_PACKS_DIR"] = str(packs_dir)
    get_settings.cache_clear()
    reset_runtime()
    return _make_runtime(get_settings())


def _make_runtime(settings):
    """组合根；测试可替换为内存适配器。"""
    return get_runtime()


def _ensure_kb(plane: ControlPlane, data: dict) -> str:
    kb_id = str(data["kbId"])
    if plane.get_kb(kb_id) is None:
        plane.create_kb(
            str(data.get("kbName") or "默认知识库"),
            kb_id=kb_id,
            domain_pack_id=(_parse_pack(data.get("pack")) or ("", ""))[0] or None,
            domain_pack_version=(_parse_pack(data.get("pack")) or ("", ""))[1] or None,
        )
    return kb_id


def _install_and_bind(runtime, data: dict, kb_id: str) -> tuple[str, str, str | None]:
    pack = _parse_pack(data.get("pack"))
    if not pack:
        return "", "", None
    pack_id, version = pack
    runtime.pack_registry.install(pack_id, version)
    runtime.pack_registry.bind_kb(kb_id, pack_id, version)
    loaded = runtime.pack_registry.load_for_build(pack_id, version)
    return pack_id, version, loaded.pack.model_dump_json()


def _iter_corpus(corpus_dir: Path) -> list[Path]:
    if not corpus_dir.is_dir():
        return []
    return sorted(
        path
        for path in corpus_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )


def cmd_init(args) -> int:
    project_dir = Path(args.dir).resolve()
    project_dir.mkdir(parents=True, exist_ok=True)
    config_path = project_dir / CONFIG_NAME
    if config_path.exists() and not args.force:
        return _fail(f"配置已存在: {config_path}（使用 --force 覆盖）")

    packs_dir = str((Path(args.packs_dir) if args.packs_dir else SERVER_ROOT / "packs").resolve())
    pack_ref = args.pack or ""
    try:
        pack = _parse_pack(pack_ref)
        if pack:
            PackLoader(packs_dir, engine_version=get_settings().version).load(*pack)
    except (ValueError, PackError, OSError) as exc:
        return _fail(f"Pack 校验失败: {exc}")

    engine_dir = project_dir / "data" / "engine"
    corpus_dir = project_dir / args.corpus
    engine_dir.mkdir(parents=True, exist_ok=True)
    corpus_dir.mkdir(parents=True, exist_ok=True)

    kb_id = args.kb_id or "kb_default"
    plane = ControlPlane(engine_dir / "control.db")
    try:
        if plane.get_kb(kb_id) is None:
            plane.create_kb(
                args.name,
                kb_id=kb_id,
                domain_pack_id=pack[0] if pack else None,
                domain_pack_version=pack[1] if pack else None,
            )
        if pack:
            from app.packs.registry import PackRegistry

            registry = PackRegistry(
                plane,
                PackLoader(packs_dir, engine_version=get_settings().version),
                engine_version=get_settings().version,
            )
            registry.install(*pack)
    finally:
        plane.close()

    config = _project_paths(packs_dir, args.corpus)
    config.update(
        {
            "kbId": kb_id,
            "kbName": args.name,
            "pack": pack_ref,
        }
    )
    _write_config(config_path, config)
    print(f"已初始化：{project_dir}")
    print(f"  配置：{config_path}")
    print(f"  知识库：{kb_id}（引擎数据目录 {engine_dir}）")
    if pack:
        print(f"  Pack：{pack[0]}@{pack[1]}")
    print(f"  下一步：把文档放入 {corpus_dir}，然后运行 `indocqa build --dir {project_dir}`")
    return 0


def cmd_validate(args) -> int:
    config_path = Path(args.config).resolve() if args.config else Path(args.dir).resolve() / CONFIG_NAME
    try:
        data = _load_config_file(config_path)
    except (FileNotFoundError, ValueError, TypeError) as exc:
        return _fail(str(exc))
    project_dir = config_path.parent
    errors = _validate_config(data, project_dir)
    if errors:
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(
        f"配置有效：kb={data['kbId']} preset={data['preset']} "
        f"pack={data.get('pack') or '-'} packsDir={data['packsDir']}"
    )
    return 0


def cmd_build(args) -> int:
    try:
        project_dir, data = _load_project(args.dir)
    except (FileNotFoundError, ValueError, TypeError) as exc:
        return _fail(str(exc))
    if args.pack:
        data = {**data, "pack": args.pack}
    if args.preset:
        data = {**data, "preset": args.preset}
    errors = _validate_config(data, project_dir)
    if errors:
        return _fail("；".join(errors))

    runtime = _prepare_runtime(data, project_dir)
    plane = runtime.plane
    kb_id = _ensure_kb(plane, data)
    pack_id, pack_version, snapshot = _install_and_bind(runtime, data, kb_id)
    build_id = runtime.engine.create_build(
        kb_id,
        preset=str(data["preset"]),
        domain_pack_id=pack_id,
        domain_pack_version=pack_version,
    )
    if snapshot:
        plane.attach_pack_snapshot(build_id, snapshot)

    corpus_dir = _resolve(project_dir, args.corpus or str(data.get("corpus") or "data/corpus"))
    files = _iter_corpus(corpus_dir)
    if not files:
        return _fail(f"语料目录为空或没有支持的文件: {corpus_dir}")

    document_count = 0
    chunk_count = 0
    for path in files:
        raw = path.read_bytes()
        content_hash = hashlib.sha256(raw).hexdigest()
        loaded = load_document(path)
        children, _parents = build_chunks(loaded)
        if not children:
            continue
        object_key = f"kb/{kb_id}/blobs/{content_hash[:2]}/{content_hash}"
        runtime.object_store.put(object_key, raw)
        plane.register_document(
            kb_id,
            loaded.doc_id,
            content_hash,
            object_key,
            path.name,
            pages=loaded.pages,
            chunks=len(children),
        )
        chunk_count += runtime.engine.index_chunks(
            build_id,
            loaded.doc_id,
            [
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
            ],
        )
        document_count += 1
        print(f"  已索引 {path.name}：{len(children)} 块")

    plane.update_build_status(
        build_id,
        "ready",
        health_status="ok",
        stats={"documents": document_count, "chunks": chunk_count},
    )
    if args.activate:
        plane.activate_build(kb_id, build_id)
    status = plane.get_build(build_id)["status"]
    print(
        f"构建完成：build={build_id} status={status} "
        f"documents={document_count} chunks={chunk_count}"
    )
    return 0


def cmd_query(args) -> int:
    try:
        project_dir, data = _load_project(args.dir)
    except (FileNotFoundError, ValueError, TypeError) as exc:
        return _fail(str(exc))
    runtime = _prepare_runtime(data, project_dir)
    plane = runtime.plane
    kb_id = args.kb or str(data["kbId"])
    kb = plane.get_kb(kb_id)
    if kb is None:
        return _fail(f"知识库不存在: {kb_id}")
    build_id = args.build or kb["active_build_id"]
    if not build_id:
        return _fail(f"知识库 {kb_id} 没有已激活的 Build，请先运行 build")

    hits = runtime.engine.search(
        build_id,
        args.question,
        top_k=args.top_k,
        preset=args.preset or str(data.get("preset") or DEFAULT_PRESET),
    )
    chunks = {item["chunk_id"]: item for item in plane.get_chunks(build_id, [h.chunk_id for h in hits])}
    rows = []
    for index, hit in enumerate(hits, start=1):
        chunk = chunks.get(hit.chunk_id, {})
        doc = plane.get_document(chunk.get("doc_id", "")) or {}
        rows.append(
            {
                "rank": index,
                "score": round(hit.score, 6),
                "chunkId": hit.chunk_id,
                "docId": chunk.get("doc_id", ""),
                "filename": doc.get("filename", ""),
                "section": chunk.get("section", ""),
                "page": chunk.get("page"),
                "text": chunk.get("text", ""),
            }
        )
    if args.json:
        print(json.dumps({"buildId": build_id, "query": args.question, "hits": rows}, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("未命中")
        return 0
    for row in rows:
        print(
            f"[{row['rank']}] score={row['score']} doc={row['filename']} "
            f"section={row['section'] or '-'} page={row['page'] if row['page'] is not None else '-'}"
        )
        print(f"    {row['text']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="indocqa", description="IndocQA 命令行工具")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="初始化项目：配置、控制面与知识库")
    init.add_argument("--dir", default=".", help="项目目录（默认当前目录）")
    init.add_argument("--name", default="默认知识库", help="知识库名称")
    init.add_argument("--kb-id", default="kb_default", help="知识库 ID")
    init.add_argument("--pack", default="", help="Domain Pack，格式 <pack-id>@<version>")
    init.add_argument("--packs-dir", default="", help="Pack 根目录（默认 server/packs）")
    init.add_argument("--corpus", default="data/corpus", help="语料目录（相对项目目录）")
    init.add_argument("--force", action="store_true", help="覆盖已有配置")
    init.set_defaults(func=cmd_init)

    validate = sub.add_parser("validate", help="校验项目配置与 Pack")
    validate.add_argument("--dir", default=".", help="项目目录")
    validate.add_argument("--config", default="", help="直接指定配置文件")
    validate.set_defaults(func=cmd_validate)

    build = sub.add_parser("build", help="构建并激活知识库 Build")
    build.add_argument("--dir", default=".", help="项目目录")
    build.add_argument("--corpus", default="", help="覆盖语料目录")
    build.add_argument("--preset", default="", help="检索预设")
    build.add_argument("--pack", default="", help="覆盖 Domain Pack")
    build.add_argument("--no-activate", dest="activate", action="store_false", help="只构建不激活")
    build.set_defaults(func=cmd_build, activate=True)

    query = sub.add_parser("query", help="对激活 Build 执行一次检索")
    query.add_argument("question", help="查询问题")
    query.add_argument("--dir", default=".", help="项目目录")
    query.add_argument("--kb", default="", help="知识库 ID")
    query.add_argument("--build", default="", help="Build ID（默认使用 KB 激活 Build）")
    query.add_argument("--top-k", type=int, default=6, help="返回条数")
    query.add_argument("--preset", default="", help="覆盖检索预设")
    query.add_argument("--json", action="store_true", help="输出 JSON")
    query.set_defaults(func=cmd_query)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (PackError, FileNotFoundError, ValueError, TypeError, KeyError, OSError) as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001 CLI 需要可读错误而不是堆栈
        return _fail(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
