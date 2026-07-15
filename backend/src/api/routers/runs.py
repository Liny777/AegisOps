from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Query
from fastapi.responses import StreamingResponse

from api.deps import User
from api.responses import ok
from app import agui_service, event_stream_service, run_state_service
from domain.schemas import CreateRunRequest, RenameRunRequest, SelectModelRequest, StartTaskRequest
from runtime import events

router = APIRouter(prefix="/api/openops/v1", tags=["runs"])


@router.get("/agent-runs")
async def list_runs(user: User):
    return ok(await run_state_service.list_runs(user))


@router.post("/agent-runs")
async def create_run(req: CreateRunRequest, user: User):
    return ok(await run_state_service.create_run(user, req))


@router.get("/agent-runs/{run_id}/state")
async def state(run_id: str, user: User):
    return ok(await run_state_service.get_state(user, run_id))


@router.get("/agent-runs/{run_id}/messages")
async def messages(run_id: str, user: User):
    """历史对话 transcript（重进会话前端自渲染用）：run→framework_session_id→已持久化 AgentState→
    投影成 [{role, content}]。CopilotKit v2 的 connect 走 sidecar 内存回放、拿不到历史，故前端绕开它
    直接拉本端点。数据源与活动栏无关（活动栏走 sre_audit_event）。"""
    await run_state_service.owned_run(user["user_id"], run_id)
    return ok(await agui_service._load_transcript(run_id))


@router.get("/agent-runs/{run_id}/events")
async def events_page(
    run_id: str,
    user: User,
    before: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
):
    """活动历史游标分页；实时事件仍走 /events/stream。"""
    return ok(await run_state_service.list_events(user, run_id, before=before, limit=limit))


@router.post("/agent-runs/{run_id}/tasks")
async def start_task(run_id: str, req: StartTaskRequest, user: User):
    return ok(await run_state_service.start_task(user, run_id, req))


@router.post("/tasks/{task_id}:cancel")
async def cancel_task(task_id: str, user: User):
    return ok(await run_state_service.cancel_task(user, task_id))


@router.post("/agent-runs/{run_id}:delete")
async def delete_run(run_id: str, user: User):
    return ok(await run_state_service.delete_run(user, run_id))


@router.post("/agent-runs/{run_id}:rename")
async def rename_run(run_id: str, req: RenameRunRequest, user: User):
    return ok(await run_state_service.rename_run(user, run_id, req))


@router.post("/agent-runs/{run_id}:close")
async def close_run(run_id: str, user: User):
    return ok(await run_state_service.close_run(user, run_id))


@router.post("/agent-runs/{run_id}:select-model")
async def select_model(run_id: str, req: SelectModelRequest, user: User):
    return ok(await run_state_service.select_model(user, run_id, req))


@router.post("/agent-runs/{run_id}/ag-ui")
async def ag_ui_batch(run_id: str, user: User):
    """批量事件端点（兼容保留）；实时走 SSE /events/stream。"""
    await run_state_service.owned_run(user["user_id"], run_id)
    return ok({"events": events.snapshot(run_id)})


@router.post("/agent-runs/{run_id}/agui")
async def agui_run(run_id: str, body: dict[str, Any], user: User):
    """AG-UI 运行态端点（B5，30.4）：RunAgentInput → 启动 task → AG-UI 标准事件流。

    校验/启动在流开始前完成（错误走标准错误 envelope）；断线恢复走 GET /state。
    """
    await run_state_service.owned_run(user["user_id"], run_id)
    ctx = await agui_service.start(user, run_id, body)
    return StreamingResponse(
        agui_service.stream(ctx),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/agent-runs/{run_id}/events/stream")
async def events_stream(
    run_id: str,
    user: User,
    last_event_id: Annotated[str | None, Header()] = None,
):
    """AG-UI over SSE（28.9）：id=sequence 支持 Last-Event-ID 补发；心跳注释。"""
    await run_state_service.owned_run(user["user_id"], run_id)
    leid = int(last_event_id) if last_event_id and last_event_id.isdigit() else None
    return StreamingResponse(
        event_stream_service.stream(run_id, leid),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
