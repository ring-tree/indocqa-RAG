"""旧链路 Pack 覆盖层：资产映射与行为等价测试。"""

import pytest

from app.core.config import SERVER_ROOT
from app.core.runtime_overrides import (
    RuntimeOverrides,
    clear_overrides,
    get_overrides,
    set_overrides,
)
from app.packs.legacy_adapter import overrides_from_pack
from app.packs.loader import PackLoader


@pytest.fixture()
def industrial():
    clear_overrides()
    loader = PackLoader(SERVER_ROOT / "packs", engine_version="0.1.0")
    loaded = loader.load("industrial-compressor", "1.0.0")
    set_overrides(overrides_from_pack(loaded.pack))
    yield get_overrides()
    clear_overrides()


def test_pack_overrides_are_complete(industrial):
    assert industrial is not None
    assert len(industrial.terms_clusters) == 34
    assert "sources.md" in industrial.skip_files
    assert industrial.folder_migration["screw"] == "设备手册/螺杆空压机"
    assert industrial.authority_types["手册"] == 1.0
    assert "工程师" in industrial.persona_roles
    assert industrial.ui["examples"]
    assert industrial.data_fact_units
    # 工业 Pack 暂不启用风格覆盖，避免改变 P4 等价行为
    assert industrial.prompt_style.get("tone") == ""


def test_data_facts_and_prompt_style_overrides():
    set_overrides(
        RuntimeOverrides(
            data_fact_units=["兆牛"],
            data_fact_keywords=["刚度"],
            prompt_style={"tone": "严谨", "length": "short", "structure": ["结论", "依据"]},
        )
    )
    try:
        from app.context.templates import KB_V1, _system_block
        from app.core.runtime_overrides import prompt_style_text
        from app.generation.citations import _data_pattern

        assert _data_pattern().search("刚度很高")
        style = prompt_style_text()
        assert "严谨" in style and "结论" in style
        system = _system_block(KB_V1, "term", [], [])
        assert "回答风格" in system and "严谨" in system
        assert system.count(KB_V1.rules[0].text) == 1
    finally:
        clear_overrides()


def test_infer_folder_uses_pack(industrial):
    from app.indexing.folders import folder_from_pack

    assert folder_from_pack("screw_manual.pdf") == "设备手册/螺杆空压机"
    assert folder_from_pack("ordinary.pdf") == "未分类"
    clear_overrides()
    assert folder_from_pack("screw_manual.pdf") is None


def test_classify_uses_pack_keywords():
    set_overrides(RuntimeOverrides(intent_keywords={"term": ["蓝灯"]}))
    try:
        from app.retrieval.intents import classify

        assert classify("蓝灯").intent == "term"
    finally:
        clear_overrides()


def test_clean_title_uses_pack_suffixes():
    set_overrides(RuntimeOverrides(title_cleanup=["用户指南"]))
    try:
        from app.classification.service import _clean_title

        assert _clean_title("设备用户指南.pdf") == "设备"
    finally:
        clear_overrides()


def test_persona_uses_pack_roles():
    set_overrides(RuntimeOverrides(persona_roles=["医生"]))
    try:
        from app.context.persona import extract_persona_signals

        facts = extract_persona_signals("我是首席医生")
        assert any("医生" in str(fact) for fact in facts)
    finally:
        clear_overrides()


def test_industrial_intent_is_equivalent():
    from app.retrieval.intents import classify

    questions = ["E-05 怎么办", "MTBF 是什么意思", "保养周期是多少"]
    clear_overrides()
    baseline = [classify(question).intent for question in questions]

    loader = PackLoader(SERVER_ROOT / "packs", engine_version="0.1.0")
    loaded = loader.load("industrial-compressor", "1.0.0")
    set_overrides(overrides_from_pack(loaded.pack))
    try:
        assert [classify(question).intent for question in questions] == baseline
    finally:
        clear_overrides()
