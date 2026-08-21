"""alerts 切片请求模型（响应 DTO 由 service 组装 dict，与 core 同口径）。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CreateRuleRequest(BaseModel):
    """一弹窗一规则（v2 需求）：名称=行名；类型**多选**（2026-08-07 用户拍板，任一命中即匹配；
    存量单值 category 规则由读侧兼容）；级别多选（UI 三档）；
    strategies=勾选的监控策略名集合（空=该类型全部）；prompt=诊断提示词（空=系统默认）。"""
    client_request_id: str = Field(min_length=1)
    agent_team_instance_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=120)
    categories: list[str] = Field(min_length=1, max_length=10)
    severities: list[str] = Field(default_factory=list)
    strategies: list[str] = Field(default_factory=list, max_length=100)
    prompt: str = Field(default="", max_length=4000)
    description: str = Field(default="", max_length=500)
    enabled: bool = True
    source: Literal["template", "custom"] = "custom"
    # 仅接管责任人为本人的告警（2026-08-21）：告警 alarmOwnerLname 含本人工号才触发
    owner_only: bool = False


class EnsureRuleRequest(BaseModel):
    """深链进站收口（rules:ensure）：查覆盖→可合并则合并→否则新建（重名自动后缀）。
    categories 与 CreateRuleRequest 同形留宽，service 强制本期单类型；不收 enabled（恒开）
    与 source（恒 custom）——深链产物必须立即生效且来源可辨识。"""
    client_request_id: str = Field(min_length=1)
    agent_team_instance_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=120)
    categories: list[str] = Field(min_length=1, max_length=10)
    severities: list[str] = Field(min_length=1, max_length=3)
    prompt: str = Field(default="", max_length=4000)
    description: str = Field(default="", max_length=500)


class UpdateRuleRequest(BaseModel):
    """部分更新：None = 不改该字段。"""
    client_request_id: str = Field(min_length=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    categories: list[str] | None = Field(default=None, min_length=1, max_length=10)
    severities: list[str] | None = None
    strategies: list[str] | None = Field(default=None, max_length=100)
    prompt: str | None = Field(default=None, max_length=4000)
    app_ids: list[str] | None = None
    keywords: list[str] | None = None
    label_selectors: dict[str, str] | None = None
    enabled: bool | None = None
    owner_only: bool | None = None


class BatchRulesRequest(BaseModel):
    """批量操作（配置列表复选框）：enable / disable / delete。"""
    client_request_id: str = Field(min_length=1)
    rule_ids: list[str] = Field(min_length=1, max_length=200)
    action: Literal["enable", "disable", "delete"]


class DeleteRuleRequest(BaseModel):
    client_request_id: str = Field(min_length=1)


class GrantUpdateRequest(BaseModel):
    """管理员开通/关闭某用户的告警接管（算力保护白名单）。"""
    client_request_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1, max_length=64)
    granted: bool


class IncidentActionRequest(BaseModel):
    client_request_id: str = Field(min_length=1)


class FeedbackRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    feedback: Literal["positive", "negative", "neutral"]
    note: str = Field(default="", max_length=500)


class AlertPrioritizeRequest(BaseModel):
    """管理员置顶/取消置顶（§5.3 软插队）：reason 必填写审计。"""
    client_request_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=300)
    cancel: bool = False


class AlertConfigUpdateRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    updates: dict[str, Any] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=200)
