"""批量移动/撤销与 AI 分类：操作日志、撤销有效期、无模型门禁、规则候选。"""

import pathlib

import pytest
from fastapi.testclient import TestClient

from app.classification import service as classify_service
from app.core.config import get_settings
from app.core.exceptions import AIUnavailableError, ParamError
from app.indexing import store
from app.indexing.ingest import ingest_file
from app.indexing.manifest import Manifest
from app.indexing.moves import batch_move, undo_move
from app.indexing.preview import preview_document
from app.main import create_app

CORPUS = pathlib.Path(__file__).resolve().parents[2] / "data" / "corpus"
FIXTURE = CORPUS / "fixture_术语混写_示例手册.md"


@pytest.fixture()
def tmp_settings(tmp_path, monkeypatch):
    terms = tmp_path / "terms"
    terms.mkdir()
    monkeypatch.setenv("INDOCQA_CHROMA_DIR", str(tmp_path / "chroma"))
    monkeypatch.setenv("INDOCQA_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("INDOCQA_SESSION_DB", str(tmp_path / "sessions.db"))
    monkeypatch.setenv("INDOCQA_EMBED_BACKEND", "local")
    monkeypatch.setenv("INDOCQA_TERMS_FILE", str(terms / "terms.json"))
    get_settings.cache_clear()
    store._client.cache_clear()
    yield get_settings()
    get_settings.cache_clear()
    store._client.cache_clear()


def _md(path: pathlib.Path, title: str, body: str) -> pathlib.Path:
    path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
    return path


def _two_docs(settings, tmp_path):
    a = _md(tmp_path / "a.md", "智能网关维护", "FLT 更换周期 4000 小时。")
    b = _md(tmp_path / "b.md", "可靠性指标", "MTBF 8000 小时。")
    first = ingest_file(a, settings, folder="售后支持/退换货")
    second = ingest_file(b, settings, folder="评测夹具")
    return first["docId"], second["docId"]


def test_batch_move_and_undo(tmp_settings, tmp_path):
    first, second = _two_docs(tmp_settings, tmp_path)
    result = batch_move(tmp_settings, [first, second], "物流配送/转运中心")
    assert sorted(result["moved"]) == sorted([first, second])
    assert result["operationId"]
    manifest = Manifest(Manifest.path_for(tmp_settings))
    assert manifest.get(first)["folder"] == "物流配送/转运中心"
    assert manifest.get(second)["folder"] == "物流配送/转运中心"

    undo = undo_move(tmp_settings, result["operationId"])
    assert sorted(undo["restored"]) == sorted([first, second])
    manifest = Manifest(Manifest.path_for(tmp_settings))
    assert manifest.get(first)["folder"] == "售后支持/退换货"
    assert manifest.get(second)["folder"] == "评测夹具"

    with pytest.raises(ParamError):
        undo_move(tmp_settings, result["operationId"])


def test_batch_move_skips_same_folder_and_reports_missing(tmp_settings, tmp_path):
    first, _ = _two_docs(tmp_settings, tmp_path)
    result = batch_move(tmp_settings, [first, "missing"], "售后支持/退换货")
    assert result["skipped"] == [first]
    assert result["failed"][0]["docId"] == "missing"
    assert result["operationId"] is None


def test_move_api_creates_undo_entry(tmp_settings, tmp_path):
    first, _ = _two_docs(tmp_settings, tmp_path)
    client = TestClient(create_app())
    response = client.post(
        "/api/kb/documents/batch-move",
        json={"docIds": [first], "folder": "物流配送/转运中心"},
    )
    assert response.status_code == 200
    operation_id = response.json()["data"]["operationId"]
    assert operation_id

    undo = client.post(f"/api/kb/moves/{operation_id}/undo")
    assert undo.status_code == 200
    assert undo.json()["data"]["restored"] == [first]


def test_classify_gate_without_model(tmp_settings, monkeypatch):
    for var in (
        "INDOCQA_LLM_BASE_URL",
        "INDOCQA_LLM_API_KEY",
        "INDOCQA_LLM_MODEL",
    ):
        monkeypatch.setenv(var, "")
    get_settings.cache_clear()
    fresh = get_settings()

    with pytest.raises(AIUnavailableError):
        __import__("asyncio").run(
            classify_service.classify_text(fresh, "a.pdf", "智能网关维护手册")
        )

    client = TestClient(create_app())
    response = client.post("/api/kb/classify", json={"docId": "any"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == 40904
    assert response.json()["error"]["message"] == "无可用模型"


def test_preview_classification_reports_unavailable_without_model(tmp_settings, monkeypatch):
    for var in (
        "INDOCQA_LLM_BASE_URL",
        "INDOCQA_LLM_API_KEY",
        "INDOCQA_LLM_MODEL",
    ):
        monkeypatch.setenv(var, "")
    get_settings.cache_clear()
    report = preview_document(FIXTURE, get_settings(), classify=True)
    assert report["classification"]["available"] is False
    assert report["classification"]["reason"] == "no_model"


def test_rule_candidates_prefer_matching_folder(domain_pack, tmp_settings):
    candidates = classify_service._build_candidates(
        ["售后支持/退换货", "物流配送/发货与签收", "评测夹具"],
        "退款政策说明.pdf",
        "退款与换货政策说明",
        {},
        {},
    )
    assert candidates[0]["path"] == "售后支持/退换货"
    assert candidates[0]["confidence"] >= 0.9
    assert "分类规则" in candidates[0]["reason"]
