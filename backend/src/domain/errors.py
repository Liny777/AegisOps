"""OpenOps V1 错误码（21 号 API 详细设计）与业务异常。"""
from __future__ import annotations


class Err:
    UNAUTHORIZED = "UNAUTHORIZED"
    NOT_WHITELISTED = "NOT_WHITELISTED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    IDEMPOTENCY_KEY_CONFLICT = "IDEMPOTENCY_KEY_CONFLICT"
    WORKSPACE_NOT_READY = "WORKSPACE_NOT_READY"
    TEMPLATE_DISABLED = "TEMPLATE_DISABLED"
    SECRET_REQUIRED = "SECRET_REQUIRED"
    MODEL_PROBE_FAILED = "MODEL_PROBE_FAILED"
    MODEL_CALL_FAILED = "MODEL_CALL_FAILED"
    MODEL_NOT_AUTHORIZED = "MODEL_NOT_AUTHORIZED"
    CONTEXT_LIMIT_EXCEEDED = "CONTEXT_LIMIT_EXCEEDED"
    ASSET_IN_USE = "ASSET_IN_USE"
    INSTANCE_BUSY = "INSTANCE_BUSY"
    CONFIG_VERSION_INVALID = "CONFIG_VERSION_INVALID"
    RUN_ALREADY_CLOSED = "RUN_ALREADY_CLOSED"
    USER_TASK_CONCURRENCY_LIMIT = "USER_TASK_CONCURRENCY_LIMIT"
    EMPTY_SCOPE = "EMPTY_SCOPE"
    SCOPE_RESOLVE_FAILED = "SCOPE_RESOLVE_FAILED"
    APPID_OUT_OF_SCOPE = "APPID_OUT_OF_SCOPE"
    TOOL_NOT_ANNOTATED = "TOOL_NOT_ANNOTATED"
    TOOL_BLOCKED = "TOOL_BLOCKED"
    SANDBOX_CAPACITY_FULL = "SANDBOX_CAPACITY_FULL"
    SANDBOX_CONTAINER_FAILED = "SANDBOX_CONTAINER_FAILED"
    SKILL_CHECKSUM_MISMATCH = "SKILL_CHECKSUM_MISMATCH"
    SKILL_TIMEOUT = "SKILL_TIMEOUT"
    IAM_UPSTREAM = "IAM_UPSTREAM"  # B9：IAM 服务不可达/异常（502，可重试）
    INTERNAL_ERROR = "INTERNAL_ERROR"


_STATUS = {
    Err.UNAUTHORIZED: 401,
    Err.NOT_WHITELISTED: 403,
    Err.FORBIDDEN: 403,
    Err.MODEL_NOT_AUTHORIZED: 403,
    Err.NOT_FOUND: 404,
    Err.IDEMPOTENCY_KEY_CONFLICT: 409,
    Err.RUN_ALREADY_CLOSED: 409,
    Err.INSTANCE_BUSY: 409,
    Err.ASSET_IN_USE: 409,
    Err.CONFIG_VERSION_INVALID: 409,
    Err.USER_TASK_CONCURRENCY_LIMIT: 429,
    Err.SANDBOX_CAPACITY_FULL: 429,  # 会话期常驻：开启 run 时单机容器名额满，可重试（低峰期）
    Err.SANDBOX_CONTAINER_FAILED: 503,
    Err.SKILL_CHECKSUM_MISMATCH: 400,
    Err.SKILL_TIMEOUT: 504,
    Err.IAM_UPSTREAM: 502,
    Err.INTERNAL_ERROR: 500,
}


class ApiError(Exception):
    """业务错误：API 层统一转 {error:{code,message,retryable}} envelope。"""

    def __init__(self, code: str, message: str, *, retryable: bool = False, status: int | None = None,
                 extra: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status = status or _STATUS.get(code, 400)
        # B9：并入 error 对象的附加字段（如 401 的 login_url，前端跳登录用）；None 值不输出
        self.extra = {k: v for k, v in (extra or {}).items() if v is not None}
