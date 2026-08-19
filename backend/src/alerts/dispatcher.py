"""队列消费与诊断收割：DB 即队列（incident_state 驱动），进程内并发闸。

- dispatch_once()：并发闸内尽量多地抢占 queued 单（条件 UPDATE，重启/并发安全），
  每单一个 worker 协程：建/复用 run（owner 身份，entry_source='alert'）→ start_task(origin='alert')
  → await orchestrator 收割 result_summary → incident 终态。
- converge_incidents()：启动收敛（在 core converge_orphan_tasks 之后跑）——diagnosing 的单
  按 task 快照分流：completed 补收割 / interrupted 按预算 requeue / 陈旧打 failed。
- start_background()：poller（OPENOPS_ALERT_PULL_INTERVAL_S，0=关）+ dispatcher 两个循环，
  形状照 sandbox_admin_service（先睡后跑 / CancelledError raise / 兜底不退出）。

并发放大警示：诊断并发 × (1 + max_children=3) = 模型峰值并发。默认 2 ⇒ 峰值 8，
模型 API 并发 3~10 时已贴上限——上线初期建议 alert_max_concurrent_diagnosis=1。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from alerts import matcher
from alerts import repository as repo
from alerts import service
from alerts.rule_templates import DEFAULT_RULE_PROMPT
from domain.errors import ApiError, Err
from domain.schemas import CreateRunRequest
from sandbox.executor import ALERT_SANDBOX_UID
from infra.repositories import agent_session_states, audit
from infra.repositories import runs as runs_repo
from infra.repositories import task_states

log = logging.getLogger("openops.alerts.dispatcher")

_AUDIT_TRACE = str(uuid.uuid5(uuid.NAMESPACE_URL, "openops:alert-dispatch"))

_kick_event: asyncio.Event | None = None
_workers: set[asyncio.Task[None]] = set()


@dataclass
class _AlertTaskReq:
    """start_task 的内部 DTO（同 agui._TaskReq 先例）：origin 经 getattr 缝隙进入分池，
    HTTP StartTaskRequest 刻意无此字段（tests/alerts/test_core_seams 守着）。
    skill_hint 与 HTTP DTO 的同名公开字段（domain/schemas.py StartTaskRequest）同语义：
    start_task 里 resolve_skill_alias 校验属主可用面，未命中即忽略（fail-safe）。"""

    client_request_id: str
    input_text: str
    origin: str = "alert"
    skill_hint: str | None = None


# 规则提示词里的 "/skill" 引用：行首或空白后的 /token（前端斜杠菜单插入的形态）。
_SLASH_TOKEN_RE = re.compile(r"(?:^|\s)/([^\s/]+)")
_TRAILING_PUNCT = "，。；、！？：,.;:!?)）]】』」\"'"


def _skill_hint_from_prompt(prompt: str) -> str | None:
    """从规则自定义提示词提取首个 /token 作 skill_hint 候选——**只做词法提取，不做 resolve**。

    校验收敛在 start_task 的 resolve_skill_alias（未命中 available_skills 即忽略），在这里
    复刻可用面装配只会造成双份逻辑漂移。剥尾部标点是为「先执行 /inspect，再…」这类中文散文；
    skill_key 形态不含 CJK 标点，误剥不可能。多个 /token 取第一个（st.skill_hint 单值语义）。
    只对用户自定义 prompt 调用——DEFAULT_RULE_PROMPT 无 slash token，调了也无害。
    """
    m = _SLASH_TOKEN_RE.search(prompt or "")
    if not m:
        return None
    return m.group(1).rstrip(_TRAILING_PUNCT) or None


class _AlertCreateRunReq(CreateRunRequest):
    """create_run 的内部 DTO：sandbox_owner 缝隙指向共享告警沙箱（决策⑥）。
    HTTP 的 CreateRunRequest 刻意无此字段（tests/alerts/test_core_seams 守着）。"""

    sandbox_owner: str = ALERT_SANDBOX_UID


def kick() -> None:
    """ingest 入队 / 用户 :retry 后降低派发延迟；循环未启动时为 no-op（短询/手动 :dispatch 兜底）。"""
    if _kick_event is not None:
        _kick_event.set()


def _active_workers() -> int:
    return sum(1 for w in _workers if not w.done())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age_s(ts: Any) -> float:
    if ts is None:
        return float("inf")
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (_now() - ts).total_seconds()


# ============================ 派发 ============================


async def dispatch_once() -> int:
    """并发闸内尽量多地启动诊断 worker；返回本轮启动数（admin :dispatch 与循环共用）。"""
    started = 0
    while True:
        cfg = await service.get_config()
        if not cfg["alert_enabled"]:
            return started
        if _active_workers() >= int(cfg["alert_max_concurrent_diagnosis"]):
            return started  # 告警池硬上限（原旋钮语义保留；与分池闸双闸取严）
        # v3 分池闸（§5.2，2026-08-15）：超出告警硬保底后要与交互池共抢弹性额度。
        # 计数单一事实源=task_registry（origin 区分两池）；n_alert 取本地 worker 数与
        # registry 的 max——worker 从起跑到 TaskState 注册有窗口期，取严防超发。
        from app import run_state_service
        from infra.repositories import runtime_config
        from runtime import task_registry

        pool_cfg = {c["config_key"]: c["config_value_json"]
                    for c in await runtime_config.get_domain(runtime_config.DOMAIN_SANDBOX)}
        n_alert = max(_active_workers(), task_registry.running_total("alert"))
        if not run_state_service.pool_admit("alert", task_registry.running_total("user"),
                                            n_alert, pool_cfg):
            return started
        inc = await repo.pick_next_queued(matcher.SEVERITY_ORDER,
                                          int(cfg["alert_aging_minutes"]))
        if inc is None:
            return started
        iid = str(inc["alert_incident_id"])
        # 白名单双保险（主闸在 list_enabled_rules 的匹配面）：owner 在排队后被移出名单，
        # 存量 queued 单派发前再拦一道——置 skipped 留痕，不白耗诊断名额。
        if not await repo.is_user_granted(str(inc["owner_user_id"])):
            await repo.transition(iid, from_states=["queued"], to_state="skipped", set_ended=True,
                                  state_reason="grant_revoked：属主的告警接管开通已被管理员关闭")
            continue
        # 条件 UPDATE 抢占：并发 dispatch_once / 多 worker 竞态下只有一个赢家
        if not await repo.transition(iid, from_states=["queued"], to_state="diagnosing",
                                     set_started=True):
            continue
        fresh = await repo.get_incident(iid)
        worker = asyncio.create_task(_run_diagnosis(fresh or inc))
        _workers.add(worker)
        worker.add_done_callback(_workers.discard)
        started += 1


_RESULT_TEXT = {"recovered": "已恢复", "escalated": "已升级"}


def _notify_owner_done(inc: dict[str, Any], run_id: str, agent_result: str | None) -> None:
    """诊断完成 WeLink 通知 owner（2026-08-16，Phase2 通知项部分落地：仅 completed）。

    fire-and-forget + to_thread：send_welink_message_for_person 是 sync httpx
    （timeout 30s）且全吞错——通知失败绝不影响收割主流程；仅日志留痕。
    链接根=OPENOPS_WEB_BASE_URL（感知快恢 Agent 前端域名）；未配则退化为文字指引（通知仍发）。
    结论只发接管结果不带 summary 摘要（2026-08-19 拍板：并发中断的诊断收割出的残缺
    文本会让通知看不懂——详情点链接进会话看）。
    """
    try:
        from infra.external.welink_client import send_welink_message_for_person

        owner = str(inc.get("owner_user_id") or "")
        if not owner:
            return
        base = os.environ.get("OPENOPS_WEB_BASE_URL", "").strip().rstrip("/")
        link = (f"查看诊断会话：{base}/agent-runs/{run_id}" if base
                else "请登录感知快恢Agent，在告警接管清单查看诊断会话")
        verdict = _RESULT_TEXT.get(str(agent_result or ""), "详见会话")
        matched = inc.get("matched_rules_json") or []
        rule_name = str((matched[0] or {}).get("rule_name") or "") if matched else ""
        # 规则名必带（2026-08-19 按 prompt 分单后同一告警可有多张姊妹单先后完成，
        # 不带规则名的多条通知长得一样，会被当成重复发送的 bug 上报）
        rule_line = f"命中规则：{rule_name}\n" if rule_name else ""
        data = ("【感知快恢Agent 告警接管】您的告警已完成自动诊断\n"
                f"告警：{inc.get('title')}（{inc.get('severity')}/{inc.get('category') or '未知'}）\n"
                f"{rule_line}"
                f"结论：{verdict}\n"
                f"{link}")
        log.info("[alerts][notify] WeLink 通知派发 incident=%s owner=%s run=%s",
                 inc.get("alert_incident_id"), owner, run_id)
        asyncio.create_task(asyncio.to_thread(send_welink_message_for_person, owner, data))
    except Exception:  # noqa: BLE001 —— 组装/派发失败也不许影响收割
        log.warning("[alerts][notify] WeLink 通知派发失败 incident=%s",
                    inc.get("alert_incident_id"), exc_info=True)


def _build_prompt(inc: dict[str, Any], events: list[dict[str, Any]], requirement: str) -> str:
    """结构化诊断输入：告警要素 + 要求段（规则自定义提示词优先，空则系统默认五步法）。

    告警编号必带（2026-08-16）：Agent/子 Agent 的 MCP 告警工具按编号查平台详情
    （query_alarm_detail 类入参=alarmCode），提示词里没编号等于工具废掉一半。
    """
    alarm_ids = [str(e.get("external_alert_id") or "") for e in events if e.get("external_alert_id")]
    head_id = alarm_ids[0] if alarm_ids else "未知"
    id_suffix = f"（等 {len(alarm_ids)} 条，全列见下方明细）" if len(alarm_ids) > 1 else ""
    lines = [
        "【7x24 告警自动接管】以下告警命中了本 Agent 的接管规则，请立即开展自动诊断。",
        "",
        f"告警编号：{head_id}{id_suffix}",
        f"告警标题：{inc.get('title')}",
        f"严重度：{inc.get('severity')}    告警类型：{inc.get('category') or '未知'}",
        f"影响应用（APPID）：{matcher.app_id_from_group_key(str(inc.get('group_key') or '')) or '未知'}",
        f"聚合条数：{inc.get('alert_count')}（首condition {inc.get('first_alert_at')}）",
    ]
    for e in events[:5]:
        labels = e.get("labels_json") or {}
        anno = e.get("annotations_json") or {}
        eid = str(e.get("external_alert_id") or "").strip()
        seg = f"- [{e.get('severity')}]{f' {eid}' if eid else ''} {e.get('alert_name')}"
        if anno.get("description"):
            seg += f"：{str(anno['description'])[:300]}"
        if labels:
            seg += "  labels(" + ", ".join(f"{k}={v}" for k, v in list(labels.items())[:8]) + ")"
        lines.append(seg)
    if len(events) > 5:
        lines.append(f"-（其余 {len(events) - 5} 条同组告警略）")
    lines += ["", requirement]
    return "\n".join(lines)


async def _ensure_run_and_task(inc: dict[str, Any]) -> tuple[str, str, Any]:
    """建/复用诊断 run 并起 task；返回 (run_id, task_id, TaskState)。"""
    from app import run_state_service
    from runtime import task_registry

    iid = str(inc["alert_incident_id"])
    retry = int(inc.get("retry_count") or 0)
    owner = {"user_id": str(inc["owner_user_id"])}
    instance_id = str(inc["agent_team_instance_id"])

    run_id = str(inc["agent_run_id"]) if inc.get("agent_run_id") else None
    if run_id is not None and await runs_repo.get_run(run_id) is None:
        run_id = None  # run 已被删：重建
    if run_id is None:
        req = _AlertCreateRunReq(client_request_id=f"alert-{iid}-r{retry}",
                                 agent_team_instance_id=instance_id)
        result = await run_state_service.create_run(owner, req)
        run_id = str(result["run"]["agent_run_id"])
        await runs_repo.set_entry_source(run_id, "alert")
        await runs_repo.set_run_title(run_id, f"[告警] {inc.get('title')}"[:60], "system")

    events, _total = await repo.list_incident_events(iid, limit=10)
    # 本单 prompt 组的提示词（多规则命中按组分单，各单各注入）：组首 alert_rule_id 优先；
    # 组首被软删时按 matched_rules_json 序回落组内下一条存活规则——同组建单时归一 prompt
    # 必相同，任一存活规则等价。不回落会静默换成默认五步法且 skill_hint 一并丢失，
    # 而删除同 prompt 冗余规则恰是合并语义鼓励的清理动作（2026-08-19 审查实证）。
    rule_prompt = ""
    candidates = [str(inc["alert_rule_id"])] if inc.get("alert_rule_id") else []
    candidates += [rid for m in (inc.get("matched_rules_json") or [])
                   if (rid := str((m or {}).get("rule_id") or "")) and rid not in candidates]
    if candidates:
        alive = {str(r["alert_rule_id"]): r for r in await repo.list_rules_by_ids(candidates)}
        for rid in candidates:
            if rid in alive:
                rule_prompt = (alive[rid].get("prompt") or "").strip()
                break
    treq = _AlertTaskReq(client_request_id=f"alert-task-{iid}-r{retry}",
                         input_text=_build_prompt(inc, events,
                                                  rule_prompt or DEFAULT_RULE_PROMPT),
                         skill_hint=_skill_hint_from_prompt(rule_prompt))
    res = await run_state_service.start_task(owner, run_id, treq)
    st = task_registry.get_by_run(run_id)
    assert st is not None  # submit_task 同步 put，紧随其后必命中
    return run_id, str(res["task_id"]), st


async def _harvest_summary(inc: dict[str, Any],
                           rca: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """result_summary 提取（优先级：rca 结论 → transcript 末条 assistant）；agent_result 仅在
    模型经诊断板提交了 verdict（recovered/escalated）时回填，否则 None（UI 显「—」详见会话）。

    rca 参数化（2026-08-19）：主链传 st.rca（内存），converge 补收割传 task 快照的
    rca_json——此前 converge 传 None，重启补收割的单连 conclusion 都拿不到。
    """
    if rca and rca.get("conclusion"):
        return str(rca["conclusion"])[:500], _agent_result_from_rca(rca)
    run_id = str(inc["agent_run_id"]) if inc.get("agent_run_id") else None
    if run_id:
        run = await runs_repo.get_run(run_id)
        if run is not None:
            from app.agui_service import project_transcript  # studio facade 文档认可的公开投影 API

            state_json = await agent_session_states.get_state_json(
                str(run["framework_session_id"]), "main")
            msgs = project_transcript(state_json, run_id)
            for m in reversed(msgs):
                if m["role"] == "assistant" and m["content"].strip():
                    return m["content"].strip()[:500], _agent_result_from_rca(rca)
    return None, _agent_result_from_rca(rca)


def _agent_result_from_rca(rca: dict[str, Any] | None) -> str | None:
    """结果列数据源=诊断板 verdict（模型经契约提交；2026-08-19 修）。此前误读 rca["status"]
    ——其值域只有 in_progress/concluded（服务端派生、契约拒收模型提交），completed 单的
    agent_result 因此恒 NULL，「已恢复/已升级」上线以来从未显示过。"""
    verdict = str((rca or {}).get("verdict") or "").lower()
    return verdict if verdict in ("recovered", "escalated") else None


async def _handle_start_failure(inc: dict[str, Any], exc: Exception) -> None:
    cfg = await service.get_config()
    iid = str(inc["alert_incident_id"])
    code = getattr(exc, "code", type(exc).__name__)
    retries = int(inc.get("retry_count") or 0)
    log.warning("[alerts][dispatch] 起诊断失败 incident=%s code=%s retry=%d", iid, code, retries)
    if retries < int(cfg["alert_max_retries"]):
        await asyncio.sleep(float(cfg["alert_retry_backoff_s"]))  # 单期间保持 diagnosing 防重复抢占
        await repo.requeue(iid, bump_retry=True)
        kick()
        return
    reason = ("no_scope" if code in (Err.SCOPE_RESOLVE_FAILED, Err.EMPTY_SCOPE,
                                     Err.WORKSPACE_NOT_READY) else str(code))
    await repo.finish(iid, to_state="failed", state_reason=reason,
                      result_summary=None, agent_result="failed")  # 清单结果列显「失败」
    await audit.insert_event(
        audit_trace_id=_AUDIT_TRACE, event_type="alert.diagnosis_failed", user_id="system",
        instance_id=str(inc["agent_team_instance_id"]), action="dispatch",
        reason_code=str(code)[:64], payload_redacted={"incident_id": iid, "retries": retries})


async def _run_diagnosis(inc: dict[str, Any]) -> None:
    iid = str(inc["alert_incident_id"])
    cfg = await service.get_config()
    try:
        run_id, task_id, st = await _ensure_run_and_task(inc)
    except asyncio.CancelledError:
        raise  # 关停：converge 下次启动收敛
    except Exception as exc:  # noqa: BLE001 —— 容量满/scope 失败等：退避重试或 failed
        await _handle_start_failure(inc, exc)
        return
    await repo.bind_run(iid, run_id, task_id)
    await audit.insert_event(
        audit_trace_id=_AUDIT_TRACE, event_type="alert.diagnosis_started", user_id="system",
        run_id=run_id, task_id=task_id, instance_id=str(inc["agent_team_instance_id"]),
        action="dispatch", payload_redacted={"incident_id": iid, "severity": inc.get("severity")})

    try:
        # shield：wait_for 超时不得直接砍协程——走标准 cancel_task_state（收口 pending ASK + 审计）
        assert st.orchestrator is not None
        await asyncio.wait_for(asyncio.shield(st.orchestrator),
                               timeout=float(cfg["alert_diagnosis_timeout_s"]))
    except asyncio.TimeoutError:
        from app import run_state_service

        try:
            await run_state_service.cancel_task_state(st, "system")
        except Exception:  # noqa: BLE001
            log.warning("[alerts][dispatch] 超时取消失败 task=%s", task_id, exc_info=True)
        await repo.finish(iid, to_state="failed", state_reason="timeout",
                          result_summary=None, agent_result="failed")  # 清单结果列显「失败」
        return
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 —— orchestrator 异常：done_callback 已置 st.status=failed
        pass

    inc_now = await repo.get_incident(iid) or inc
    if st.status == "completed":
        summary, agent_result = await _harvest_summary(inc_now, getattr(st, "rca", None))
        await repo.finish(iid, to_state="completed", state_reason=None,
                          result_summary=summary, agent_result=agent_result)
        await audit.insert_event(
            audit_trace_id=_AUDIT_TRACE, event_type="alert.diagnosis_completed", user_id="system",
            run_id=run_id, task_id=task_id, instance_id=str(inc["agent_team_instance_id"]),
            action="harvest", payload_redacted={"incident_id": iid,
                                                "summary_chars": len(summary or "")})
        _notify_owner_done(inc_now, run_id, agent_result)
    else:
        await repo.finish(iid, to_state="failed", state_reason=f"task_{st.status}",
                          result_summary=None, agent_result="failed")
        await audit.insert_event(
            audit_trace_id=_AUDIT_TRACE, event_type="alert.diagnosis_failed", user_id="system",
            run_id=run_id, task_id=task_id, instance_id=str(inc["agent_team_instance_id"]),
            action="harvest", reason_code=f"task_{st.status}",
            payload_redacted={"incident_id": iid})


# ============================ 重启收敛 ============================


async def converge_incidents() -> dict[str, int]:
    """启动收敛（须在 core converge_orphan_tasks 之后）：diagnosing 单按 task 快照分流。

    completed → 补收割（transcript 先于 task.completed 持久化，宕机窗口内也齐全）；
    其余 → 预算内 requeue（复用同一 run：AgentState 记忆延续）/ 超预算或陈旧 → failed。
    queued 单零处理——DB 即队列的直接回报。
    """
    cfg = await service.get_config()
    counts = {"harvested": 0, "requeued": 0, "failed": 0}
    for inc in await repo.list_diagnosing():
        iid = str(inc["alert_incident_id"])
        task_id = inc.get("diagnosis_task_id")
        snap = await task_states.get_by_task(str(task_id)) if task_id else None
        status = (snap or {}).get("task_status")
        if status == "completed":
            # 快照 rca_json：重启后内存 TaskState 已失，结论/verdict 从落盘快照取（select * 已含）
            summary, agent_result = await _harvest_summary(inc, (snap or {}).get("rca_json"))
            await repo.finish(iid, to_state="completed", state_reason=None,
                              result_summary=summary, agent_result=agent_result)
            counts["harvested"] += 1
            if inc.get("agent_run_id"):  # 重启补收割：owner 同样没收到过完成通知
                _notify_owner_done(inc, str(inc["agent_run_id"]), agent_result)
            continue
        stale = _age_s(inc.get("last_alert_at")) > float(cfg["alert_requeue_max_age_s"])
        if not stale and int(inc.get("retry_count") or 0) < int(cfg["alert_max_retries"]):
            await repo.requeue(iid, bump_retry=True)
            counts["requeued"] += 1
        else:
            await repo.finish(iid, to_state="failed",
                              state_reason="stale" if stale else "interrupted_by_restart",
                              result_summary=None, agent_result="failed")
            counts["failed"] += 1
    if any(counts.values()):
        log.warning("[alerts][converge] %s", counts)
    return counts


# ============================ 后台循环（lifespan） ============================


async def _poll_loop(interval_s: float) -> None:
    from alerts import ingest

    while True:
        await asyncio.sleep(interval_s)  # 先睡后跑（启动风暴/测试进程友好）
        try:
            await ingest.run_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 —— DB/网络抖动：本轮跳过，绝不退出循环
            log.warning("[alerts][poller] 本轮拉取失败", exc_info=True)


async def _dispatch_loop() -> None:
    last_purge = time.monotonic()
    while True:
        try:
            assert _kick_event is not None
            try:
                await asyncio.wait_for(_kick_event.wait(), timeout=3.0)  # kick 降延迟，短询兜底
            except asyncio.TimeoutError:
                pass
            _kick_event.clear()
            # 排队超时先翻（2026-08-14）：必须在 dispatch_once 之前，否则本轮会把已过期
            # 的单捞去起诊断白耗名额。queued ≤ queue_max，空命中 UPDATE 成本可忽略。
            expired = await repo.expire_stale_queued(
                int((await service.get_config())["alert_queue_max_age_s"]))
            if expired:
                log.warning("[alerts][dispatch] 排队超时翻接管失败 %d 单（queue_expired，"
                            "清单可见可重试）", expired)
            await dispatch_once()
            if time.monotonic() - last_purge > 3600:  # 搭车清理（学 studio drain）
                last_purge = time.monotonic()
                purged = await repo.purge_expired_events()
                if purged:
                    log.info("[alerts][purge] 过期告警事件 %d 行", purged)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.warning("[alerts][dispatcher] 本轮派发失败", exc_info=True)


async def start_background() -> dict[str, Any] | None:
    """converge 恒执行。摄入通道按档位：
    - `OPENOPS_ALERT=real` → Kafka 消费循环（契约 §2；缺配/未装 aiokafka 在此同步 fail-closed，
      由 facade 捕获打启动警告）；
    - mock 档 → `OPENOPS_ALERT_PULL_INTERVAL_S>0` 才起内存流轮询（默认 0=关，
      摄入/派发仍可经 admin :pull/:dispatch 手动驱动）。"""
    import os

    await converge_incidents()
    global _kick_event
    if os.environ.get("OPENOPS_ALERT", "mock").strip().lower() == "real":
        from alerts import kafka_source

        kafka_source.kafka_config()  # 启动期同步校验：缺配即抛，绝不静默降级
        _kick_event = asyncio.Event()
        handle = {
            "poller": asyncio.create_task(kafka_source.consume_loop()),
            "dispatcher": asyncio.create_task(_dispatch_loop()),
        }
        log.warning("[alerts] 7x24 接管已启动（Kafka 消费 + 派发循环）")
        return handle
    interval = float(os.environ.get("OPENOPS_ALERT_PULL_INTERVAL_S", "0") or "0")
    if interval <= 0:
        return None
    _kick_event = asyncio.Event()
    handle = {
        "poller": asyncio.create_task(_poll_loop(interval)),
        "dispatcher": asyncio.create_task(_dispatch_loop()),
    }
    log.warning("[alerts] 7x24 接管循环已启动（mock 轮询 pull=%ss）", interval)
    return handle


async def stop_background(handle: dict[str, Any] | None) -> None:
    """handle=None（循环未启用）也收口 worker：admin :dispatch 手动派发的协程同样要在关池前结束。"""
    global _kick_event
    handle = handle or {}
    for key in ("poller", "dispatcher"):
        task = handle.get(key)
        if task is not None:
            task.cancel()
    for worker in list(_workers):
        worker.cancel()  # 单停在 diagnosing：下次启动 converge 收敛为 requeue/failed
    pending = [t for t in [handle.get("poller"), handle.get("dispatcher"), *list(_workers)]
               if t is not None]
    if pending:
        try:
            await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=3)
        except asyncio.TimeoutError:
            pass
    _kick_event = None
