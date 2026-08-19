"""Scope Service（28.6）：运行边界 resolve → ScopeContext + scope_snapshot。

- `effective_appids` 只来自 oModel resolve（无本地 workspace→APPID 表）。
- 30 秒短 TTL 缓存（key=workspace+revision+user）：命中复用 ScopeContext 与 scope_snapshot_id，不重解不重写快照；
  缓存只做性能优化，不作失败兜底。
- fail-closed：syncing→WORKSPACE_NOT_READY、failed→SCOPE_RESOLVE_FAILED、empty→EMPTY_SCOPE，并推 scope.blocked。
- oModel 返回新 scope_revision：回写 agent_team_instance、写审计、推 scope.updated；scope_snapshot 仅审计回放用。
"""
from __future__ import annotations

import os
import time
from typing import Any

from domain.errors import ApiError, Err
from infra.external import omodel_client
from infra.redact import redact_text
from infra.repositories import agent_teams, audit, runs
from runtime import events

_TTL_S = float(os.environ.get("OPENOPS_SCOPE_TTL_S", "30"))
# (workspace_id, scope_revision, user_id) -> (expires_at_monotonic, ScopeContext)
_cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}

# peek 专用缓存：只存 effective_appids（不是 ScopeContext），故不会污染 _cache 的消费面。
# 存在理由见 peek_effective_appids 的「频率」段。
_PEEK_TTL_S = float(os.environ.get("OPENOPS_SCOPE_PEEK_TTL_S", "") or _TTL_S)
# 失败负缓存要**短**：oModel 恢复后不该还被自己压着不重试。
_PEEK_FAIL_TTL_S = float(os.environ.get("OPENOPS_SCOPE_PEEK_FAIL_TTL_S", "10"))
# (workspace_id, scope_revision, user_id) -> (expires_at_monotonic, effective_appids, resolve 是否成功)
# 第三位区分「上游答复范围为空」与「问不到」——两者 appids 都是 []，但 TTL 与可绕过性不同。
_peek_cache: dict[tuple[str, str, str], tuple[float, list[str], bool]] = {}


def _reset_cache() -> None:  # 测试隔离
    _cache.clear()
    _peek_cache.clear()


def invalidate_workspace(workspace_id: str) -> None:
    """workspace 范围内容变更后的主动失效（2026-08-19：改范围后告警仍按旧 scope 接管 30s）。

    写路径（workspace_service.update_workspace）成功后调用，清掉两张缓存里该
    workspace 的全部键——下一次 peek/resolve 立即实时解析新范围，不再硬等 TTL。
    进程内存缓存，多副本部署时其余副本仍靠 30s TTL 兜底（与现状一致）。
    """
    for cache in (_cache, _peek_cache):
        for key in [k for k in cache if k[0] == workspace_id]:
            del cache[key]


async def _blocked(run_id: str, task_id: str, instance_id: str, trace: str,
                   user_id: str, reason_code: str, msg: str) -> None:
    """fail-closed：写审计 scope.blocked + 推 openops.scope.blocked。"""
    msg = redact_text(msg, max_length=500)
    eid = await audit.insert_event(
        audit_trace_id=trace, event_type="scope.blocked", user_id=user_id,
        run_id=run_id, instance_id=instance_id, task_id=task_id, action="scope_block", reason_code=reason_code,
        payload_redacted={"summary": msg},
    )
    events.publish(run_id, events.envelope(
        run_id, "openops.scope.blocked", task_id=task_id, severity="warning",
        message=msg, reason_code=reason_code, audit_trace_id=trace, event_id=eid,
    ))


def _scope_override() -> list[str] | None:
    """联调测试缝：设 OPENOPS_SCOPE_OVERRIDE_APPIDS（逗号分隔）就用它当 effective_appids、跳过 oModel——
    oModel 服务未就绪时也能拿真 appid 联调真 MCP。空/未设=不覆盖，照常走 oModel。仅测试/联调用。"""
    raw = os.environ.get("OPENOPS_SCOPE_OVERRIDE_APPIDS", "").strip()
    return [a.strip() for a in raw.split(",") if a.strip()] or None


