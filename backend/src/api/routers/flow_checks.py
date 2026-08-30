"""四号校验（29.14）：pending 列表 + 决策端点（形制对齐 approvals 路由）。"""
from __future__ import annotations

from fastapi import APIRouter

from api.deps import User
from api.responses import ok
from app import run_state_service
from domain.schemas import FlowCheckDecisionRequest

router = APIRouter(prefix="/api/openops/v1", tags=["flow-checks"])


@router.get("/agent-runs/{run_id}/flow-checks")
async def pending(run_id: str, user: User):
    return ok(await run_state_service.list_pending_flow_checks(user, run_id))


@router.post("/flow-checks/{flow_check_id}:decide")
async def decide(flow_check_id: str, req: FlowCheckDecisionRequest, user: User):
    return ok(await run_state_service.decide_flow_check(user, flow_check_id, req))
