"""告警接管完成 WeLink 通知（2026-08-16，Phase2 通知项部分落地：仅 completed）。

send_welink_message_for_person 收编自内网（sync httpx 全吞错）；dispatcher 在
completed 收割后 to_thread fire-and-forget 派发——通知失败绝不影响诊断终态。
"""
from __future__ import annotations

import asyncio

import pytest


def test_welink_client_skips_when_url_unset(monkeypatch):
    """SEND_WELINK_MESSAGE_URL 未配=功能天然关：零 HTTP 请求（别对空 URL 刷 error）。"""
    import httpx

    from infra.external import welink_client

    monkeypatch.delenv("SEND_WELINK_MESSAGE_URL", raising=False)
    called = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: called.append(1))
    welink_client.send_welink_message_for_person("u1", "hello")
    assert not called


def test_welink_client_swallows_errors(monkeypatch, caplog):
    """对端 code!=200 与网络异常都只留 error 日志不外抛（fail-safe 铁律）。"""
    import logging

    import httpx

    from infra.external import welink_client

    monkeypatch.setenv("SEND_WELINK_MESSAGE_URL", "http://welink.internal/send")

    class _Resp:
        def json(self):
            return {"code": 500, "message": "boom"}

    with caplog.at_level(logging.ERROR, logger="openops.welink"):
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
        welink_client.send_welink_message_for_person("u1", "m1")  # code!=200：不抛

        def _boom(*a, **k):
            raise httpx.ConnectError("down")

        monkeypatch.setattr(httpx, "post", _boom)
        welink_client.send_welink_message_for_person("u1", "m2")  # 异常：吞
    assert caplog.text.count("Failed to send_welink_message_for_person") == 2


def test_notify_owner_done_builds_link_and_dispatches(monkeypatch):
    """_notify_owner_done：data 含标题/结论中文/会话链接，收件人=owner 工号；
    OPENOPS_WEB_BASE_URL 未配退化为文字指引（通知仍发）。"""
    from alerts import dispatcher
    from infra.external import welink_client

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(welink_client, "send_welink_message_for_person",
                        lambda uid, data: sent.append((uid, data)))
    inc = {"alert_incident_id": "i1", "owner_user_id": "w_owner", "title": "MySQL 主库延迟>5s",
           "severity": "fatal", "category": "MySQL"}

    async def _run():
        monkeypatch.setenv("OPENOPS_WEB_BASE_URL", "https://openops.example.com/")
        dispatcher._notify_owner_done(inc, "run-123", "主从延迟已恢复", "recovered")
        await asyncio.sleep(0.05)  # 驱动 to_thread fire-and-forget
        monkeypatch.delenv("OPENOPS_WEB_BASE_URL")
        dispatcher._notify_owner_done(inc, "run-456", None, None)
        await asyncio.sleep(0.05)

    asyncio.run(_run())
    assert len(sent) == 2
    uid, data = sent[0]
    assert uid == "w_owner"
    assert "MySQL 主库延迟>5s" in data and "已恢复" in data
    assert "https://openops.example.com/agent-runs/run-123" in data  # rstrip 后无双斜杠
    _, data2 = sent[1]
    assert "详见会话" in data2 and "请登录 OpenOps" in data2 and "agent-runs" not in data2
