"""配置 Schema 的 Pydantic 模型；由 schema_export 导出为 JSON Schema。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EngineDefaults(StrictConfig):
    engine_version: str = "0.1.0"
    pipeline_version: str = "1"
    parser_version: str = "1"
    chunker_version: str = "1"
    index_schema_version: str = "1"
    retrieval_preset: str = "balanced@1"
    context_template_id: str = "kb-answer"
    context_template_version: str = "1"


class QuotaConfig(StrictConfig):
    max_documents: int = Field(default=10000, ge=1)
    max_chunks: int = Field(default=1000000, ge=1)
    max_bytes: int = Field(default=10 * 1024 * 1024 * 1024, ge=1024)


class KBConfig(StrictConfig):
    kb_id: str = Field(default="kb_default", pattern=r"^kb_[a-z0-9_-]+$")
    name: str = Field(default="默认知识库", min_length=1, max_length=100)
    language_profile: str = "zh-CN"
    domain_pack_id: str | None = None
    embedding_model: str = ""
    preset: str = Field(default="balanced@1", pattern=r"^(precision|balanced|recall)(@1)?$")
    allowed_folders: list[str] = Field(default_factory=list)
    quota: QuotaConfig = Field(default_factory=QuotaConfig)


class AnswerConfig(StrictConfig):
    prompt_profile: str = "default"
    answer_length: str = Field(default="medium", pattern=r"^(short|medium|long)$")
    data_fact_units: list[str] = Field(default_factory=list)
    authority_types: list[str] = Field(default_factory=list)
    role_words: list[str] = Field(default_factory=list)


class UIConfig(StrictConfig):
    subtitle: str = ""
    examples: list[str] = Field(default_factory=list)
    folder_example: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
