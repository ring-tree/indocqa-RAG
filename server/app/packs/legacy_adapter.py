"""把 Domain Pack 转为问答链路的运行期覆盖配置。"""

from __future__ import annotations

from app.contracts.pack import DomainPack
from app.core.runtime_overrides import RuntimeOverrides, set_overrides


def enable_legacy_pack(pack_ref: str, packs_dir: str, engine_version: str) -> str:
    """加载并启用问答链路的 Domain Pack（`id@version`），返回实际生效版本。"""
    from app.packs.loader import PackLoader

    pack_id, _, version = pack_ref.partition("@")
    loaded = PackLoader(packs_dir, engine_version=engine_version).load(
        pack_id, version or "1.0.0"
    )
    set_overrides(overrides_from_pack(loaded.pack, content_hash=loaded.content_hash))
    return f"{loaded.pack.manifest.id}@{loaded.pack.manifest.version}"


def overrides_from_pack(pack: DomainPack, *, content_hash: str = "") -> RuntimeOverrides:
    return RuntimeOverrides(
        pack_id=pack.manifest.id,
        pack_version=pack.manifest.version,
        pack_hash=content_hash,
        intent_keywords={
            key: list(values) for key, values in pack.intent.keywords.items()
        },
        intent_connectors=list(pack.intent.connectors),
        intent_exceptions=list(pack.intent.exceptions),
        reference_code_pattern=pack.intent.reference_code_pattern,
        classification_rules=[
            {
                "pattern": rule.pattern,
                "folder": rule.folder,
                "reason": f"Pack 分类规则（priority={rule.priority}）",
            }
            for rule in pack.classification.filename_rules
        ],
        document_types=list(pack.classification.document_types),
        title_cleanup=list(pack.classification.title_cleanup),
        folder_default=pack.folders.default,
        folder_migration=dict(pack.folders.migration),
        skip_files=list(pack.parser.skip_files),
        terms_clusters={
            key: list(values) for key, values in pack.terms.clusters.items()
        },
        data_fact_units=list(pack.data_facts.units),
        data_fact_keywords=list(pack.data_facts.keywords),
        authority_types=dict(pack.authority.document_types),
        persona_roles=list(pack.persona.roles),
        prompt_style=pack.prompts.model_dump(),
        ui=pack.ui.model_dump(),
    )
