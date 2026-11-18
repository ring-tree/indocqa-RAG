"""问题意图分类：规则优先（可解释、零延迟），供路由表选择检索策略。

类别：term 术语解释 / param 参数查询 / procedure 操作步骤 / fault 故障诊断
     / multihop 多跳关联 / chitchat 寒暄 / general 一般
"""

import re
import unicodedata
from dataclasses import dataclass

from app.core.runtime_overrides import get_overrides

_CHITCHAT = re.compile(
    r"^(你好|您好|hi|hello|嗨|谢谢|感谢|在吗|再见|早上好|下午好|晚上好)\b", re.IGNORECASE
)
_FAULT = re.compile(
    r"报错|故障|错误码|错误代码|异常|报警|警报|掉线|断连|断线|无法连接|无法访问|失败|超时|"
    r"死机|卡死|闪退|fault code|error code",
    re.IGNORECASE,
)
_FAULT_CODE = re.compile(r"\b[Ee][-]?\d{2,3}\b")
_PARAM = re.compile(
    r"多少|多久|额定|参数表?|周期|规格|范围|容量|流量|压力值|温度值|功率是|尺寸|重量|电压|电流|几|"
    r"控制|保持|允许|上限|下限|等级"
)
_TERM = re.compile(
    r"是什么|什么意思|什么含义|什么定义|定义是|指什么|指的是|全称|缩写|同一|一回事|一样吗|等同|区别|difference|what is|what does.*mean",
    re.IGNORECASE,
)
_PROCEDURE = re.compile(
    r"怎么|如何|步骤|操作流程|操作方法|处理|处置|怎么办|怎么排查|怎么处理|维护流程|"
    r"方法|流程|排查|维护|保养|更换|安装|调试|启停|操作|巡检|点检|记录|how to|how do",
    re.IGNORECASE,
)
_ENTITY = re.compile(r"[A-Za-z][A-Za-z0-9.\-]{1,14}")
_CONNECTIVE = re.compile(r"和|与|跟|以及|分别|两者|两个都|都是")
_STOPWORDS = {
    "what",
    "is",
    "the",
    "a",
    "an",
    "of",
    "in",
    "for",
    "and",
    "how",
    "to",
    "do",
    "does",
    "mean",
    "are",
    "同一",
}
# “平均无故障时间”等可靠性术语中的“故障”不是故障诊断意图，匹配前剔除
_NO_FAULT_TERM = re.compile(r"(平均)?无故障(运行)?时间?")


@dataclass
class IntentResult:
    intent: str
    entities: list[str]


def entities_of(question: str) -> list[str]:
    """提取问题中的实体信号（拉丁缩写/型号/编码），多跳判断与子查询生成共用。"""
    seen = []
    for tok in _ENTITY.findall(question):
        low = tok.lower().rstrip(".-")
        if len(low) >= 2 and low not in _STOPWORDS and low not in seen:
            seen.append(low)
    return seen


def _merged(base: re.Pattern, terms: list[str]) -> re.Pattern:
    values = [term for term in terms if term]
    if not values:
        return base
    extra = "|".join(re.escape(term) for term in values)
    return re.compile(f"(?:{base.pattern})|(?:{extra})", base.flags)


def _patterns() -> tuple[re.Pattern, re.Pattern, re.Pattern, re.Pattern, re.Pattern]:
    overrides = get_overrides()
    if overrides is None:
        return _FAULT, _PARAM, _TERM, _PROCEDURE, _CONNECTIVE
    keywords = overrides.intent_keywords
    return (
        _merged(_FAULT, keywords.get("fault", [])),
        _merged(_PARAM, keywords.get("param", [])),
        _merged(_TERM, keywords.get("term", [])),
        _merged(_PROCEDURE, keywords.get("procedure", [])),
        _merged(_CONNECTIVE, overrides.intent_connectors),
    )


def _fault_code_pattern() -> re.Pattern:
    overrides = get_overrides()
    if overrides and overrides.reference_code_pattern:
        return re.compile(overrides.reference_code_pattern)
    return _FAULT_CODE


def _strip_exceptions(text: str) -> str:
    overrides = get_overrides()
    result = _NO_FAULT_TERM.sub("", text)
    if overrides:
        for term in overrides.intent_exceptions:
            if term:
                result = result.replace(term, "")
    return result


def classify(question: str) -> IntentResult:
    # 全角字母/连字符/数字归一（Ａ－１２ → A-12），再进入规则与检索
    q = unicodedata.normalize("NFKC", question).strip()
    entities = entities_of(q)
    fault_re, param_re, term_re, procedure_re, connective_re = _patterns()
    if len(q) <= 20 and _CHITCHAT.search(q):
        return IntentResult("chitchat", entities)
    fault_text = _strip_exceptions(q)
    # 多实体对比优先于故障意图：避免“两个编号的区别”被单一故障规则截走
    if connective_re.search(q) and len(entities) >= 2:
        return IntentResult("multihop", entities)
    if fault_re.search(fault_text) or _fault_code_pattern().search(q):
        return IntentResult("fault", entities)
    # 多跳：连接词 + ≥2 实体，或“对比型术语问题”
    if term_re.search(q) and connective_re.search(q):
        return IntentResult("multihop", entities)
    if param_re.search(q):
        return IntentResult("param", entities)
    if term_re.search(q):
        return IntentResult("term", entities)
    if procedure_re.search(q):
        return IntentResult("procedure", entities)
    return IntentResult("general", entities)
