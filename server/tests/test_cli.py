"""CLI：init / validate / build / query。"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import cli
from app.adapters.keyword.bm25 import Bm25KeywordAdapter
from app.adapters.object_store.local import LocalObjectStore
from app.adapters.vector.chroma import ChromaVectorAdapter
from app.contracts.embedding import EmbeddingModelDescriptor
from app.contracts.types import AdapterHealth, AdapterStatus
from app.control.db import ControlPlane
from app.core.config import get_settings
from app.engine.pipeline import EnginePipeline
from app.engine.runtime import reset_runtime


class FakeEmbedder:
    descriptor = EmbeddingModelDescriptor(
        provider="fake",
        model="fake-2d",
        revision="1",
        dimension=2,
        runtime="test",
    )

    def embed_documents(self, texts):
        return [[1.0, 0.0] if "智能网关" in text else [0.0, 1.0] for text in texts]

    def embed_query(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def health(self):
        return AdapterHealth(status=AdapterStatus.OK)


@pytest.fixture(autouse=True)
def _restore_cli_env(monkeypatch):
    for key in ("INDOCQA_ENGINE_DATA_DIR", "INDOCQA_PACKS_DIR"):
        monkeypatch.delenv(key, raising=False)
    yield
    get_settings.cache_clear()
    reset_runtime()


def test_init_validate_and_invalid(tmp_path, capsys):
    project = tmp_path / "proj"
    assert cli.main(["init", "--dir", str(project), "--pack", "customer-service-faq@1.0.0"]) == 0
    config = json.loads((project / "indocqa.json").read_text(encoding="utf-8"))
    assert config["kbId"] == "kb_default"
    assert config["pack"] == "customer-service-faq@1.0.0"
    assert (project / "data" / "engine" / "control.db").exists()
    assert (project / "data" / "corpus").is_dir()
    assert cli.main(["validate", "--dir", str(project)]) == 0

    config["pack"] = "missing-pack@1.0.0"
    (project / "indocqa.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    assert cli.main(["validate", "--dir", str(project)]) == 1
    assert "Pack 不可用" in capsys.readouterr().err

    assert cli.main(["init", "--dir", str(project)]) == 1


def test_build_and_query_with_fake_runtime(tmp_path, monkeypatch, capsys):
    project = tmp_path / "proj"
    assert cli.main(["init", "--dir", str(project)]) == 0
    corpus = project / "data" / "corpus"
    (corpus / "faq.md").write_text(
        "# 智能网关维护\n\n智能网关滤芯更换周期为 4000 小时。\n",
        encoding="utf-8",
    )

    def fake_runtime(settings):
        base = Path(settings.engine_data_dir)
        plane = ControlPlane(base / "control.db")
        engine = EnginePipeline(
            plane,
            FakeEmbedder(),
            ChromaVectorAdapter(base / "chroma"),
            Bm25KeywordAdapter(base / "bm25"),
        )
        return SimpleNamespace(
            plane=plane,
            engine=engine,
            object_store=LocalObjectStore(base / "objects"),
            pack_registry=None,
        )

    monkeypatch.setattr(cli, "_make_runtime", fake_runtime)
    assert cli.main(["build", "--dir", str(project)]) == 0
    build_output = capsys.readouterr().out
    assert "构建完成" in build_output
    assert "documents=1" in build_output

    assert cli.main(["query", "--dir", str(project), "智能网关"]) == 0
    query_output = capsys.readouterr().out
    assert "智能网关" in query_output
    assert "faq.md" in query_output

    assert cli.main(["query", "--dir", str(project), "--json", "智能网关"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["hits"] and payload["hits"][0]["filename"] == "faq.md"
