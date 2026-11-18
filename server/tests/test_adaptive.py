"""自适应检索阶段测试（T2.2 / T2.3 / T2.4 / T2.5）：意图、术语归一、路由、多跳。"""

import pathlib

import pytest

from app.core.config import get_settings
from app.indexing import store
from app.indexing.ingest import ingest_file
from app.retrieval.intents import classify
from app.retrieval.searcher import _code_bonus
from app.retrieval.terms import build_clusters, expand_query, load_clusters

CORPUS = pathlib.Path(__file__).resolve().parents[2] / "data" / "corpus"
FIXTURE = CORPUS / "fixture_术语混写_示例手册.md"


@pytest.fixture()
def tmp_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("INDOCQA_CHROMA_DIR", str(tmp_path / "chroma"))
    monkeypatch.setenv("INDOCQA_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("INDOCQA_EMBED_BACKEND", "local")
    terms_dir = tmp_path / "terms"
    terms_dir.mkdir()
    monkeypatch.setenv("INDOCQA_TERMS_FILE", str(terms_dir / "terms.json"))
    get_settings.cache_clear()
    store._client.cache_clear()
    ingest_file(FIXTURE)
    yield get_settings()
    get_settings.cache_clear()
    store._client.cache_clear()


# ---- 意图分类（T2.3）----


def test_intent_classification_five_types(domain_pack):
    assert classify("退款是什么意思？").intent == "term"
    assert classify("退款一般多久到账？").intent == "param"
    assert classify("收不到货怎么办？").intent == "fault"
    assert classify("如何申请退货？").intent == "procedure"
    assert classify("退款和换货的区别分别是什么？").intent == "multihop"


def test_intent_chitchat_and_general():
    assert classify("你好").intent == "chitchat"
    assert classify("介绍一下会员权益的常见类型").intent == "general"


def test_code_bonus_matches_dash_alias():
    assert _code_bonus("报错 E-05 怎么处理", "故障代码 E05：设备温度过高") > 0
    assert _code_bonus("报错 E-05 怎么处理", "与故障码无关的段落") == 0
    assert _code_bonus("普通问题", "E-05 故障代码") == 0


# ---- 术语归一（T2.2）----


def test_clusters_and_query_expansion(tmp_settings, domain_pack):
    clusters = load_clusters(tmp_settings)
    assert len(clusters) >= 15, f"簇数量不足: {len(clusters)}"
    expanded, used = expand_query("退款是什么意思", clusters)
    assert "退货退款" in expanded
    assert used


def test_corpus_extraction_merges_into_clusters(tmp_settings, domain_pack):
    clusters = build_clusters(tmp_settings)
    flt = [c for c in clusters if "FLT" in c]
    assert flt, "语料抽取未并入 FLT 簇"
    assert "滤网" in flt[0]


@pytest.mark.asyncio
async def test_fault_query_keeps_step_chunk_from_same_document(tmp_settings):
    from app.graph.graph import run_retrieval

    result = await run_retrieval(
        "智能网关报错代码 E-05 应该怎么处理？请给出 S-12 的具体操作步骤。",
        "adaptive",
        tmp_settings,
    )
    assert result["intent"] == "fault"
    assert any("立即停机" in hit.text for hit in result["hits"])


def test_expansion_keeps_question_prefix(tmp_settings):
    clusters = load_clusters(tmp_settings)
    expanded, _ = expand_query("E05 故障是什么含义", clusters)
    assert expanded.startswith("E05 故障是什么含义")


# ---- 路由与多跳（T2.3 / T2.4，LangGraph 状态图）----


@pytest.mark.asyncio
async def test_adaptive_vs_fixed_plan_difference(tmp_settings):
    from app.graph.graph import run_retrieval

    q = "MTBF 和平均无故障时间是同一个指标吗？额定值是多少？"
    adaptive = await run_retrieval(q, "adaptive", tmp_settings)
    fixed = await run_retrieval(q, "fixed", tmp_settings)
    assert adaptive["plan"]["label"].startswith("adaptive")
    assert fixed["plan"]["label"].startswith("fixed")
    assert fixed["plan"]["expand"] is False and fixed["plan"]["multihop"] is False
    assert adaptive["hits"], "adaptive 无命中"


@pytest.mark.asyncio
async def test_multihop_trace_records_hops(tmp_settings):
    from app.graph.graph import run_retrieval

    q = "MTBF 和 FLT 的定义分别是什么？"
    result = await run_retrieval(q, "adaptive", tmp_settings)
    stages = [s["stage"] for s in result["trace"]]
    assert "classify" in stages and "retrieve" in stages and "decide" in stages
    decide_steps = [s for s in result["trace"] if s["stage"] == "decide"]
    hops = sum(1 for s in result["trace"] if s["stage"] == "retrieve") - 1
    assert hops <= 2, "多跳超过 2 跳上限"
    assert decide_steps, "缺少多跳决策记录"


@pytest.mark.asyncio
async def test_param_intent_boosts_table(tmp_settings):
    from app.graph.graph import run_retrieval

    result = await run_retrieval("FLT 更换的保养周期是多少小时？", "adaptive", tmp_settings)
    assert result["plan"].get("boost_table") is True
    assert any(h.kind == "table" for h in result["hits"][:3]), "参数查询未提升表格块"


@pytest.mark.asyncio
async def test_term_expansion_ablation_switch(tmp_settings, monkeypatch, domain_pack):
    from app.graph.graph import run_retrieval

    q = "FLT 是什么"
    on = await run_retrieval(q, "adaptive", tmp_settings)
    monkeypatch.setenv("INDOCQA_TERM_EXPANSION_ENABLED", "0")
    get_settings.cache_clear()
    off = await run_retrieval(q, "adaptive", tmp_settings)
    first_hop = next(s for s in off["trace"] if s["stage"] == "retrieve")
    assert first_hop["expansions"] == [], "消融开关未生效"
    on_first = next(s for s in on["trace"] if s["stage"] == "retrieve")
    assert on_first["expansions"], "基线应存在术语扩展"


# ---- 置信度三信号（T2.5，启发式部分）----


def test_citation_coverage_downgrades():
    from app.graph.pipeline import _citation_coverage, _downgrade

    coverage, valid = _citation_coverage("MTBF 是可靠性指标 [1] [2]", [object(), object()])
    assert valid and coverage == 1.0
    coverage, valid = _citation_coverage("无引用回答", [object()])
    assert not valid and coverage == 0.0
    assert _downgrade("high") == "mid" and _downgrade("mid") == "low"