async def peek_effective_appids(user_id: str, instance: dict[str, Any], *,
                                fresh: bool = False) -> list[str]:
    """只读探询（告警摄入/历史预览用）：override 缝 → _cache 命中 → _peek_cache 命中 → omodel 实时 resolve。

    与 resolve_for_task 的三点**刻意**差异，别顺手"统一"：
    ① 不写 scope_snapshot——快照表 run_id NOT NULL，且快照语义是任务边界的审计证据，
      预览没有任务边界（tests/alerts 有"peek 不产生快照"守卫用例）；
    ② 不发 scope.blocked 审计/SSE——没有 run 通道可推；
    ③ 失败/空一律返回 []（调用方自行降级本地数据）——预览不是执行边界，拿不到范围
      不该拦住用户看页面；执行边界的 fail-closed 语义只归 resolve_for_task。
    只读**不写 _cache**：它的值是含 scope_snapshot_id 的完整 ScopeContext，peek 写半截
    ctx 会污染 resolve_for_task 的命中消费面（audit 要读 snapshot_id）。

    频率（2026-08-15 修）：此前 peek 只读不写**任何**缓存，于是 Kafka 每批（getmany
    timeout 1s）都真打一次 omodel——内网实测约 1 秒一次出站，上游 403 时把日志刷满，
    每次还搭一个同步 IAM 取 token 阻塞事件循环。故加 `_peek_cache`：只存 appid 列表，
    不含 snapshot_id，天然不会污染 _cache 的消费面。**失败也缓存**（更短的负 TTL）——
    当前 403 场景每批都失败，只缓存成功等于没修。

    `fresh=True`：跳过**负**缓存（成功缓存照读）。给用户点了按钮才发生的调用用——
    人手重试的间隔比负 TTL 短，让用户对着 10 秒前的一次失败干等，是拿降噪换体验。
    后台循环一律用默认 False：那里没有「用户在等」这回事，压频率才是正解。
    """
    ws_id = instance["workspace_id"]
    key = (ws_id, instance["scope_revision"], user_id)
    override = _scope_override()
    if override is not None:
        return list(override)
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and cached[0] > now:  # resolve_for_task 写的，比 peek 自己的更权威
        return list(cached[1].get("effective_appids") or [])
    peeked = _peek_cache.get(key)
    if peeked is not None and peeked[0] > now and (peeked[2] or not fresh):
        return list(peeked[1])
    try:
        res = await omodel_client.resolve_scope(ws_id, instance["scope_revision"], user_id)
    except Exception:  # noqa: BLE001 —— OModelError/网络等：预览降级，不放大为 500
        return _peek_miss(key, now)
    if str(res.get("status") or "") != "ok":
        return _peek_miss(key, now)
    appids = sorted(a for a in (res.get("effective_appids") or []) if a)
    # ok=True 即使 appids 为空：「上游明确答复范围为空」和「问不到」是两回事，
    # 前者该按正常 TTL 缓存，后者是故障、要短 TTL 且允许 fresh 绕过。
    _peek_cache[key] = (now + _PEEK_TTL_S, list(appids), True)
    return appids


def _peek_miss(key: tuple[str, str, str], now: float) -> list[str]:
    """peek 取不到范围：写短负缓存并返回 []（调用方按「宁漏勿越权」降级）。"""
    _peek_cache[key] = (now + _PEEK_FAIL_TTL_S, [], False)
    return []


