"""Domain Pack 声明式数据模型。

Pack 只允许数据，不允许代码；未知字段一律拒绝，避免拼写错误静默生效。
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

_ALLOWED_INTENTS = {"term", "param", "procedure", "fault"}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PackManifest(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    domain: str = Field(min_length=1, max_length=64)
    languages: list[str] = Field(default_factory=lambda: ["zh"], min_length=1)
    engine_range: str = ">=0.1,<1.0"
    description: str = ""


class FilenameRule(StrictModel):
    pattern: str = Field(min_length=1, max_length=200)
    folder: str = Field(min_length=1, max_length=200)
    priority: int = Field(default=100, ge=0, le=1000)

    @field_validator("pattern")
    @classmethod
    def _compile_pattern(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"正则不合法: {exc}") from exc
        return value


class FolderConfig(StrictModel):
    template: list[str] = Field(default_factory=list)
    default: str = "未分类"
    migration: dict[str, str] = Field(default_factory=dict)


class ParserConfig(StrictModel):
    skip_files: list[str] = Field(default_factory=list)


class TermsConfig(StrictModel):
    clusters: dict[str, list[str]] = Field(default_factory=dict)


class ClassificationConfig(StrictModel):
    filename_rules: list[FilenameRule] = Field(default_factory=list)
    document_types: list[str] = Field(default_factory=list)
    title_cleanup: list[str] = Field(default_factory=list)


class IntentConfig(StrictModel):
    keywords: dict[str, list[str]] = Field(default_factory=dict)
    connectors: list[str] = Field(default_factory=list)
    reference_code_pattern: str | None = None
    exceptions: list[str] = Field(default_factory=list)

    @field_validator("keywords")
    @classmethod
    def _allowed_intents(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        unknown = set(value) - _ALLOWED_INTENTS
        if unknown:
            raise ValueError(f"不允许的意图键: {', '.join(sorted(unknown))}")
        return value

    @field_validator("reference_code_pattern")
    @classmethod
    def _compile_reference_pattern(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"编号正则不合法: {exc}") from exc
        return value


class DataFactsConfig(StrictModel):
    units: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class AuthorityConfig(StrictModel):
    document_types: dict[str, float] = Field(default_factory=dict)


class PersonaConfig(StrictModel):
    roles: list[str] = Field(default_factory=list)


class PromptStyleConfig(StrictModel):
    tone: str = ""
    length: str = ""
    structure: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)


class PackUIConfig(StrictModel):
    subtitle: str = ""
    examples: list[str] = Field(default_factory=list)
    folder_example: str = ""
    labels: dict[str, str] = Field(default_factory=dict)


class EvalConfig(StrictModel):
    dataset: str | None = None


class DomainPack(StrictModel):
    manifest: PackManifest
    parser: ParserConfig = Field(default_factory=ParserConfig)
    terms: TermsConfig = Field(default_factory=TermsConfig)
    folders: FolderConfig = Field(default_factory=FolderConfig)
    classification: ClassificationConfig = Field(default_factory=ClassificationConfig)
    intent: IntentConfig = Field(default_factory=IntentConfig)
    data_facts: DataFactsConfig = Field(default_factory=DataFactsConfig)
    authority: AuthorityConfig = Field(default_factory=AuthorityConfig)
    persona: PersonaConfig = Field(default_factory=PersonaConfig)
    prompts: PromptStyleConfig = Field(default_factory=PromptStyleConfig)
    ui: PackUIConfig = Field(default_factory=PackUIConfig)
    evaluation: EvalConfig = Field(default_factory=EvalConfig)
