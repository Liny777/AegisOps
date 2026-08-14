"""alerts 切片自有路由。

用户面 `/api/openops/v1/alerts/*`（User：白名单 + owner 校验在 service 层），
管理面 `/api/openops/v1/admin/alerts/*`（Admin）。动作用冒号（对齐 runs/agent_teams 先例）。
清单/详情/动作类端点（incidents/summary）在 B6 步补齐——只改本文件与 service，core 零改动。
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from alerts import service
from alerts.schemas import (
    AlertConfigUpdateRequest,
    BatchRulesRequest,
    CreateRuleRequest,
    DeleteRuleRequest,
    FeedbackRequest,
    GrantUpdateRequest,
    IncidentActionRequest,
    SubscriptionUpdateRequest,
    UpdateRuleRequest,
)
from api.deps import Admin, User
from api.responses import ok

user_router = APIRouter(prefix="/api/openops/v1/alerts", tags=["alerts"])
admin_router = APIRouter(prefix="/api/openops/v1/admin/alerts", tags=["alerts"])


# ---- 规则模板（快速配置的单一事实源） ----
@user_router.get("/rule-templates")
async def rule_templates(_user: User):
    return ok(service.rule_templates())


# ---- 规则 CRUD ----
@user_router.get("/rules")
async def list_rules(user: User, instance_id: str = Query(min_length=1)):
    return ok(await service.list_rules(user, instance_id))


@user_router.post("/rules")
async def create_rule(req: CreateRuleRequest, user: User):
    return ok(await service.create_rule(user, req))


@user_router.post("/rules:batch")
async def batch_rules(req: BatchRulesRequest, user: User):
    return ok(await service.batch_rules(user, req))


@user_router.post("/rules/{rule_id}:update")
async def update_rule(rule_id: str, req: UpdateRuleRequest, user: User):
    return ok(await service.update_rule(user, rule_id, req))


@user_router.post("/rules/{rule_id}:delete")
async def delete_rule(rule_id: str, req: DeleteRuleRequest, user: User):
    return ok(await service.delete_rule(user, rule_id, req))


# ---- 实例级订阅（总开关） ----
@user_router.get("/subscription")
async def get_subscription(user: User, instance_id: str = Query(min_length=1)):
    return ok(await service.get_subscription(user, instance_id))


@user_router.post("/subscription:update")
async def update_subscription(req: SubscriptionUpdateRequest, user: User):
    return ok(await service.update_subscription(user, req))


# ---- 事件清单（全量告警视角，v2 需求 1.3） ----
@user_router.get("/events")
async def list_events(
    user: User,
    instance_id: str | None = Query(default=None),
    alert_status: str | None = Query(default=None, description="closed|assigned|unassigned"),
    severity: str | None = Query(default=None, description="逗号分隔：fatal,critical,warning"),
    takeover: str | None = Query(default=None, description="done|processing|none"),
    category: str | None = Query(default=None, max_length=64),
    search: str | None = Query(default=None, max_length=200),
    since_days: int | None = Query(default=None, ge=1, le=30,
                                   description="只看最近 N 天（配置弹窗预览=3）"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    return ok(await service.list_alert_events(
        user, instance_id=instance_id, alert_status=alert_status, severities=severity,
        takeover=takeover, category=category, search=search, since_days=since_days,
        page=page, page_size=page_size))


@admin_router.get("/events")
async def admin_list_events(
    admin: Admin,
    user_id: str | None = Query(default=None, description="按用户查看（空=全量任意 owner）"),
    alert_status: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    takeover: str | None = Query(default=None),
    category: str | None = Query(default=None, max_length=64),
    search: str | None = Query(default=None, max_length=200),
    since_days: int | None = Query(default=None, ge=1, le=30),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    return ok(await service.admin_list_alert_events(
        admin, user_id=user_id, alert_status=alert_status, severities=severity,
        takeover=takeover, category=category, search=search, since_days=since_days,
        page=page, page_size=page_size))


# ---- 功能开通探询（白名单闸；算力保护） ----
@user_router.get("/access")
async def takeover_access(user: User):
    return ok(await service.takeover_access(user))


# ---- 历史告警预览（规则编辑器第二步；主路径=平台 alarm_list，失败降级本地库） ----
@user_router.get("/history-preview")
async def history_preview(
    user: User,
    instance_id: str = Query(min_length=1),
    categories: str = Query(min_length=1, description="逗号分隔，开放枚举（MySQL,PostgreSQL,Docker,…）"),
    severity: str | None = Query(default=None, description="逗号分隔：fatal,critical,warning（缺省=三档全选）"),
    since_days: int = Query(default=7, ge=1, le=30, description="近 N 天窗口（编辑器下拉 3|7）"),
    page_size: int = Query(default=20, ge=1, le=100),
):
    return ok(await service.history_preview(
        user, instance_id=instance_id, categories=categories, severities=severity,
        since_days=since_days, page_size=page_size))


# ---- 接管清单（incidents） ----
@user_router.get("/incidents")
async def list_incidents(
    user: User,
    instance_id: str | None = Query(default=None),
    state: str | None = Query(default=None, description="逗号分隔：queued,diagnosing,…"),
    severity: str | None = Query(default=None, description="逗号分隔：fatal,critical,…"),
    search: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    return ok(await service.list_incidents(user, instance_id=instance_id, states=state,
                                           severities=severity, search=search,
                                           page=page, page_size=page_size))


@user_router.get("/summary")
async def get_summary(user: User, instance_id: str | None = Query(default=None)):
    return ok(await service.summary(user, instance_id))


@user_router.get("/incidents/{incident_id}")
async def get_incident(incident_id: str, user: User):
    return ok(await service.get_incident_detail(user, incident_id))


@user_router.post("/incidents/{incident_id}:ignore")
async def ignore_incident(incident_id: str, _req: IncidentActionRequest, user: User):
    return ok(await service.ignore_incident(user, incident_id))


@user_router.post("/incidents/{incident_id}:retry")
async def retry_incident(incident_id: str, _req: IncidentActionRequest, user: User):
    return ok(await service.retry_incident(user, incident_id))


@user_router.post("/incidents/{incident_id}:feedback")
async def feedback_incident(incident_id: str, req: FeedbackRequest, user: User):
    return ok(await service.feedback_incident(user, incident_id,
                                              feedback=req.feedback, note=req.note))


# ---- 管理面：alert 域热更新配置 + 手动拉取 ----
@admin_router.get("/config")
async def get_config(_admin: Admin):
    # bounds/env_locked 供管理台运行参数面板：渲染输入范围 + 禁用被 env 钉死的键
    return ok({"config": await service.get_config(), "bounds": service.config_bounds(),
               "env_locked": service.env_locked_keys()})


@admin_router.post("/config:update")
async def update_config(req: AlertConfigUpdateRequest, admin: Admin):
    return ok({"config": await service.update_config(admin, req)})


@admin_router.post(":pull")
async def manual_pull(_admin: Admin):
    """手动触发一轮拉取（联调/演示用；与后台 poller 共用 run_once 单轮入口）。"""
    from alerts import ingest

    return ok({"counters": await ingest.run_once()})


@admin_router.post(":dispatch")
async def manual_dispatch(_admin: Admin):
    """手动触发一轮派发（联调/演示用；与后台循环共用 dispatch_once）。"""
    from alerts import dispatcher

    return ok({"started": await dispatcher.dispatch_once()})


@admin_router.get("/grants")
async def admin_list_grants(admin: Admin):
    return ok(await service.admin_list_grants(admin))


@admin_router.post("/grants:update")
async def admin_set_grant(req: GrantUpdateRequest, admin: Admin):
    return ok(await service.admin_set_grant(
        admin, target_user_id=req.user_id, granted=req.granted,
        client_request_id=req.client_request_id))


@admin_router.get("/overview")
async def overview(_admin: Admin):
    return ok(await service.admin_overview())
