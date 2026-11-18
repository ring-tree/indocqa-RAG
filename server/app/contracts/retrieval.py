"""检索边界契约：固定阶段、三档预设与预算计算。

算法与阶段顺序不可配置；预设只允许调整 topK 倍率与阈值偏移（D29–D32）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RetrievalStage(str, Enum):
    QUERY_NORMALIZE = "query_normalize"
    INTENT = "intent"
    QUERY_REWRITE = "query_rewrite"
    HYBRID_RECALL = "hybrid_recall"
    RRF_FUSION = "rrf_fusion"
    EXACT_BOOST = "exact_boost"
    TABLE_ANCHOR = "table_anchor"
    DOC_SEAT = "doc_seat"
    MULTIHOP = "multihop"
    BOUNDARY = "boundary"
    CONTEXT = "context"
    GENERATE = "generate"


STAGE_ORDER: tuple[RetrievalStage, ...] = (
    RetrievalStage.QUERY_NORMALIZE,
    RetrievalStage.INTENT,
    RetrievalStage.QUERY_REWRITE,
    RetrievalStage.HYBRID_RECALL,
    RetrievalStage.RRF_FUSION,
    RetrievalStage.EXACT_BOOST,
    RetrievalStage.TABLE_ANCHOR,
    RetrievalStage.DOC_SEAT,
    RetrievalStage.MULTIHOP,
    RetrievalStage.BOUNDARY,
    RetrievalStage.CONTEXT,
    RetrievalStage.GENERATE,
)


@dataclass(frozen=True)
class RetrievalPresetSpec:
    id: str
    top_k_multiplier: float
    threshold_offset: float
    min_top_k: int = 4
    max_top_k: int = 20


PRECISION = RetrievalPresetSpec("precision@1", 0.75, 0.03)
BALANCED = RetrievalPresetSpec("balanced@1", 1.0, 0.0)
RECALL = RetrievalPresetSpec("recall@1", 1.5, -0.03)

PRESETS: dict[str, RetrievalPresetSpec] = {item.id: item for item in (PRECISION, BALANCED, RECALL)}
_PRESET_ALIASES = {
    "precision": PRECISION.id,
    "balanced": BALANCED.id,
    "recall": RECALL.id,
}


def normalize_preset(preset: str | None) -> RetrievalPresetSpec:
    """接受 precision@1 / precision 两种写法；未知值回退 balanced。"""
    if not preset:
        return BALANCED
    key = preset.strip()
    key = _PRESET_ALIASES.get(key, key)
    if key not in PRESETS:
        raise ValueError(f"未知检索预设: {preset}")
    return PRESETS[key]


def effective_top_k(
    base_top_k: int,
    preset: str | RetrievalPresetSpec | None = None,
    max_top_k: int | None = None,
) -> int:
    """按预设倍率计算实际 topK，并受框架边界与适配器上限约束。"""
    spec = preset if isinstance(preset, RetrievalPresetSpec) else normalize_preset(preset)
    raw = round(base_top_k * spec.top_k_multiplier)
    clamped = max(spec.min_top_k, min(spec.max_top_k, raw))
    if max_top_k is not None:
        clamped = min(clamped, max_top_k)
    return max(1, clamped)


def effective_threshold(
    model_threshold: float,
    preset: str | RetrievalPresetSpec | None = None,
    lower: float = 0.20,
    upper: float = 0.80,
) -> float:
    """模型校准阈值 + 预设偏移，并夹紧到安全区间。"""
    spec = preset if isinstance(preset, RetrievalPresetSpec) else normalize_preset(preset)
    return max(lower, min(upper, model_threshold + spec.threshold_offset))
