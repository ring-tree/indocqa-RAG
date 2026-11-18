"""按 KB 解析 Domain Pack 运行期配置与 UI Profile。

进程级 `INDOCQA_LEGACY_PACK` 仅作为问答链路的默认值；显式传入 kbId 时以 KB 绑定为准，
无绑定的 KB 使用空覆盖（不继承进程默认 Pack）。
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import Settings
from app.core.runtime_overrides import RuntimeOverrides
from app.engine.runtime import get_runtime
from app.packs.legacy_adapter import overrides_from_pack
from app.packs.loader import PackError, PackLoader

DEFAULT_SUBTITLE = "企业知识库智能问答"
DEFAULT_EXAMPLES = [
    "这个系统能做什么？",
    "支持哪些文档格式？",
    "如何提高检索命中率？",
]


@lru_cache(maxsize=32)
def _pack_overrides(
    packs_dir: str, pack_id: str, version: str, engine_version: str
) -> RuntimeOverrides:
    loaded = PackLoader(packs_dir, engine_version=engine_version).load(pack_id, version)
    return overrides_from_pack(loaded.pack, content_hash=loaded.content_hash)


def overrides_for_kb(kb_id: str | None, settings: Settings) -> RuntimeOverrides | None:
    """返回 KB 绑定 Pack 的覆盖；KB 不存在或未绑定 Pack 时返回 None。"""
    if not kb_id:
        return None
    kb = get_runtime().plane.get_kb(kb_id)
    if not kb or not kb.get("domain_pack_id"):
        return None
    version = kb.get("domain_pack_version") or "1.0.0"
    try:
        return _pack_overrides(
            str(settings.packs_dir), kb["domain_pack_id"], version, settings.version
        )
    except (PackError, OSError):
        return None


def ui_profile(overrides: RuntimeOverrides | None) -> dict:
    ui = overrides.ui if overrides and overrides.ui else {}
    return {
        "subtitle": ui.get("subtitle") or DEFAULT_SUBTITLE,
        "examples": ui.get("examples") or DEFAULT_EXAMPLES,
        "folderExample": ui.get("folder_example") or "",
    }


def ui_profile_for_kb(kb_id: str | None, settings: Settings) -> dict:
    return ui_profile(overrides_for_kb(kb_id, settings))
