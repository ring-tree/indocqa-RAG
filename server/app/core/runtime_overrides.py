"""引擎中立的运行期配置覆盖层。

旧模块默认使用内置硬编码；当组合根加载 Domain Pack 时，通过本模块注入
声明式配置，避免引擎直接依赖具体插件。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RuntimeOverrides:
    intent_keywords: dict[str, list[str]] = field(default_factory=dict)
    intent_connectors: list[str] = field(default_factory=list)
    intent_exceptions: list[str] = field(default_factory=list)
    reference_code_pattern: str | None = None
    classification_rules: list[dict] = field(default_factory=list)
    document_types: list[str] = field(default_factory=list)
    title_cleanup: list[str] = field(default_factory=list)
    folder_default: str = "未分类"
    folder_migration: dict[str, str] = field(default_factory=dict)
    skip_files: list[str] = field(default_factory=list)
    terms_clusters: dict[str, list[str]] = field(default_factory=dict)
    data_fact_units: list[str] = field(default_factory=list)
    data_fact_keywords: list[str] = field(default_factory=list)
    authority_types: dict[str, float] = field(default_factory=dict)
    persona_roles: list[str] = field(default_factory=list)
    prompt_style: dict = field(default_factory=dict)
    ui: dict = field(default_factory=dict)


_OVERRIDES: RuntimeOverrides | None = None


def set_overrides(overrides: RuntimeOverrides | None) -> None:
    global _OVERRIDES
    _OVERRIDES = overrides


def clear_overrides() -> None:
    set_overrides(None)


def get_overrides() -> RuntimeOverrides | None:
    return _OVERRIDES


def prompt_style_text() -> str:
    """把 Pack 风格层转成一句可追加的系统提示，不覆盖核心规则。"""
    overrides = get_overrides()
    if not overrides or not overrides.prompt_style:
        return ""
    style = overrides.prompt_style
    parts: list[str] = []
    if style.get("tone"):
        parts.append(f"语气：{style['tone']}")
    if style.get("length"):
        parts.append(f"长度：{style['length']}")
    if style.get("structure"):
        parts.append("结构：" + " → ".join(str(item) for item in style["structure"]))
    return "；".join(parts)
