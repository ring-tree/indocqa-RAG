"""四层配置合并、来源标注与变更影响分析。

层级优先级：engine < language < pack < kb。
engine 层字段是安全基线，其他层不得覆盖；覆盖尝试直接报错。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.contracts.types import ContractError


class ConfigLayer(str, Enum):
    ENGINE = "engine"
    LANGUAGE = "language"
    PACK = "pack"
    KB = "kb"


LAYER_ORDER: tuple[ConfigLayer, ...] = (
    ConfigLayer.ENGINE,
    ConfigLayer.LANGUAGE,
    ConfigLayer.PACK,
    ConfigLayer.KB,
)


class ConfigLockError(ContractError):
    """尝试覆盖引擎锁定字段。"""


@dataclass(frozen=True)
class ConfigValue:
    value: Any
    layer: ConfigLayer
    locked: bool = False


@dataclass(frozen=True)
class MergedConfig:
    values: Mapping[str, ConfigValue]

    def get(self, path: str, default: Any = None) -> Any:
        item = self.values.get(path)
        return default if item is None else item.value

    def layer_of(self, path: str) -> ConfigLayer | None:
        item = self.values.get(path)
        return None if item is None else item.layer

    def sources(self) -> dict[str, str]:
        return {path: item.layer.value for path, item in self.values.items()}

    def flat(self) -> dict[str, Any]:
        return {path: item.value for path, item in self.values.items()}


class RebuildMode(str, Enum):
    FULL = "full"
    EMBEDDING_ONLY = "embedding-only"
    CONFIG_ONLY = "config-only"
    METADATA_ONLY = "metadata-only"


@dataclass(frozen=True)
class ConfigImpact:
    requires_new_build: bool
    rebuild_mode: RebuildMode | None
    profiles: tuple[str, ...] = ()
    affected: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()


def flatten_config(data: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """把嵌套配置展平为点路径；列表与标量保持原值。"""
    flat: dict[str, Any] = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            flat.update(flatten_config(value, path))
        else:
            flat[path] = value
    return flat


def merge_layers(
    layers: Mapping[ConfigLayer, Mapping[str, Any]],
    locked_prefixes: tuple[str, ...] = ("engine.",),
) -> MergedConfig:
    """按优先级合并四层配置，并记录每个生效值的来源。"""
    merged: dict[str, ConfigValue] = {}
    locked: set[str] = set()

    for layer in LAYER_ORDER:
        payload = layers.get(layer)
        if not payload:
            continue
        for path, value in flatten_config(payload).items():
            is_locked = path.startswith(locked_prefixes)
            if path in locked and layer != ConfigLayer.ENGINE:
                raise ConfigLockError(f"字段 {path} 由引擎锁定，{layer.value} 层不得覆盖")
            merged[path] = ConfigValue(value=value, layer=layer, locked=is_locked)
            if is_locked:
                locked.add(path)

    return MergedConfig(values=merged)


_EMBEDDING_PREFIXES = ("embedding", "embedding_model", "embedding_fingerprint")
_FULL_PREFIXES = ("pipeline", "parser", "chunker", "index_schema", "index.")
_RETRIEVAL_PREFIXES = ("retrieval", "terms", "intent", "folders.migration", "preset")
_ANSWER_PREFIXES = ("prompts", "prompt", "data_facts", "authority", "persona", "answer")
_UI_PREFIXES = ("ui",)


def _matches(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(prefix + ".") for prefix in prefixes)


def analyze_impact(changed_paths: list[str] | tuple[str, ...]) -> ConfigImpact:
    """根据变更字段判断是否需要新 Build、重建模式与 Profile 类型。"""
    affected = tuple(sorted(dict.fromkeys(changed_paths)))
    modes: set[RebuildMode] = set()
    profiles: set[str] = set()
    unknown: list[str] = []

    for path in affected:
        if _matches(path, _FULL_PREFIXES):
            modes.add(RebuildMode.FULL)
        elif _matches(path, _EMBEDDING_PREFIXES):
            modes.add(RebuildMode.EMBEDDING_ONLY)
        elif _matches(path, _RETRIEVAL_PREFIXES):
            modes.add(RebuildMode.CONFIG_ONLY)
        elif _matches(path, _ANSWER_PREFIXES):
            profiles.add("answer")
        elif _matches(path, _UI_PREFIXES):
            profiles.add("ui")
        else:
            unknown.append(path)

    if RebuildMode.FULL in modes:
        mode: RebuildMode | None = RebuildMode.FULL
    elif RebuildMode.EMBEDDING_ONLY in modes:
        mode = RebuildMode.EMBEDDING_ONLY
    elif RebuildMode.CONFIG_ONLY in modes:
        mode = RebuildMode.CONFIG_ONLY
    elif profiles:
        mode = RebuildMode.METADATA_ONLY
    else:
        mode = None

    return ConfigImpact(
        requires_new_build=mode in {
            RebuildMode.FULL,
            RebuildMode.EMBEDDING_ONLY,
            RebuildMode.CONFIG_ONLY,
        },
        rebuild_mode=mode,
        profiles=tuple(sorted(profiles)),
        affected=affected,
        unknown=tuple(unknown),
    )
