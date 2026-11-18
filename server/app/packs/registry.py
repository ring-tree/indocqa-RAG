"""Pack 注册表：安装、升级、卸载与 KB 绑定。"""

from __future__ import annotations

from app.contracts.types import ContractError
from app.control.db import ControlPlane
from app.packs.loader import (
    LoadedPack,
    PackIncompatible,
    PackInvalid,
    PackLoader,
    version_satisfies,
)


class PackInUseError(ContractError):
    """Pack 仍被知识库绑定，不能卸载。"""


class PackNotInstalled(ContractError):
    """Pack 未安装。"""


class PackRegistry:
    def __init__(self, plane: ControlPlane, loader: PackLoader, *, engine_version: str):
        self.plane = plane
        self.loader = loader
        self.engine_version = engine_version

    def install(self, pack_id: str, version: str) -> dict:
        loaded = self.loader.load(pack_id, version)
        if not version_satisfies(self.engine_version, loaded.pack.manifest.engine_range):
            raise PackIncompatible("Pack 与当前引擎版本不兼容")
        existing = self.get(pack_id, version)
        if existing:
            if existing["content_hash"] != loaded.content_hash:
                raise PackInvalid("同一 id+version 的 Pack 内容不可变")
            return existing
        with self.plane.transaction() as conn:
            conn.execute(
                "INSERT INTO domain_packs(id, version, content_hash, engine_range, status, "
                "installed_at) VALUES (?,?,?,?,'installed',datetime('now'))",
                (
                    pack_id,
                    version,
                    loaded.content_hash,
                    loaded.pack.manifest.engine_range,
                ),
            )
        self.plane.write_audit(
            "pack.install",
            "pack",
            f"{pack_id}@{version}",
            detail={"contentHash": loaded.content_hash},
        )
        return self.get(pack_id, version) or {}

    def get(self, pack_id: str, version: str) -> dict | None:
        row = self.plane.connection.execute(
            "SELECT * FROM domain_packs WHERE id=? AND version=?", (pack_id, version)
        ).fetchone()
        return dict(row) if row else None

    def list_installed(self) -> list[dict]:
        rows = self.plane.connection.execute(
            "SELECT * FROM domain_packs ORDER BY id, version"
        ).fetchall()
        return [dict(row) for row in rows]

    def uninstall(self, pack_id: str, version: str) -> None:
        if self.get(pack_id, version) is None:
            raise PackNotInstalled(f"Pack 未安装: {pack_id}@{version}")
        bindings = self.plane.list_kb_bindings(pack_id, version)
        if bindings:
            raise PackInUseError(f"Pack 仍被知识库绑定: {', '.join(bindings)}")
        with self.plane.transaction() as conn:
            conn.execute(
                "DELETE FROM domain_packs WHERE id=? AND version=?", (pack_id, version)
            )
        self.plane.write_audit(
            "pack.uninstall", "pack", f"{pack_id}@{version}"
        )

    def bind_kb(self, kb_id: str, pack_id: str, version: str) -> dict:
        record = self.get(pack_id, version)
        if record is None:
            raise PackNotInstalled(f"Pack 未安装: {pack_id}@{version}")
        if not version_satisfies(self.engine_version, record["engine_range"]):
            raise PackIncompatible("Pack 与当前引擎版本不兼容")
        if self.plane.get_kb(kb_id) is None:
            raise PackInvalid(f"知识库不存在: {kb_id}")
        self.plane.bind_pack(kb_id, pack_id, version)
        self.plane.write_audit(
            "kb.pack.bind", "knowledge_base", kb_id, kb_id=kb_id,
            detail={"packId": pack_id, "version": version},
        )
        return {"kbId": kb_id, "packId": pack_id, "version": version}

    def unbind_kb(self, kb_id: str) -> None:
        self.plane.unbind_pack(kb_id)
        self.plane.write_audit("kb.pack.unbind", "knowledge_base", kb_id, kb_id=kb_id)

    def load_for_build(self, pack_id: str, version: str) -> LoadedPack:
        if self.get(pack_id, version) is None:
            raise PackNotInstalled(f"Pack 未安装: {pack_id}@{version}")
        return self.loader.load(pack_id, version)
