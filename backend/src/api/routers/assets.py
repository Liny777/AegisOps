from __future__ import annotations

from fastapi import APIRouter, File, Form, Query, UploadFile

from api.deps import User
from api.responses import ok
from api.skill_upload import check_skill_zip, parse_tags  # 与管理台上传共用同一套上限/魔数/tags 口径
from app import asset_reconcile_service, asset_registry_service
from domain.schemas import RegisterMcpRequest, UploadSkillRequest

router = APIRouter(prefix="/api/openops/v1/assets", tags=["assets"])


@router.post(":reconcile")
async def reconcile(user: User):
    """配置页 refresh：立即对账 Skill Hub / MCP Registry（28.7）+ 强制重拉当前用户的个人 skill。"""
    asset_registry_service.invalidate_user_skill_sync(user["user_id"])  # 清节流：随后列表 GET 强制重同步个人 skill
    return ok(await asset_reconcile_service.reconcile(force=True, trigger="refresh"))


@router.get("/skills")
async def list_skills(
    user: User,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),  # 上限对齐 29.3 §2.2 的 page_size ≤ 100
    source_type: str | None = Query(default=None, pattern="^(platform|user)$"),
    q: str | None = Query(default=None, max_length=200),  # 按名称/skill_key 模糊搜（服务端过滤）
):
    """分页列 Skill → {items,total,page,page_size}（管理台按 source_type=platform 取基线；插件页按来源分组）。"""
    return ok(await asset_registry_service.list_skills(
        user, page=page, page_size=page_size, source_type=source_type, q=q))


@router.get("/skills/{skill_key}/detail")
async def skill_detail(skill_key: str, user: User):
    """Skill 真详情（29.3 §2.4，含 SKILL.md 全文）；上游失败降级本地描述。插件页点开时调。"""
    return ok(await asset_registry_service.skill_detail(user, skill_key))


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
    check_skill_zip(data)
    return ok(await asset_registry_service.upload_skill_package(
        user, file.filename or "skill.zip", data, category.strip(), parse_tags(tags)))


@router.delete("/skills/{skill_id}")
async def delete_skill(skill_id: str, user: User):
    await asset_registry_service.delete_skill(user, skill_id)
    return ok({"deleted": True})


@router.get("/mcps")
async def list_mcps(
    user: User,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),  # 上限对齐 29.3 §3.2
    source_type: str | None = Query(default=None, pattern="^(platform|user)$"),
    q: str | None = Query(default=None, max_length=200),
):
    """分页列 HTTP MCP → {items,total,page,page_size}。"""
    return ok(await asset_registry_service.list_mcps(
        user, page=page, page_size=page_size, source_type=source_type, q=q))


@router.get("/mcps/{mcp_id}/detail")
async def mcp_detail(mcp_id: str, user: User):
    """MCP 真详情（29.3 §3.3）；上游失败降级本地描述。插件页点开时调。"""
    return ok(await asset_registry_service.mcp_detail(user, mcp_id))


@router.post("/mcps")
async def register_mcp(req: RegisterMcpRequest, user: User):
    return ok(await asset_registry_service.register_mcp(user, req))


@router.delete("/mcps/{mcp_id}")
async def delete_mcp(mcp_id: str, user: User):
    await asset_registry_service.delete_mcp(user, mcp_id)
    return ok({"deleted": True})
