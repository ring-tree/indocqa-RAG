"""HTTP 嵌入适配器测试（MockTransport，无外部依赖）。"""

import json

import httpx
import pytest

from app.adapters.embedding.http import (
    OllamaEmbeddingAdapter,
    OpenAICompatibleEmbeddingAdapter,
)
from app.contracts.types import DimensionMismatchError


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ollama_documents_are_prefixed_and_normalized():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/api/embed"
        assert body["model"] == "bge-m3"
        assert body["input"] == ["doc:hello", "doc:world"]
        return httpx.Response(200, json={"embeddings": [[3.0, 4.0], [0.0, 2.0]]})

    adapter = OllamaEmbeddingAdapter(
        "http://ollama.test",
        "bge-m3",
        2,
        doc_prefix="doc:",
        query_prefix="query:",
        client=_client(handler),
    )
    vectors = adapter.embed_documents(["hello", "world"])
    assert vectors == [[0.6, 0.8], [0.0, 1.0]]
    assert adapter.descriptor.provider == "ollama"


def test_ollama_query_uses_query_prefix():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["input"] == ["query:故障"]
        return httpx.Response(200, json={"embeddings": [[1.0, 0.0]]})

    adapter = OllamaEmbeddingAdapter(
        "http://ollama.test",
        "bge-m3",
        2,
        query_prefix="query:",
        client=_client(handler),
    )
    assert adapter.embed_query(["故障"]) == [[1.0, 0.0]]


def test_ollama_rejects_dimension_mismatch():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[1.0, 2.0, 3.0]]})

    adapter = OllamaEmbeddingAdapter("http://ollama.test", "bge-m3", 2, client=_client(handler))
    with pytest.raises(DimensionMismatchError):
        adapter.embed_documents(["x"])


def test_ollama_health_reports_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": []})

    adapter = OllamaEmbeddingAdapter("http://ollama.test", "bge-m3", 2, client=_client(handler))
    assert adapter.health().status.value == "ok"


def test_openai_compatible_sorts_by_index_and_sends_auth():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        assert request.headers["authorization"] == "Bearer sk-test"
        body = json.loads(request.content)
        assert body["input"] == ["doc:a", "doc:b"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 2.0]},
                    {"index": 0, "embedding": [3.0, 4.0]},
                ]
            },
        )

    adapter = OpenAICompatibleEmbeddingAdapter(
        "http://llm.test/v1",
        "sk-test",
        "embed-model",
        2,
        doc_prefix="doc:",
        client=_client(handler),
    )
    assert adapter.embed_documents(["a", "b"]) == [[0.6, 0.8], [0.0, 1.0]]
    assert adapter.descriptor.provider == "openai-compatible"


def test_openai_compatible_health_reports_degraded_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    adapter = OpenAICompatibleEmbeddingAdapter(
        "http://llm.test/v1", "sk-test", "embed-model", 2, client=_client(handler)
    )
    assert adapter.health().status.value == "degraded"
