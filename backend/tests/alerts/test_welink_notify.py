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


def test_welink_client_success_logs_and_strips_url(monkeypatch, caplog):
    """成功链路可观测（2026-08-18 内网排查补）：发送前后各一条 info（告警内容不进日志），
    URL 尾随空格 strip（启动脚本 export 引号内带空格实见会 404）。"""
    import logging

    import httpx

    from infra.external import welink_client

    monkeypatch.setenv("SEND_WELINK_MESSAGE_URL", "http://welink.internal/send  ")
    calls: list[tuple[str, dict]] = []

    class _Ok:
        def json(self):
            return {"code": 200}

    monkeypatch.setattr(httpx, "post", lambda url, **k: (calls.append((url, k["json"])), _Ok())[1])
    with caplog.at_level(logging.INFO, logger="openops.welink"):
        welink_client.send_welink_message_for_person("w_u1", "告警敏感正文")
    assert calls[0][0] == "http://welink.internal/send"  # 尾随空格已 strip
    # 对端契约守卫：键名 user_id（带下划线，2026-08-18 核对——userid 会被宽容 200 但不投递）
    assert calls[0][1] == {"user_id": "w_u1", "data": "告警敏感正文"}
    assert "WeLink 通知发送中 user=w_u1" in caplog.text
    assert "WeLink 通知已发送 user=w_u1" in caplog.text
    assert "告警敏感正文" not in caplog.text  # 成功路径只打工号，data 不落日志


def test_notify_owner_done_builds_link_and_dispatches(monkeypatch, caplog):
    """_notify_owner_done：data 含品牌标题/结论中文/会话直达链接，收件人=owner 工号；
    结论只带接管结果不带 summary（2026-08-19：并发中断的残缺摘要看不懂，详情进会话）；
    OPENOPS_WEB_BASE_URL 未配退化为文字指引（通知仍发）；派发时留 info 痕。"""
    import logging

    from alerts import dispatcher
    from infra.external import welink_client

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(welink_client, "send_welink_message_for_person",
                        lambda uid, data: sent.append((uid, data)))
    inc = {"alert_incident_id": "i1", "owner_user_id": "w_owner", "title": "MySQL 主库延迟>5s",
           "severity": "fatal", "category": "MySQL",
           "result_summary": "主从延迟已恢复，同步位点追平"}

    async def _run():
        monkeypatch.setenv("OPENOPS_WEB_BASE_URL", "https://openops.example.com/")
        dispatcher._notify_owner_done(inc, "run-123", "recovered")
        await asyncio.sleep(0.05)  # 驱动 to_thread fire-and-forget
        monkeypatch.delenv("OPENOPS_WEB_BASE_URL")
        dispatcher._notify_owner_done(inc, "run-456", None)
        await asyncio.sleep(0.05)
        # 按 prompt 分单后姊妹单通知靠规则名区分（2026-08-19）：带 matched_rules_json 时必有此行
        dispatcher._notify_owner_done(
            dict(inc, matched_rules_json=[{"rule_id": "r1", "rule_name": "Docker 接管 A"}]),
            "run-789", "recovered")
        await asyncio.sleep(0.05)

    with caplog.at_level(logging.INFO, logger="openops.alerts.dispatcher"):
        asyncio.run(_run())
    assert caplog.text.count("[alerts][notify] WeLink 通知派发 incident=i1 owner=w_owner") == 3
    assert len(sent) == 3
    uid, data = sent[0]
    assert uid == "w_owner"
    assert "【感知快恢Agent 告警接管】" in data
    assert "MySQL 主库延迟>5s" in data and "结论：已恢复" in data
    assert "主从延迟已恢复" not in data  # summary 摘要不进通知（残缺文本防看不懂）
    assert "https://openops.example.com/agent-runs/run-123" in data  # rstrip 后无双斜杠
    assert "命中规则" not in data  # 无 matched_rules_json（存量单）时该行整体省略
    _, data2 = sent[1]
    assert "详见会话" in data2 and "请登录感知快恢Agent" in data2 and "agent-runs" not in data2
    assert "OpenOps" not in data2  # 品牌词全量替换
    _, data3 = sent[2]
    assert "命中规则：Docker 接管 A\n" in data3  # 姊妹单通知的唯一区分项


