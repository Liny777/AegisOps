"""WeLink 个人消息通知（内网网关；2026-08-16 告警接管完成通知落地）。

函数体收编自内网同事实现（保留原名与对端契约：POST {SEND_WELINK_MESSAGE_URL}
body={"user_id", "data"}——键名带下划线，2026-08-18 用户核对对端确认；响应
code==200 为成功）。fail-safe 铁律：任何失败只留 error 日志绝不抛——通知是
锦上添花，不允许影响诊断收割主流程。

⚠ sync httpx（timeout 30s）：async 调用方必须 `asyncio.to_thread` 包裹 +
fire-and-forget（见 alerts/dispatcher._notify_owner_done），直调会阻塞事件循环。
"""
from __future__ import annotations

import logging
import os

import httpx

from infra.external.mcp_registry_client import console_tls_verify, http_trust_env

_log = logging.getLogger("openops.welink")


def send_welink_message_for_person(user_id: str, data: str):
    # strip：内网启动脚本 export 引号内尾随空格会让 URL 404（2026-08-18 排查实见）
    url = os.environ.get("SEND_WELINK_MESSAGE_URL", "").strip()
    if not url:  # 未配置=功能天然关（守卫收编时补：别对空 URL 发请求每次刷 error）
        _log.debug("SEND_WELINK_MESSAGE_URL 未配置，跳过 WeLink 通知 user=%s", user_id)
        return
    try:
        # 发送前后各留一条 info（只打工号，告警内容不进日志）：有「发送中」无后续=挂在 30s 超时
        _log.info("WeLink 通知发送中 user=%s", user_id)
        body = {"user_id": user_id, "data": data}  # 对端只认 user_id（userid 会被宽容 200 但不投递）
        response = httpx.post(url, json=body, headers={"Content-Type": "application/json"}, timeout=30,
                              verify=console_tls_verify(), trust_env=http_trust_env())
        response_json = response.json()
        if response_json.get("code", 0) != 200:
            _log.error(f"Failed to send_welink_message_for_person:{user_id},message:{data}", exc_info=True)
        else:
            _log.info("WeLink 通知已发送 user=%s", user_id)
        return
    except Exception:  # noqa: BLE001 —— fail-safe：通知失败只留痕，绝不外抛
        _log.error(f"Failed to send_welink_message_for_person:{user_id},message:{data}", exc_info=True)
        return
