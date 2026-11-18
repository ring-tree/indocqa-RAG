"""配置合并、影响分析与 Domain Pack 校验测试。"""

import json

import pytest
from pydantic import ValidationError

from app.config.models import AnswerConfig, KBConfig
from app.config.schema_export import export_schemas
from app.contracts.config import (
    ConfigLayer,
    ConfigLockError,
    RebuildMode,
    analyze_impact,
    merge_layers,
)
from app.contracts.pack import DomainPack


def test_merge_priority_and_source_tracking():
    merged = merge_layers(
        {
            ConfigLayer.ENGINE: {"nested": {"x": 1}, "engine": {"safety": True}},
            ConfigLayer.LANGUAGE: {"nested": {"x": 2}},
            ConfigLayer.PACK: {"nested": {"x": 3, "y": 4}},
            ConfigLayer.KB: {"nested": {"x": 5}},
        }
    )
    assert merged.get("nested.x") == 5
    assert merged.layer_of("nested.x") == ConfigLayer.KB
    assert merged.get("nested.y") == 4
    assert merged.layer_of("nested.y") == ConfigLayer.PACK
    assert merged.values["engine.safety"].locked is True
    assert merged.sources()["nested.x"] == "kb"


def test_engine_locked_field_cannot_be_overridden():
    with pytest.raises(ConfigLockError):
        merge_layers(
            {
                ConfigLayer.ENGINE: {"engine": {"safety": True}},
                ConfigLayer.KB: {"engine": {"safety": False}},
            }
        )


@pytest.mark.parametrize(
    ("paths", "mode", "requires_build", "profiles"),
    [
        (["embedding.model"], RebuildMode.EMBEDDING_ONLY, True, ()),
        (["pipeline.version"], RebuildMode.FULL, True, ()),
        (["retrieval.preset"], RebuildMode.CONFIG_ONLY, True, ()),
        (["prompts.tone"], RebuildMode.METADATA_ONLY, False, ("answer",)),
        (["ui.examples"], RebuildMode.METADATA_ONLY, False, ("ui",)),
        (["embedding.model", "pipeline.version"], RebuildMode.FULL, True, ()),
    ],
)
def test_analyze_impact(paths, mode, requires_build, profiles):
    impact = analyze_impact(paths)
    assert impact.rebuild_mode == mode
    assert impact.requires_new_build is requires_build
    assert impact.profiles == profiles


def test_analyze_impact_reports_unknown_fields():
    impact = analyze_impact(["unknown.field"])
    assert impact.unknown == ("unknown.field",)
    assert impact.rebuild_mode is None
    assert impact.requires_new_build is False


def _minimal_pack() -> dict:
    return {
        "manifest": {
            "id": "customer-service-faq",
            "version": "1.0.0",
            "domain": "customer-service",
        }
    }


def test_minimal_pack_is_valid():
    pack = DomainPack.model_validate(_minimal_pack())
    assert pack.manifest.id == "customer-service-faq"
    assert pack.folders.default == "未分类"


def test_pack_rejects_unknown_fields():
    payload = _minimal_pack()
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        DomainPack.model_validate(payload)


def test_pack_rejects_invalid_manifest():
    payload = _minimal_pack()
    payload["manifest"]["id"] = "Invalid ID"
    with pytest.raises(ValidationError):
        DomainPack.model_validate(payload)


def test_pack_rejects_disallowed_intent_keys():
    payload = _minimal_pack()
    payload["intent"] = {"keywords": {"multihop": ["a", "b"]}}
    with pytest.raises(ValidationError):
        DomainPack.model_validate(payload)


def test_pack_rejects_invalid_regex():
    payload = _minimal_pack()
    payload["classification"] = {"filename_rules": [{"pattern": "([", "folder": "x"}]}
    with pytest.raises(ValidationError):
        DomainPack.model_validate(payload)


def test_export_schemas_writes_five_files(tmp_path):
    written = export_schemas(tmp_path)
    assert len(written) == 5
    names = {path.name for path in written}
    assert names == {
        "engine.schema.json",
        "kb.schema.json",
        "answer.schema.json",
        "ui.schema.json",
        "pack.schema.json",
    }
    for path in written:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data.get("type") == "object"
        assert data.get("properties")


def test_profile_configs_reject_invalid_values():
    with pytest.raises(ValidationError):
        KBConfig(preset="unknown")
    with pytest.raises(ValidationError):
        AnswerConfig(answer_length="huge")
    with pytest.raises(ValidationError):
        KBConfig(unknown_field=True)

