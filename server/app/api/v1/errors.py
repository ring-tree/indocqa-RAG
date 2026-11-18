"""错误码元数据：稳定名称与可重试提示。

既有错误码语义不变，只新增 name / retryable 字段。
"""

from __future__ import annotations

ERROR_META: dict[int, tuple[str, bool]] = {
    40001: ("param_invalid", False),
    40002: ("file_too_large", False),
    40003: ("unsupported_format", False),
    40004: ("attachment_limit", False),
    40005: ("upload_not_found", False),
    40006: ("chunk_invalid", False),
    40007: ("config_invalid", False),
    40008: ("field_locked", False),
    40101: ("unauthenticated", False),
    40102: ("token_invalid", False),
    40301: ("permission_denied", False),
    40302: ("forbidden", False),
    40401: ("kb_empty", False),
    40402: ("kb_resource_not_found", False),
    40403: ("session_not_found", False),
    40404: ("attachment_not_found", False),
    40405: ("build_not_found", False),
    40406: ("profile_not_found", False),
    40407: ("adapter_not_found", False),
    40901: ("upload_conflict", False),
    40902: ("embedding_backend_mismatch", False),
    40903: ("kb_busy", False),
    40904: ("llm_not_configured", False),
    40905: ("build_state_conflict", False),
    40906: ("namespace_incompatible", False),
    40907: ("idempotency_conflict", False),
    40908: ("quota_exceeded", False),
    40909: ("session_busy", True),
    40910: ("version_conflict", False),
    40911: ("build_blocked", False),
    42901: ("upstream_unavailable", True),
    42902: ("rate_limited", True),
    50001: ("retrieval_error", True),
    50002: ("internal_error", False),
    50003: ("attachment_storage_error", True),
    50004: ("index_adapter_error", True),
    50005: ("build_failed", False),
    50006: ("object_store_error", True),
    50007: ("embedding_adapter_error", True),
    50301: ("not_ready", True),
    50302: ("maintenance", True),
}


def error_meta(code: int) -> tuple[str, bool]:
    return ERROR_META.get(code, ("unknown_error", False))