def test_notify_completed_carries_rca_conclusion(monkeypatch):
    """v3（2026-08-19）：completed 结论行带根因结论（只取模型经诊断板提交的 rca.conclusion，
    200 字截断）；未提交 conclusion 时无竖线（残缺 transcript 永不进通知）。"""
    from alerts import dispatcher
    from infra.external import welink_client

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(welink_client, "send_welink_message_for_person",
                        lambda uid, data: sent.append((uid, data)))
    monkeypatch.setenv("OPENOPS_WEB_BASE_URL", "https://openops.example.com")
    inc = {"alert_incident_id": "i2", "owner_user_id": "w_owner", "title": "MySQL 主库延迟>5s",
           "severity": "fatal", "category": "MySQL"}

    async def _run():
        dispatcher._notify_owner_done(inc, "run-1", "recovered",
                                      conclusion="Redis 连接泄漏已修复，P99 恢复 210ms")
        dispatcher._notify_owner_done(inc, "run-2", "recovered", conclusion="长" * 300)
        await asyncio.sleep(0.05)

    asyncio.run(_run())
    assert "结论：已恢复｜Redis 连接泄漏已修复，P99 恢复 210ms" in sent[0][1]
    assert "长" * 200 + "……" in sent[1][1] and "长" * 201 not in sent[1][1]  # 无句读硬截+省略号


def test_notify_brief_strips_markdown_and_cuts_on_sentence():
    """结论摘要清洗（2026-08-19 内网四样本反馈）：①Markdown 标记剥离（## 标题符/星号/
    反引号原样露出是「看不懂」主源）；②按句边界截断（200 字硬截会拦腰砍句）。"""
    from alerts.dispatcher import _notify_brief

    # 内网样本 3 原文形态（契约压平后的 Markdown 噪音）
    md = ("## 诊断结论 ### 影响边界 - 告警对象：Docker实例 kweekshcct-dxba8:64770 "
          "- 影响应用：智能监控告警(多租) ### 根因判定 最可能根因：**CPU资源规格不足**（置信度：70%） `top` 确认")
    out = _notify_brief(md)
    assert "#" not in out and "*" not in out and "`" not in out
    assert out.startswith("诊断结论")
    assert " · 告警对象" in out  # 列表符转分隔点

    # 句边界截断：超 200 字在最后句读处收口，不拦腰
    long = "最可能根因：流量激增导致连接池打满。" + "补充细节" * 60
    out2 = _notify_brief(long)
    assert out2 == "最可能根因：流量激增导致连接池打满。" or out2.endswith("……")
    sent_cut = _notify_brief("影响边界描述" * 15 + "。最可能根因在此句结束。" + "后续建议内容" * 30)
    assert sent_cut.endswith("最可能根因在此句结束。")  # 在句读处收口，不拦腰

    # 全段无句读：硬截 200 + 省略号；短文本原样
    nostop = "一逗到底" * 80
    out3 = _notify_brief(nostop)
    assert len(out3) == 202 and out3.endswith("……")
    assert _notify_brief("Redis 连接泄漏已修复，P99 恢复 210ms") == "Redis 连接泄漏已修复，P99 恢复 210ms"


def test_notify_failed_reason_and_link_fallback(monkeypatch):
    """v3（2026-08-19 拍板 failed 也通知）：标题「自动诊断失败」+ 结论=原因中文
    （诊断超时/执行失败等 service.reason_text 词表）；无 run 时链接退化清单深链
    /alerts/{incident_id}（可定位重试）。"""
    from alerts import dispatcher
    from infra.external import welink_client

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(welink_client, "send_welink_message_for_person",
                        lambda uid, data: sent.append((uid, data)))
    monkeypatch.setenv("OPENOPS_WEB_BASE_URL", "https://openops.example.com")
    inc = {"alert_incident_id": "i3", "owner_user_id": "w_owner", "title": "MySQL 主库延迟>5s",
           "severity": "fatal", "category": "MySQL"}

    async def _run():
        dispatcher._notify_owner_done(inc, "run-9", None, failed=True, state_reason="timeout")
        dispatcher._notify_owner_done(inc, "", None, failed=True,
                                      state_reason="task_failed")  # 无 run：起跑即败形态
        monkeypatch.delenv("OPENOPS_WEB_BASE_URL")
        dispatcher._notify_owner_done(inc, "", None, failed=True, state_reason="SOME_RAW_CODE")
        await asyncio.sleep(0.05)

    asyncio.run(_run())
    assert len(sent) == 3
    d1 = sent[0][1]
    assert "您的告警自动诊断失败" in d1 and "结论：诊断超时被取消" in d1
    assert "https://openops.example.com/agent-runs/run-9" in d1
    d2 = sent[1][1]
    assert "结论：诊断执行失败（模型或工具调用异常" in d2
    assert "处理入口：https://openops.example.com/alerts/i3" in d2  # 无 run→清单深链
    assert "agent-runs" not in d2
    d3 = sent[2][1]
    assert "结论：SOME_RAW_CODE" in d3  # 未收录裸 code 显原码
    assert "请登录感知快恢Agent，在告警接管清单查看并可重试" in d3  # base 未配退化
