from __future__ import annotations

from fastapi import APIRouter

from api.deps import User
from api.responses import ok
from app import audit_trace_service

router = APIRouter(prefix="/api/openops/v1/audit", tags=["audit"])


@router.get("/runs/{run_id}")
async def run_events(run_id: str, user: User):
    return ok(await audit_trace_service.run_events(user, run_id))


@router.get("/traces/{trace_id}")
async def by_trace(trace_id: str, user: User):
    return ok(await audit_trace_service.by_trace(user, trace_id))
