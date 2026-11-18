"""模型注册表：指纹唯一、verified 门禁。"""

from __future__ import annotations

from app.contracts.embedding import EmbeddingModelDescriptor
from app.contracts.types import ContractError
from app.control.db import ControlPlane


class ModelNotVerifiedError(ContractError):
    """模型未注册或未通过验证，禁止用于 Build。"""


class ModelRegistry:
    def __init__(self, plane: ControlPlane):
        self.plane = plane

    def register(
        self,
        descriptor: EmbeddingModelDescriptor,
        *,
        model_id: str | None = None,
        status: str = "verified",
    ) -> str:
        fingerprint = descriptor.fingerprint()
        existing = self.get_by_fingerprint(fingerprint)
        if existing:
            return existing["id"]
        model_id = model_id or f"emb_{fingerprint[:12]}"
        with self.plane.transaction() as conn:
            conn.execute(
                """
                INSERT INTO embedding_models(
                    id, provider, model, revision, runtime, dimension,
                    fingerprint, status, created_at
                ) VALUES (?,?,?,?,?,?,?,?,datetime('now'))
                """,
                (
                    model_id,
                    descriptor.provider,
                    descriptor.model,
                    descriptor.revision,
                    descriptor.runtime,
                    descriptor.dimension,
                    fingerprint,
                    status,
                ),
            )
        return model_id

    def get(self, model_id: str) -> dict | None:
        row = self.plane.connection.execute(
            "SELECT * FROM embedding_models WHERE id=?", (model_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_by_fingerprint(self, fingerprint: str) -> dict | None:
        row = self.plane.connection.execute(
            "SELECT * FROM embedding_models WHERE fingerprint=?", (fingerprint,)
        ).fetchone()
        return dict(row) if row else None

    def list_verified(self) -> list[dict]:
        rows = self.plane.connection.execute(
            "SELECT * FROM embedding_models WHERE status='verified' ORDER BY created_at"
        ).fetchall()
        return [dict(row) for row in rows]

    def require_verified(self, model_id: str) -> dict:
        record = self.get(model_id)
        if record is None:
            raise ModelNotVerifiedError(f"嵌入模型未注册: {model_id}")
        if record["status"] != "verified":
            raise ModelNotVerifiedError(
                f"嵌入模型未通过验证（status={record['status']}）: {model_id}"
            )
        return record
