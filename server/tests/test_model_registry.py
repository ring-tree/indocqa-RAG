"""模型注册表：指纹唯一与 verified 门禁。"""

import pytest

from app.contracts.embedding import EmbeddingModelDescriptor
from app.control.db import ControlPlane
from app.engine.model_registry import ModelNotVerifiedError, ModelRegistry


def _descriptor(runtime: str = "ollama-1") -> EmbeddingModelDescriptor:
    return EmbeddingModelDescriptor(
        provider="ollama",
        model="bge-m3",
        revision="latest",
        dimension=2,
        runtime=runtime,
    )


@pytest.fixture()
def registry(tmp_path):
    plane = ControlPlane(tmp_path / "control.db")
    yield ModelRegistry(plane)
    plane.close()


def test_register_is_idempotent_by_fingerprint(registry):
    first = registry.register(_descriptor())
    second = registry.register(_descriptor())
    assert first == second
    assert len(registry.list_verified()) == 1
    assert registry.require_verified(first)["dimension"] == 2


def test_runtime_change_creates_new_model(registry):
    first = registry.register(_descriptor("ollama-1"))
    second = registry.register(_descriptor("ollama-2"))
    assert first != second
    assert len(registry.list_verified()) == 2


def test_require_verified_rejects_candidate_and_missing(registry):
    model_id = registry.register(_descriptor(), status="candidate")
    with pytest.raises(ModelNotVerifiedError):
        registry.require_verified(model_id)
    with pytest.raises(ModelNotVerifiedError):
        registry.require_verified("missing")
