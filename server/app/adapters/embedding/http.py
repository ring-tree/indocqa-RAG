"""嵌入适配器：Ollama 本地服务与 OpenAI 兼容接口。"""

from __future__ import annotations

import math

import httpx

from app.contracts.embedding import EmbeddingModelDescriptor
from app.contracts.types import (
    AdapterHealth,
    AdapterStatus,
    ContractError,
    DimensionMismatchError,
)


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def _validate(vectors: list[list[float]], expected: int, count: int) -> list[list[float]]:
    if len(vectors) != count:
        raise ContractError(f"嵌入返回数量不匹配: 期望 {count}，实际 {len(vectors)}")
    for vector in vectors:
        if len(vector) != expected:
            raise DimensionMismatchError(
                f"嵌入维度不匹配: 期望 {expected}，实际 {len(vector)}"
            )
    return vectors


class OllamaEmbeddingAdapter:
    def __init__(
        self,
        base_url: str,
        model: str,
        dimension: int,
        *,
        revision: str = "latest",
        runtime: str = "ollama",
        normalize: bool = True,
        query_prefix: str = "",
        doc_prefix: str = "",
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None
        self._descriptor = EmbeddingModelDescriptor(
            provider="ollama",
            model=model,
            revision=revision,
            dimension=dimension,
            runtime=runtime,
            normalize=normalize,
            query_prefix=query_prefix,
            doc_prefix=doc_prefix,
        )

    @property
    def descriptor(self) -> EmbeddingModelDescriptor:
        return self._descriptor

    def _embed(self, texts: list[str], prefix: str) -> list[list[float]]:
        response = self._client.post(
            f"{self.base_url}/api/embed",
            json={"model": self._descriptor.model, "input": [prefix + text for text in texts]},
        )
        response.raise_for_status()
        vectors = response.json().get("embeddings") or []
        vectors = _validate(vectors, self._descriptor.dimension, len(texts))
        return [_normalize(vector) for vector in vectors] if self._descriptor.normalize else vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, self._descriptor.doc_prefix)

    def embed_query(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, self._descriptor.query_prefix)

    def health(self) -> AdapterHealth:
        try:
            response = self._client.get(f"{self.base_url}/api/tags")
            if response.status_code < 400:
                return AdapterHealth(status=AdapterStatus.OK)
            return AdapterHealth(
                status=AdapterStatus.DEGRADED, message=f"HTTP {response.status_code}"
            )
        except httpx.HTTPError as exc:
            return AdapterHealth(status=AdapterStatus.DOWN, message=str(exc))

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


class OpenAICompatibleEmbeddingAdapter:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        dimension: int,
        *,
        revision: str = "latest",
        runtime: str = "openai-compatible",
        normalize: bool = True,
        query_prefix: str = "",
        doc_prefix: str = "",
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None
        self._descriptor = EmbeddingModelDescriptor(
            provider="openai-compatible",
            model=model,
            revision=revision,
            dimension=dimension,
            runtime=runtime,
            normalize=normalize,
            query_prefix=query_prefix,
            doc_prefix=doc_prefix,
        )

    @property
    def descriptor(self) -> EmbeddingModelDescriptor:
        return self._descriptor

    def _embed(self, texts: list[str], prefix: str) -> list[list[float]]:
        response = self._client.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self._descriptor.model, "input": [prefix + text for text in texts]},
        )
        response.raise_for_status()
        payload = response.json().get("data") or []
        ordered = sorted(payload, key=lambda item: item.get("index", 0))
        vectors = [item.get("embedding") or [] for item in ordered]
        vectors = _validate(vectors, self._descriptor.dimension, len(texts))
        return [_normalize(vector) for vector in vectors] if self._descriptor.normalize else vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, self._descriptor.doc_prefix)

    def embed_query(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, self._descriptor.query_prefix)

    def health(self) -> AdapterHealth:
        try:
            response = self._client.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            if response.status_code < 400:
                return AdapterHealth(status=AdapterStatus.OK)
            return AdapterHealth(
                status=AdapterStatus.DEGRADED, message=f"HTTP {response.status_code}"
            )
        except httpx.HTTPError as exc:
            return AdapterHealth(status=AdapterStatus.DOWN, message=str(exc))

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
