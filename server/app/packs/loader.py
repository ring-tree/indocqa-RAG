"""Domain Pack 发现、读取与安全护栏。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.contracts.pack import DomainPack
from app.contracts.types import ContractError

_BLOCKED_SUFFIXES = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".sh",
    ".bat",
    ".cmd",
    ".ps1",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".jar",
}
_BLOCKS = {
    "parser": "parser",
    "terms": "terms",
    "folders": "folders",
    "classification": "classification",
    "intent": "intent",
    "data_facts": "data-facts",
    "authority": "authority",
    "persona": "persona",
    "prompts": "prompts",
    "ui": "ui",
    "evaluation": "eval",
}
_HTML = re.compile(r"<[^>]+>|javascript:", re.IGNORECASE)


class PackError(ContractError):
    """Pack 相关错误基类。"""


class PackNotFound(PackError):
    """Pack 不存在。"""


class PackInvalid(PackError):
    """Pack 内容或路径不合法。"""


class PackIncompatible(PackError):
    """Pack 与当前引擎版本不兼容。"""


@dataclass(frozen=True)
class LoadedPack:
    pack: DomainPack
    path: Path
    content_hash: str
    files: tuple[str, ...]


def _version(value: str) -> tuple[int, int, int]:
    parts = (value or "").split(".")
    if not 1 <= len(parts) <= 3 or not all(part.isdigit() for part in parts):
        raise PackInvalid(f"版本号不合法: {value!r}")
    numbers = [int(part) for part in parts]
    numbers.extend([0] * (3 - len(numbers)))
    return numbers[0], numbers[1], numbers[2]


def version_satisfies(version: str, spec: str) -> bool:
    """支持 `>=0.1,<1.0` 形式的简单 semver 范围。"""
    current = _version(version)
    for clause in (spec or "").split(","):
        clause = clause.strip()
        if not clause:
            continue
        match = re.match(r"^(>=|<=|>|<|==|=)?\s*(\d+(?:\.\d+){0,2})$", clause)
        if not match:
            raise PackInvalid(f"engine_range 不合法: {spec!r}")
        operator = match.group(1) or "=="
        target = _version(match.group(2))
        if operator in {">=", ">"} and current < target:
            return False
        if operator == ">" and current == target:
            return False
        if operator in {"<=", "<"} and current > target:
            return False
        if operator == "<" and current == target:
            return False
        if operator in {"==", "="} and current != target:
            return False
    return True


class PackLoader:
    def __init__(
        self,
        root: str | Path,
        *,
        engine_version: str = "0.1.0",
        max_files: int = 200,
        max_file_bytes: int = 2 * 1024 * 1024,
        max_total_bytes: int = 20 * 1024 * 1024,
    ):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.engine_version = engine_version
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes

    @staticmethod
    def _pack_file(path: Path) -> Path | None:
        for name in ("pack.json", "pack.yaml", "manifest.json", "manifest.yaml"):
            candidate = path / name
            if candidate.is_file():
                return candidate
        return None

    def discover(self) -> list[dict]:
        results: list[dict] = []
        for entry in sorted(item for item in self.root.iterdir() if item.is_dir()):
            if self._pack_file(entry):
                results.append(self._describe(entry))
                continue
            for version_dir in sorted(item for item in entry.iterdir() if item.is_dir()):
                if self._pack_file(version_dir):
                    results.append(self._describe(version_dir))
        return results

    def _describe(self, path: Path) -> dict:
        try:
            loaded = self.load_path(path)
            return {
                "packId": loaded.pack.manifest.id,
                "version": loaded.pack.manifest.version,
                "path": str(loaded.path),
                "engineRange": loaded.pack.manifest.engine_range,
                "contentHash": loaded.content_hash,
                "valid": True,
            }
        except PackError as exc:
            return {
                "packId": path.parent.name if path.parent != self.root else path.name,
                "version": "",
                "path": str(path),
                "valid": False,
                "error": str(exc),
            }

    def load(self, pack_id: str, version: str) -> LoadedPack:
        candidates = [self.root / pack_id / version, self.root / pack_id]
        for candidate in candidates:
            if not candidate.is_dir() or not self._pack_file(candidate):
                continue
            loaded = self.load_path(candidate)
            if (
                loaded.pack.manifest.id == pack_id
                and loaded.pack.manifest.version == version
            ):
                return loaded
        raise PackNotFound(f"Pack 不存在: {pack_id}@{version}")

    def load_path(self, path: str | Path) -> LoadedPack:
        path = Path(path).resolve()
        if path != self.root and self.root not in path.parents:
            raise PackInvalid(f"Pack 路径越界: {path}")
        files = self._scan(path)
        data = self._read(path)
        try:
            pack = DomainPack.model_validate(data)
        except ValidationError as exc:
            raise PackInvalid(f"Pack 校验失败: {exc.errors()}") from exc
        if not version_satisfies(self.engine_version, pack.manifest.engine_range):
            raise PackIncompatible(
                f"Pack 要求引擎 {pack.manifest.engine_range}，当前 {self.engine_version}"
            )
        self._guard(pack)
        digest = hashlib.sha256()
        for file in files:
            digest.update(file.relative_to(path).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(file.read_bytes())
        return LoadedPack(
            pack=pack,
            path=path,
            content_hash=digest.hexdigest(),
            files=tuple(file.relative_to(path).as_posix() for file in files),
        )

    def _scan(self, path: Path) -> list[Path]:
        files: list[Path] = []
        total = 0
        for file in sorted(path.rglob("*")):
            if file.is_symlink():
                raise PackInvalid(f"Pack 不允许符号链接: {file.name}")
            if not file.is_file():
                continue
            if file.suffix.lower() in _BLOCKED_SUFFIXES:
                raise PackInvalid(f"Pack 不允许可执行文件: {file.name}")
            size = file.stat().st_size
            if size > self.max_file_bytes:
                raise PackInvalid(f"Pack 文件过大: {file.name}")
            total += size
            files.append(file)
        if len(files) > self.max_files:
            raise PackInvalid(f"Pack 文件数超过上限 {self.max_files}")
        if total > self.max_total_bytes:
            raise PackInvalid("Pack 总大小超过上限")
        return files

    @staticmethod
    def _load_structured(path: Path) -> Any:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            return json.loads(text)
        return yaml.safe_load(text)

    def _read(self, path: Path) -> dict:
        for name in ("pack.json", "pack.yaml"):
            candidate = path / name
            if candidate.exists():
                data = self._load_structured(candidate)
                if not isinstance(data, dict):
                    raise PackInvalid("Pack 根节点必须是对象")
                return data
        manifest = self._pack_file(path)
        if manifest is None:
            raise PackInvalid("缺少 pack.json / manifest.json")
        manifest_data = self._load_structured(manifest)
        if not isinstance(manifest_data, dict):
            raise PackInvalid("Manifest 必须是对象")
        data: dict = {"manifest": manifest_data}
        for key, stem in _BLOCKS.items():
            for suffix in (".json", ".yaml"):
                candidate = path / f"{stem}{suffix}"
                if candidate.exists():
                    data[key] = self._load_structured(candidate)
                    break
        return data

    @staticmethod
    def _guard(pack: DomainPack) -> None:
        if len(pack.classification.filename_rules) > 200:
            raise PackInvalid("分类规则超过上限 200")
        total_keywords = sum(len(items) for items in pack.intent.keywords.values())
        if any(len(items) > 500 for items in pack.intent.keywords.values()):
            raise PackInvalid("单类意图关键词超过上限 500")
        if total_keywords > 2000:
            raise PackInvalid("意图关键词总数超过上限 2000")
        for text in (
            pack.ui.subtitle,
            pack.ui.folder_example,
            *pack.ui.examples,
            *pack.ui.labels.values(),
        ):
            if _HTML.search(text):
                raise PackInvalid("UI 文案只允许纯文本")
        for folder in [pack.folders.default, *pack.folders.template]:
            if not folder or folder.startswith("/") or ".." in folder:
                raise PackInvalid(f"目录路径不合法: {folder!r}")
            parts = folder.split("/")
            if len(parts) > 6 or any(len(part) > 40 for part in parts):
                raise PackInvalid(f"目录路径超过限制: {folder!r}")
