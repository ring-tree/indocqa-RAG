"""问答链路 Pack 覆盖层：资产映射与行为等价测试。"""

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
def support_pack():
    clear_overrides()
    loader = PackLoader(SERVER_ROOT / "packs", engine_version="0.1.0")
    loaded = loader.load("customer-service-faq", "1.0.0")
    set_overrides(overrides_from_pack(loaded.pack, content_hash=loaded.content_hash))
    yield get_overrides()
    clear_overrides()


def test_pack_overrides_are_complete(support_pack):
    assert support_pack is not None
    assert len(support_pack.terms_clusters) == 15
    assert "sources.md" in support_pack.skip_files
    assert support_pack.folder_migration["退款"] == "售后支持/退换货"
    assert support_pack.authority_types["政策"] == 1.0
    assert "客服专员" in support_pack.persona_roles
    assert support_pack.ui["examples"]
    assert support_pack.data_fact_units
    assert support_pack.prompt_style.get("tone") == "友好、简洁、先结论后依据"


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


def test_infer_folder_uses_pack(support_pack):
    from app.indexing.folders import folder_from_pack

    assert folder_from_pack("退款政策说明.pdf") == "售后支持/退换货"
    assert folder_from_pack("ordinary.pdf") == "未分类"
    clear_overrides()
    assert folder_from_pack("退款政策说明.pdf") is None


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


def test_support_intent_is_equivalent():
    from app.retrieval.intents import classify

    questions = ["退款失败怎么办", "退款是什么意思", "退款一般多久到账"]
    clear_overrides()
    baseline = [classify(question).intent for question in questions]

    loader = PackLoader(SERVER_ROOT / "packs", engine_version="0.1.0")
    loaded = loader.load("customer-service-faq", "1.0.0")
    set_overrides(overrides_from_pack(loaded.pack, content_hash=loaded.content_hash))
    try:
        assert [classify(question).intent for question in questions] == baseline
    finally:
        clear_overrides()
