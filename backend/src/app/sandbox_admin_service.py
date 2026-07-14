"""管理台沙箱视图与容器操作（B8-4，30.6 号「沙箱与容量」）。

容器运行态以 SandboxExecutor 进程内注册表 + Docker 为真相源（不落 PG 核心表）；管理员可查看
容器列表（含 active_run_count）与强制销毁运行中容器（二次确认+reason 由前端保证，后端写审计+
向该用户活跃 run 推可见事件）。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from infra.redact import redact_text, sanitize_activity_payload
from infra.repositories import audit, runs, runtime_config
from runtime import events
from sandbox.executor import executor

log = logging.getLogger("openops.sandbox")


async def list_containers() -> list[dict[str, Any]]:
    """当前用户容器列表（含容量口径：并发活跃 run 用户数 + idle 未回收）。"""
    items = executor.list_containers()
    return items


async def sweep_once() -> int:
    """按 DB 里当前 idle TTL 回收一次过期容器（后台循环与手工触发共用）。"""
    cfg = {c["config_key"]: c["config_value_json"]
           for c in await runtime_config.get_domain(runtime_config.DOMAIN_SANDBOX)}
    return await executor.sweep_idle(cfg)


async def background_sweep_loop(interval_s: float) -> None:
    """后台定时回收 idle 沙箱容器（现有懒回收只在关会话/容量满触发，静默期需定时器兜底）。"""
    while True:
        await asyncio.sleep(interval_s)  # 先睡后扫：启动风暴/测试进程友好
        try:
            n = await sweep_once()
            if n:
                log.info("[sandbox] 后台回收 idle 容器 %d 个", n)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — DB 抖动等：本轮跳过，绝不退出循环
            log.warning("[sandbox] 后台回收失败，本轮跳过", exc_info=True)


async def destroy_container(target_user_id: str, reason: str, by: str) -> dict[str, Any]:
    """强制销毁指定用户容器（中断其运行中 run）；写审计 + 推用户可见事件。"""
    trace = str(uuid.uuid4())
    await audit.insert_event(
        audit_trace_id=trace, event_type="sandbox.container.destroy_requested", user_id=by,
        action="destroy", actor_type="platform_admin",
        payload_redacted={"target_user_id": target_user_id, "reason": reason},
    )
    destroyed = await executor.destroy(target_user_id)
    await audit.insert_event(
        audit_trace_id=trace, event_type="sandbox.container.destroyed", user_id=by,
        action="destroy", actor_type="platform_admin", decision="destroyed" if destroyed else "absent",
        payload_redacted={"target_user_id": target_user_id, "reason": reason},
    )
    # 向该用户的活跃 run 推可见事件（会话进行中被销毁需告知）
    if destroyed:
        for r in await runs.list_runs_by_user(target_user_id):
            if r["run_status"] == "active":
                message = redact_text(
                    "管理员已回收你的沙箱容器：当前任务已中断，新任务将重建容器。原因：" + reason,
                    max_length=500,
                )
                payload = sanitize_activity_payload(
                    "openops.sandbox.container.destroyed_by_admin",
                    {"summary": message},
                    message=message,
                )
                eid = await audit.insert_event(
                    audit_trace_id=trace,
                    event_type="sandbox.container.destroyed_by_admin",
                    user_id=target_user_id,
                    run_id=str(r["agent_run_id"]),
                    instance_id=str(r["agent_team_instance_id"]),
                    action="destroy",
                    actor_type="platform_admin",
                    payload_redacted=payload,
                )
                events.publish(str(r["agent_run_id"]), events.envelope(
                    str(r["agent_run_id"]), "openops.sandbox.container.destroyed_by_admin", severity="warning",
                    message=message, payload=payload, audit_trace_id=trace, event_id=eid))
    return {"destroyed": destroyed, "target_user_id": target_user_id}
