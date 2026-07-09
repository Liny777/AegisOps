from __future__ import annotations

from fastapi import APIRouter

from api.deps import User
from api.responses import ok
from app import run_state_service
from domain.schemas import DecisionRequest

router = APIRouter(prefix="/api/openops/v1", tags=["approvals"])


@router.get("/agent-runs/{run_id}/approvals")
async def pending(run_id: str, user: User):
    return ok(await run_state_service.list_pending(user, run_id))


@router.post("/approvals/{approval_id}:decide")
async def decide(approval_id: str, req: DecisionRequest, user: User):
    return ok(await run_state_service.decide_approval(user, approval_id, req))
