from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, UploadFile

from api.deps import User
from api.responses import ok
from app import asset_reconcile_service, asset_registry_service
from domain.errors import ApiError, Err
from domain.schemas import RegisterMcpRequest, UploadSkillRequest

router = APIRouter(prefix="/api/openops/v1/assets", tags=["assets"])

_MAX_SKILL_ZIP = 50 * 1024 * 1024  # 29.3 §2.1：ZIP ≤ 50MB


def _parse_tags(raw: str) -> list[str]:
    """tags 兼容 JSON 数组串 `["a","b"]` 或逗号分隔 `a,b`（中英文逗号）。"""
    raw = (raw or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            return [str(x).strip() for x in json.loads(raw) if str(x).strip()]
        except (ValueError, TypeError):
            pass
    return [t.strip() for t in raw.replace("，", ",").split(",") if t.strip()]


@router.post(":reconcile")
async def reconcile(_user: User):
    """配置页 refresh：立即对账 Skill Hub / MCP Registry（28.7）。"""
    return ok(await asset_reconcile_service.reconcile(force=True, trigger="refresh"))


@router.get("/skills")
async def list_skills(user: User):
    return ok(await asset_registry_service.list_skills(user))


@router.post("/skills")
async def upload_skill(req: UploadSkillRequest, user: User):
    return ok(await asset_registry_service.upload_skill(user, req))


@router.post("/skills:upload")
async def upload_skill_package(
    user: User,
    file: UploadFile = File(...),
    category: str = Form(""),  # 分类/标签已从上传流程移除：可选，缺省即不带（仍接受显式传入）
    tags: str = Form(""),
):
    """上传 Skill ZIP（29.3 §2.1 multipart）：转发 SkillHub + 写本地目录即时可见。分类/标签可选。"""
    data = await file.read()
    if len(data) > _MAX_SKILL_ZIP:
        raise ApiError(Err.VALIDATION_FAILED, "ZIP 超过 50MB 上限（29.3 §2.1）")
    if data[:2] != b"PK":  # ZIP 魔数——非 zip 直接拒，避免下游 BadZipFile 迷惑定位
        raise ApiError(Err.SKILL_PACKAGE_INVALID, "文件不是有效的 ZIP 包")
    return ok(await asset_registry_service.upload_skill_package(
        user, file.filename or "skill.zip", data, category.strip(), _parse_tags(tags)))


@router.delete("/skills/{skill_id}")
async def delete_skill(skill_id: str, user: User):
    await asset_registry_service.delete_skill(user, skill_id)
    return ok({"deleted": True})


@router.get("/mcps")
async def list_mcps(user: User):
    return ok(await asset_registry_service.list_mcps(user))


@router.post("/mcps")
async def register_mcp(req: RegisterMcpRequest, user: User):
    return ok(await asset_registry_service.register_mcp(user, req))


@router.delete("/mcps/{mcp_id}")
async def delete_mcp(mcp_id: str, user: User):
    await asset_registry_service.delete_mcp(user, mcp_id)
    return ok({"deleted": True})