async def resolve_for_task(
    user_id: str, instance: dict[str, Any], run_id: str, task_id: str, audit_trace_id: str
) -> dict[str, Any]:
    ws_id = instance["workspace_id"]
    instance_rev = instance["scope_revision"]
    instance_id = str(instance["agent_team_instance_id"])
    key = (ws_id, instance_rev, user_id)

    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and cached[0] > now:
        ctx = dict(cached[1])
        ctx["cache_hit"] = True
        return ctx  # 复用 ScopeContext + scope_snapshot_id（28.6：不重解、不重写快照）

    override = _scope_override()
    if override is not None:  # 联调缝：跳过 oModel，用真 appid 当 scope（omodel_request_id 打标记，审计可辨）
        res: dict[str, Any] = {"status": "ok", "effective_appids": override,
                               # 覆盖缝只有裸 appid，无名字/企业来源
                               "scope_apps": [{"appid": a, "name": "", "enterprise_id": ""} for a in override],
                               "scope_revision": instance_rev, "omodel_request_id": "scope-override"}
    else:
        res = await omodel_client.resolve_scope(ws_id, instance_rev, user_id)
    status = res["status"]
    if status == "syncing":
        await _blocked(run_id, task_id, instance_id, audit_trace_id, user_id, "WORKSPACE_NOT_READY", "workspace 未就绪")
        raise ApiError(Err.WORKSPACE_NOT_READY, "workspace 未就绪（fail-closed）", retryable=True)
    if status != "ok":
        # 原因必达（内网实测：光秃 SCOPE_RESOLVE_FAILED 无法定位——常见=实例绑的 workspace 是
        # mock 时代旧 id / 已在 umodel 删除，check-net ③ 可列真实 workspaces 核对）
        why = str(res.get("reason") or "上游未给原因")
        msg = (f"范围解析失败（fail-closed）：workspace={ws_id}，{why}。"
               "该实例绑定的系统范围可能已失效——用初始化向导从真实应用树新建 Agent，或核对 check-net ③")
        await _blocked(run_id, task_id, instance_id, audit_trace_id, user_id, "SCOPE_RESOLVE_FAILED", msg)
        raise ApiError(Err.SCOPE_RESOLVE_FAILED, msg, retryable=True)
    appids = res["effective_appids"]
    if not appids:
        await _blocked(run_id, task_id, instance_id, audit_trace_id, user_id, "EMPTY_SCOPE", "有效范围为空")
        raise ApiError(Err.EMPTY_SCOPE, "有效范围为空，禁止平台工具调用")  # SCOPE-003

    new_rev = res.get("scope_revision", instance_rev)
    if new_rev != instance_rev:  # 范围有变：回写实例 + 审计 + scope.updated
        await agent_teams.update_scope_revision(instance_id, new_rev, user_id)
        message = f"工作范围已更新到新版本（{new_rev}）"
        eid = await audit.insert_event(
            audit_trace_id=audit_trace_id, event_type="scope.updated", user_id=user_id,
            run_id=run_id, instance_id=instance_id, task_id=task_id, action="scope_update",
            payload_redacted={"from": instance_rev, "to": new_rev, "summary": message},
        )
        events.publish(run_id, events.envelope(
            run_id, "openops.scope.updated", task_id=task_id,
            message=message, payload={"scope_revision": new_rev},
            audit_trace_id=audit_trace_id, event_id=eid,
        ))

    snapshot_id = await runs.insert_scope_snapshot(
        user_id, instance_id, run_id, task_id, ws_id, new_rev, appids, res["omodel_request_id"], "task_start",
    )
    ctx = {
        "scope_snapshot_id": snapshot_id, "effective_appids": appids,
        # scope_apps：`[{appid, name, enterprise_id}]`，effective_appids 的纯显示装饰（list_scope_apps 用）。
        # 用 .get 兜底——不返回该键的实现（旧 mock/三方）不得在任务启动处 KeyError。
        "scope_apps": res.get("scope_apps") or [],
        "scope_revision": new_rev,
        "workspace_id": ws_id,  # 出站平台 MCP header 用（omodel 工作空间 id，ws_id 见上）
        "omodel_request_id": res["omodel_request_id"], "cache_hit": False,
    }
    expires = now + _TTL_S
    _cache[key] = (expires, ctx)
    if new_rev != instance_rev:  # 下次以新 revision 为 key 也能命中
        _cache[(ws_id, new_rev, user_id)] = (expires, ctx)
    return ctx


async def resolve_from_last_snapshot(
    user_id: str, instance: dict[str, Any], run_id: str, task_id: str, audit_trace_id: str
) -> dict[str, Any] | None:
    """夜间降级（仅告警派发链调用）：登录态缺失致 resolve 失败时，复用实例最近一次快照。

    插入**新**快照行（compute_reason='alert_snapshot_fallback'，task_id 对得上）保持审计链完整；
    ctx 带 degraded=True 随 task 快照落盘留痕，omodel_request_id='snapshot-fallback' 让
    start_task 的 scope.resolved 审计一眼可辨降级。无历史快照/快照为空 → None（调用方按原错抛）。
    不写 _cache：降级结果不该被交互任务命中复用。
    """
    _ = audit_trace_id  # 审计由 start_task 的 scope.resolved 统一记录（external_request_id 可辨）
    instance_id = str(instance["agent_team_instance_id"])
    last = await runs.latest_scope_snapshot_by_instance(instance_id)
    if last is None:
        return None
    # 范围已变更（实例行 revision 被写路径推进）后旧快照作废——用旧边界起诊断=越权
    # （2026-08-19，宁漏勿越权口径同 2026-08-11 拍板）。revision 未变的夜间降级不受影响。
    if str(last.get("scope_revision") or "") != str(instance.get("scope_revision") or ""):
        return None
    appids = list(last.get("effective_appids_snapshot") or [])
    if not appids:
        return None
    ws_id = instance["workspace_id"]
    rev = str(last.get("scope_revision") or instance["scope_revision"] or "")
    snapshot_id = await runs.insert_scope_snapshot(
        user_id, instance_id, run_id, task_id, ws_id, rev, appids,
        "snapshot-fallback", "alert_snapshot_fallback",
    )
    return {
        "scope_snapshot_id": snapshot_id, "effective_appids": appids,
        "scope_apps": [],  # 降级无名字装饰来源；工具面只吃 effective_appids
        "scope_revision": rev, "workspace_id": ws_id,
        "omodel_request_id": "snapshot-fallback", "cache_hit": False, "degraded": True,
    }
