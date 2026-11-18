"""领域词静态检查（T-P4-16）：工业词汇只能出现在 Pack 资产中。

引擎与前端示例必须保持领域中立；工业术语、目录模板和语料名称由
`server/packs/<id>/<version>/` 提供，不得回流到 `server/app` 或 `web/src`。
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
ENGINE_DIR = ROOT / "server" / "app"
WEB_DIR = ROOT / "web" / "src"
SUPPORT_PACK_DIR = ROOT / "server" / "packs" / "customer-service-faq"

CHINESE_TERMS = (
    "空压机",
    "螺杆",
    "压缩机",
    "储气罐",
    "润滑油",
    "机油",
    "排气压力",
    "油分芯",
    "空气过滤器",
    "设备手册",
    "行业标准",
    "安全规程",
    "评测夹具",
    "维保",
    "机组",
    "故障码",
)
LATIN_TERMS = ("MTBF", "LUB", "PSIG", "SCFM", "CFM", "E-05")
SUFFIXES = {".py", ".ts", ".tsx", ".css"}
SKIP_DIRS = {"__pycache__"}


def _scan(base: pathlib.Path) -> list[str]:
    hits: list[str] = []
    for path in base.rglob("*"):
        if not path.is_file() or path.suffix not in SUFFIXES:
            continue
        if SKIP_DIRS & set(path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        relative = path.relative_to(ROOT).as_posix()
        for term in CHINESE_TERMS:
            if term in text:
                hits.append(f"{relative}: {term}")
        for term in LATIN_TERMS:
            if term in text:
                hits.append(f"{relative}: {term}")
    return hits


def test_engine_has_no_industrial_vocabulary():
    hits = _scan(ENGINE_DIR)
    assert not hits, "引擎目录出现工业领域词：\n" + "\n".join(hits)


def test_web_has_no_industrial_vocabulary():
    hits = _scan(WEB_DIR)
    assert not hits, "前端目录出现工业领域词：\n" + "\n".join(hits)


def test_customer_service_pack_has_no_industrial_vocabulary():
    """第二领域 Pack 不得复用工业术语、目录或语料名称。"""
    hits = _scan(SUPPORT_PACK_DIR)
    assert not hits, "客服 Pack 出现工业领域词：\n" + "\n".join(hits)
