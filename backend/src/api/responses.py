"""响应封装：成功 {data, request_id} / 失败 {error:{code,message,retryable}, request_id}（20 号契约）。"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from domain.errors import ApiError


def ok(data: Any) -> dict[str, Any]:
    return {"data": data, "request_id": "req_" + uuid.uuid4().hex[:12]}


def install_error_handlers(app: Any) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError) -> JSONResponse:  # noqa: ANN001
        return JSONResponse(
            status_code=exc.status,
            content={
                "error": {"code": exc.code, "message": exc.message, "retryable": exc.retryable},
                "request_id": "req_" + uuid.uuid4().hex[:12],
            },
        )
