"""Agent Studio（管理员回溯复盘）：按用户查 run 列表 + run 详情按 agent 实例分组。

数据源 = sre_agent_studio_span（span 原文，采集见 runtime/studio_tracing）联合
sre_agent_delegation（主↔子交接账本）。**仅 /admin/studio/* 路由调用**（Admin 依赖已把
非 platform_admin 挡在 403）——本层不再做归属校验，管理员对全部用户可见是功能定位。
"""
from __future__ import annotations

from typing import Any

from app import agui_service
from domain.errors import ApiError, Err
from infra.db import row_json
from infra.repositories import agent_session_states, delegations, runs, studio_spans

_EMPTY_STATS = {"agents": 0, "llm_calls": 0, "tool_calls": 0, "total_tokens": 0, "latency_ms": 0.0}


async def list_user_runs(user_id: str, page: int, page_size: int) -> dict[str, Any]:
    offset = (page - 1) * page_size
    rows, total = await runs.list_runs_by_user_paged(user_id, limit=page_size, offset=offset)
    stats = await studio_spans.stats_by_run_ids([str(r["agent_run_id"]) for r in rows])
    items = []
    for r in rows:
        item = row_json(r)
        items.append({
            "agent_run_id": item["agent_run_id"],
            "run_title": item.get("run_title") or "",
            "run_status": item.get("run_status") or "",
            "started_at": item.get("started_at"),
            "ended_at": item.get("ended_at"),
            "deleted": item.get("deleted_at") is not None,
            "framework_session_id": item.get("framework_session_id") or "",
            "stats": stats.get(item["agent_run_id"], dict(_EMPTY_STATS)),
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def run_messages(run_id: str) -> list[dict[str, str]]:
    """run 的对话记录（用户↔助手全文）：与用户端 /agent-runs/{id}/messages 同一投影
    （agui_service.project_transcript），管理员看到的与用户自己看到的完全一致。

    差异仅在 run 查询含软删（管理员复盘不受用户删会话影响）。无脱敏无截断——
    与 span 表同一功能定位，仅 /admin/studio/* 可读。mock runtime 不落 AgentState → 空列表。
    """
    run = await runs.get_run(run_id, include_deleted=True)
    if run is None:
        raise ApiError(Err.NOT_FOUND, "Run 不存在")
    state = await agent_session_states.get_state_json(str(run["framework_session_id"]), "main")
    return agui_service.project_transcript(state, run_id)


async def run_replay(user: dict[str, Any], run_id: str) -> dict[str, Any]:
    """用户自查回放（对话界面侧栏「回放」）：owner-only，软删 404（同 /messages 口径）。

    与管理员版 run_detail 共用 _group_spans，仅两点差异在此收口（不靠前端隐藏，防 API
    直连绕过）：① 归属校验；② LLM 调用的输入 messages 置空——内含平台系统提示词/人设/
    skill 提示，不对普通用户外露。输出/token/工具入参结果/主↔子交接全量可见。
    """
    run = await runs.get_run(run_id)  # 不含软删
    if run is None:
        raise ApiError(Err.NOT_FOUND, "Run 不存在")
    if run["user_id"] != user["user_id"]:
        raise ApiError(Err.FORBIDDEN, "无权查看该 Run")  # IAM-005
    spans = await studio_spans.list_spans_by_run(run_id)
    dels = await delegations.list_by_run(run_id)
    detail = _group_spans(run, spans, dels)
    for a in detail["agents"]:
        for c in a["calls"]:
            if c.get("kind") == "llm":
                c["input"] = ""
    return detail


async def run_detail(run_id: str) -> dict[str, Any]:
    run = await runs.get_run(run_id, include_deleted=True)  # 管理员复盘不受用户软删影响
    if run is None:
        raise ApiError(Err.NOT_FOUND, "Run 不存在")
    spans = await studio_spans.list_spans_by_run(run_id)
    dels = await delegations.list_by_run(run_id)
    return _group_spans(run, spans, dels)


def _handover_of(d: dict[str, Any]) -> dict[str, Any]:
    item = row_json(d)
    return {
        "delegation_id": item["delegation_id"],
        "agent_key": item.get("agent_key") or "",
        "task_text": item.get("task_text") or "",
        "report_text": item.get("report_text") or "",
        "delegation_status": item.get("delegation_status") or "",
        "dispatch_batch_no": item.get("dispatch_batch_no"),
        "had_final_report": bool(item.get("had_final_report")),
        "creation_date": item.get("creation_date"),
    }


def _match_handover(session_id: str, handovers: list[dict[str, Any]]) -> dict[str, Any] | None:
    """子 Agent 卡 ↔ 派发账本：session_id = `{fsid}:{agent_key}-{delegation_id 前 8 位}`
    （见 subagent_dispatch._run_one 的 AgentState 构造）。解析失败 → None，不抛。"""
    try:
        _, suffix = session_id.split(":", 1)
        agent_key, did8 = suffix.rsplit("-", 1)
    except ValueError:
        return None
    for h in handovers:
        if h["agent_key"] == agent_key and str(h["delegation_id"])[:8] == did8:
            return h
    return None


def _group_spans(run: dict[str, Any], rows: list[dict[str, Any]],
                 dels: list[dict[str, Any]]) -> dict[str, Any]:
    """span 行按 (session_id, agent_role) 分组成 Agent 卡（移植老 _studio_group_spans）：
    main = framework_session_id 那张卡排最前；`agent` 类 span 取显示名 + agent 层输入输出
    （交接内容 span 侧来源）；llm/tool 进 calls 并累加指标；另出 run 级 rollup。"""
    run_j = row_json(run)
    fsid = run_j.get("framework_session_id") or ""
    handovers = [_handover_of(d) for d in dels]

    agents: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for raw in rows:
        r = row_json(raw)
        sid = r.get("session_id") or ""
        role = r.get("agent_role") or ""
        key = (sid, role)
        g = agents.get(key)
        if g is None:
            g = {
                "session_id": sid,
                "role": role,
                "agent_name": "",
                "is_main": sid == fsid,
                "agent_io": None,
                "handover": None if sid == fsid else _match_handover(sid, handovers),
                "calls": [],
                "totals": {
                    "llm_calls": 0, "tool_calls": 0,
                    "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                    "latency_ms": 0.0,
                },
            }
            agents[key] = g
            order.append(key)
        kind = r.get("kind") or ""
        if kind == "agent":
            if r.get("agent_name"):
                g["agent_name"] = r["agent_name"]
            # 同 agent 多次 reply（同 run 多 task）：保留最后一次非空输入输出即可
            if r.get("input_messages") or r.get("output_messages"):
                g["agent_io"] = {
                    "input": r.get("input_messages") or "",
                    "output": r.get("output_messages") or "",
                }
            continue
        in_tok = int(r.get("input_tokens") or 0)
        out_tok = int(r.get("output_tokens") or 0)
        lat = float(r.get("latency_ms") or 0.0)
        if kind == "llm":
            g["calls"].append({
                "kind": "llm",
                "model": r.get("model") or "",
                "provider": r.get("provider") or "",
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "cache_input_tokens": int(r.get("cache_input_tokens") or 0),
                "latency_ms": lat,
                "started_at": r.get("started_at"),
                "finish_reason": r.get("finish_reason") or "",
                "input": r.get("input_messages") or "",
                "output": r.get("output_messages") or "",
                "status": r.get("span_status") or "",
            })
            g["totals"]["llm_calls"] += 1
            g["totals"]["input_tokens"] += in_tok
            g["totals"]["output_tokens"] += out_tok
            g["totals"]["total_tokens"] += in_tok + out_tok
        elif kind == "tool":
            g["calls"].append({
                "kind": "tool",
                "tool_name": r.get("tool_name") or "",
                "tool_args": r.get("tool_args") or "",
                "tool_result": r.get("tool_result") or "",
                "latency_ms": lat,
                "started_at": r.get("started_at"),
                "status": r.get("span_status") or "",
            })
            g["totals"]["tool_calls"] += 1
        g["totals"]["latency_ms"] += lat

    agent_list = [agents[k] for k in order]
    agent_list.sort(key=lambda a: (not a["is_main"],))  # main 最前，其余保持时间序（sort 稳定）

    models: list[str] = []
    seen_models: set[str] = set()
    rollup: dict[str, Any] = {
        "agents": len(agent_list), "llm_calls": 0, "tool_calls": 0,
        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        "total_latency_ms": 0.0, "models": models,
    }
    for a in agent_list:
        t = a["totals"]
        rollup["llm_calls"] += t["llm_calls"]
        rollup["tool_calls"] += t["tool_calls"]
        rollup["input_tokens"] += t["input_tokens"]
        rollup["output_tokens"] += t["output_tokens"]
        rollup["total_tokens"] += t["total_tokens"]
        rollup["total_latency_ms"] += t["latency_ms"]
        for c in a["calls"]:
            m = c.get("model")
            if m and m not in seen_models:
                seen_models.add(m)
                models.append(m)

    return {
        "run": {
            "agent_run_id": run_j["agent_run_id"],
            "user_id": run_j.get("user_id") or "",
            "run_title": run_j.get("run_title") or "",
            "run_status": run_j.get("run_status") or "",
            "started_at": run_j.get("started_at"),
            "ended_at": run_j.get("ended_at"),
            "deleted": run_j.get("deleted_at") is not None,
            "framework_session_id": fsid,
        },
        "agents": agent_list,
        "rollup": rollup,
        "delegations": handovers,  # 全量账本：零 span 的派发（如启动即失败）也可见
    }
