"""告警平台 client 分发器：按 `OPENOPS_ALERT=mock`（默认）|`real` 选实现。

7x24 告警接管的唯一上游入口：增量拉取告警变更流 + 单条详情。契约由我方定义、
告警平台实现（服务态 Bearer token，非用户登录态——后台轮询循环无请求上下文，
cookie 三档在这里全部不适用）。callers（alerts 切片 poller/ingest、check-net 自检）
只 import 本 facade，签名不随 mock/real 切换变化。
"""
from __future__ import annotations

import os
from typing import Any


class AlertPlatformError(RuntimeError):
    """告警平台 adapter 的结构化失败，供 alerts 切片稳定映射审计与重试语义。

    kind ∈ config（本端配置缺失/非法，重试无意义）/ auth / network / http /
    cursor_expired（游标超出对端保留窗，带 detail.earliest_cursor 重新对账）/
    rate_limited（带 detail.retry_after_s）。
    """

    def __init__(self, kind: str, message: str, *, status_code: int | None = None,
                 request_id: str = "", detail: dict[str, Any] | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status_code = status_code
        self.request_id = request_id
        self.detail = detail or {}


def _impl() -> Any:
    if os.environ.get("OPENOPS_ALERT", "mock").strip().lower() == "real":
        from infra.external import alert_platform_real

        return alert_platform_real
    from infra.external import alert_platform_mock

    return alert_platform_mock


async def list_changes(cursor: str = "", limit: int = 200) -> dict[str, Any]:
    """增量变更拉取：返回 {"alerts": [AlertDTO...], "next_cursor": str, "has_more": bool}。

    游标不透明、原样回传；空游标 = 从「现在」开始（首启不回灌历史）。update-log 语义，
    同一 alert 每次状态变化会重现流中（at-least-once），去重由 ingest 的 fingerprint
    upsert 承担。AlertDTO 字段见契约：alert_id/fingerprint/status(firing|resolved)/
    severity(fatal|critical|warning|info)/category/title/description/app_id/labels/
    annotations/started_at/resolved_at/updated_at/source。
    """
    return await _impl().list_changes(cursor, limit)


async def get_alert(alert_id: str) -> dict[str, Any] | None:
    """单条详情；超出保留期或不存在返回 None。"""
    return await _impl().get_alert(alert_id)
