from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse

from api.deps import User
from api.responses import ok
from app import event_stream_service, run_state_service
from domain.schemas import CreateRunRequest, SelectModelRequest, StartTaskRequest
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


@router.post("/agent-runs/{run_id}/tasks")
async def start_task(run_id: str, req: StartTaskRequest, user: User):
    return ok(await run_state_service.start_task(user, run_id, req))


@router.post("/tasks/{task_id}:cancel")
async def cancel_task(task_id: str, user: User):
    return ok(await run_state_service.cancel_task(user, task_id))


@router.post("/agent-runs/{run_id}:close")
async def close_run(run_id: str, user: User):
    return ok(await run_state_service.close_run(user, run_id))


@router.post("/agent-runs/{run_id}:select-model")
async def select_model(run_id: str, req: SelectModelRequest, user: User):
    return ok(await run_state_service.select_model(user, run_id, req))


@router.post("/agent-runs/{run_id}/ag-ui")
async def ag_ui_batch(run_id: str, user: User):
    """批量事件端点（兼容保留）；实时走 SSE /events/stream。"""
    await run_state_service._owned_run(user["user_id"], run_id)  # noqa: SLF001
    return ok({"events": events.snapshot(run_id)})


@router.get("/agent-runs/{run_id}/events/stream")
async def events_stream(
    run_id: str,
    user: User,
    last_event_id: Annotated[str | None, Header()] = None,
):
    """AG-UI over SSE（28.9）：id=sequence 支持 Last-Event-ID 补发；心跳注释。"""
    await run_state_service._owned_run(user["user_id"], run_id)  # noqa: SLF001
    leid = int(last_event_id) if last_event_id and last_event_id.isdigit() else None
    return StreamingResponse(
        event_stream_service.stream(run_id, leid),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
