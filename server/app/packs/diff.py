"""Pack 字段级 diff 与影响分析。"""

from __future__ import annotations

from dataclasses import dataclass

from app.contracts.config import RebuildMode, flatten_config
from app.contracts.pack import DomainPack


@dataclass(frozen=True)
class PackDiff:
    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]
    requires_new_build: bool
    rebuild_mode: RebuildMode | None
    profiles: tuple[str, ...]
    ingest_policy_changed: bool
    unknown: tuple[str, ...]


def impact_for_paths(paths: list[str] | tuple[str, ...]) -> dict:
    mode: RebuildMode | None = None
    profiles: set[str] = set()
    ingest = False
    unknown: list[str] = []
    for path in paths:
        top = path.split(".", 1)[0]
        if top in {"intent", "retrieval", "terms"}:
            mode = RebuildMode.CONFIG_ONLY
        elif top in {"prompts", "data_facts", "authority", "persona"}:
            profiles.add("answer")
        elif top == "ui":
            profiles.add("ui")
        elif top in {"folders", "classification"}:
            ingest = True
        elif top in {"manifest", "evaluation"}:
            continue
        else:
            unknown.append(path)
    return {
        "requiresNewBuild": mode in {
            RebuildMode.FULL,
            RebuildMode.EMBEDDING_ONLY,
            RebuildMode.CONFIG_ONLY,
        },
        "rebuildMode": mode.value if mode else None,
        "profiles": sorted(profiles),
        "ingestPolicyChanged": ingest,
        "unknown": sorted(set(unknown)),
    }


def diff_packs(old: DomainPack, new: DomainPack) -> PackDiff:
    old_flat = flatten_config(old.model_dump())
    new_flat = flatten_config(new.model_dump())
    added = tuple(sorted(set(new_flat) - set(old_flat)))
    removed = tuple(sorted(set(old_flat) - set(new_flat)))
    changed = tuple(
        sorted(
            path
            for path in set(old_flat) & set(new_flat)
            if old_flat[path] != new_flat[path]
        )
    )
    impact = impact_for_paths([*added, *removed, *changed])
    return PackDiff(
        added=added,
        removed=removed,
        changed=changed,
        requires_new_build=impact["requiresNewBuild"],
        rebuild_mode=RebuildMode(impact["rebuildMode"]) if impact["rebuildMode"] else None,
        profiles=tuple(impact["profiles"]),
        ingest_policy_changed=impact["ingestPolicyChanged"],
        unknown=tuple(impact["unknown"]),
    )
