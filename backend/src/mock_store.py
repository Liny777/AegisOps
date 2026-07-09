from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def expires_in(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class MockStore:
    """In-memory store for the V1 prototype backend.

    The real implementation will replace this with PG repositories and runtime
    services while keeping the API contract stable.
    """

    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {
            "0026demo01": {
                "user_id": "user_0026demo01",
                "login_key": "0026demo01",
                "display_name": "林一",
                "role": "user",
                "whitelisted": True,
            },
            "admin": {
                "user_id": "user_admin",
                "login_key": "admin",
                "display_name": "平台管理员",
                "role": "platform_admin",
                "whitelisted": True,
            },
        }
        self.templates: dict[str, dict[str, Any]] = {
            "tpl_sre_fast_recovery": {
                "template_id": "tpl_sre_fast_recovery",
                "template_key": "sre_fast_recovery",
                "display_name": "感知快恢 Agent 模板",
                "description": "主 Agent 调度巡检、定界、恢复相关子 Agent。",
                "status": "published",
                "latest_version": {
                    "template_version_id": "tplv_sre_fast_recovery_1",
                    "version_no": 1,
                    "status": "published",
                },
                "agents": [
                    {"agent_key": "main", "display_name": "主 Agent", "editable": True},
                    {"agent_key": "inspect", "display_name": "巡检子 Agent", "editable": False},
                    {"agent_key": "diagnose", "display_name": "定界子 Agent", "editable": False},
                    {"agent_key": "recover", "display_name": "恢复子 Agent", "editable": False},
                ],
            }
        }
        self.workspaces: dict[str, dict[str, Any]] = {
            "ws_pay_abc": {
                "workspace_id": "ws_pay_abc",
                "display_name": "支付域 ABC 工作范围",
                "scope_revision": "rev-20260708-001",
                "status": "ready",
                "effective_appids_preview": ["APP-A", "APP-B", "APP-C"],
            }
        }
        self.skills: dict[str, dict[str, Any]] = {
            "skill_inspection": {
                "skill_id": "skill_inspection",
                "skill_version_id": "skillv_inspection_1",
                "source": "openops",
                "source_type": "platform",
                "display_name": "应用巡检",
                "skill_key": "app_inspection",
                "version_no": "1.0.0",
                "status": "enabled",
                "bound_agent_key": "inspect",
            },
            "skill_user_logscan": {
                "skill_id": "skill_user_logscan",
                "skill_version_id": "skillv_user_logscan_1",
                "source": "openops",
                "source_type": "user",
                "display_name": "自定义日志扫描",
                "skill_key": "user_logscan",
                "version_no": "0.1.0",
                "status": "enabled",
                "bound_agent_key": "main",
            },
        }
        self.mcps: dict[str, dict[str, Any]] = {
            "mcp_omodel_query": {
                "mcp_id": "mcp_omodel_query",
                "mcp_version_id": "mcpv_omodel_query_1",
                "source": "openops",
                "source_type": "platform",
                "display_name": "oModel 查询 MCP",
                "mcp_key": "omodel_query",
                "transport": "http",
                "endpoint_url": "https://mcp.example.internal/omodel",
                "status": "enabled",
            },
            "mcp_user_metrics": {
                "mcp_id": "mcp_user_metrics",
                "mcp_version_id": "mcpv_user_metrics_1",
                "source": "openops",
                "source_type": "user",
                "display_name": "用户指标 HTTP MCP",
                "mcp_key": "user_metrics",
                "transport": "http",
                "endpoint_url": "https://metrics.example.internal/mcp",
                "status": "enabled",
            },
        }
        self.mcp_tools: list[dict[str, Any]] = [
            {
                "tool_catalog_id": "tool_query_resource",
                "mcp_id": "mcp_omodel_query",
                "tool_name": "query_resource",
                "display_name": "查询资源",
                "schema_hash": "sha256:resource-v1",
                "is_approval_required": False,
                "is_secret_required": False,
                "scope_mode": "required",
                "appid_arg_path": "$.appid",
                "status": "allowed",
            },
            {
                "tool_catalog_id": "tool_recover_execute",
                "mcp_id": "mcp_omodel_query",
                "tool_name": "recover_execute",
                "display_name": "执行恢复",
                "schema_hash": "sha256:recover-v1",
                "is_approval_required": True,
                "is_secret_required": False,
                "scope_mode": "required",
                "appid_arg_path": "$.appid",
                "status": "allowed",
            },
        ]
        self.llm_configs: dict[str, dict[str, Any]] = {
            "llm_platform_default": {
                "llm_config_id": "llm_platform_default",
                "display_name": "平台默认模型",
                "provider": "platform",
                "model_name": "sre-default",
                "status": "active",
                "context_window_tokens": 128000,
                "max_output_tokens": 4096,
                "supports_tool_calling": True,
                "supports_streaming": True,
            },
            "llm_user_gpt4o": {
                "llm_config_id": "llm_user_gpt4o",
                "display_name": "我的 OpenAI Compatible",
                "provider": "user",
                "model_name": "gpt-4o-compatible",
                "status": "active",
                "context_window_tokens": 64000,
                "max_output_tokens": 4096,
                "supports_tool_calling": True,
                "supports_streaming": True,
                "secret_ref_id": "secret_user_llm",
            },
        }
        self.secrets: dict[str, dict[str, Any]] = {
            "secret_user_llm": {
                "secret_ref_id": "secret_user_llm",
                "secret_name": "OpenAI Compatible API Key",
                "secret_kind": "llm_api_key",
                "fingerprint": "sk-...9f2a",
                "status": "active",
            }
        }
        self.instances: dict[str, dict[str, Any]] = {}
        self.config_versions: dict[str, dict[str, Any]] = {}
        self.bindings: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self.approvals: dict[str, dict[str, Any]] = {}
        self.audit_events: list[dict[str, Any]] = []
        self.runtime_config = {
            "max_user_containers_per_host": 26,
            "per_user_running_task_limit": 2,
            "idle_container_ttl_minutes": 30,
            "skill_task_timeout_minutes": 10,
        }
        self.seed_instance()

    def seed_instance(self) -> None:
        if self.instances:
            return
        instance_id = "agt_pay_fast_recovery"
        config_version_id = "cfg_pay_fast_recovery_1"
        now = utcnow()
        instance = {
            "agent_team_instance_id": instance_id,
            "instance_name": "支付域感知快恢",
            "owner_user_id": "user_0026demo01",
            "template_id": "tpl_sre_fast_recovery",
            "template_name": "感知快恢 Agent 模板",
            "workspace_id": "ws_pay_abc",
            "scope_revision": "rev-20260708-001",
            "active_config_version_id": config_version_id,
            "status": "active",
            "run_status": "ready",
            "created_at": now,
            "updated_at": now,
        }
        config = {
            "config_version_id": config_version_id,
            "agent_team_instance_id": instance_id,
            "template_version_id": "tplv_sre_fast_recovery_1",
            "version_no": 1,
            "main_role_append": "优先解释影响范围，再建议最小恢复动作。",
            "llm_config_id": "llm_platform_default",
            "status": "active",
            "change_reason": "initial",
            "created_at": now,
        }
        self.instances[instance_id] = instance
        self.config_versions[config_version_id] = config

    def current_user(self, role: str = "user", login_key: str = "0026demo01", whitelisted: bool = True) -> dict[str, Any]:
        if role == "platform_admin":
            base = deepcopy(self.users["admin"])
        else:
            base = deepcopy(self.users.get(login_key, self.users["0026demo01"]))
            base["role"] = "user"
        base["whitelisted"] = whitelisted
        return base

    def add_audit(self, event_type: str, payload: dict[str, Any], agent_run_id: str | None = None, task_id: str | None = None) -> dict[str, Any]:
        run = self.runs.get(agent_run_id or "")
        audit_trace_id = run["audit_trace_id"] if run else payload.get("audit_trace_id", new_id("trace"))
        event = {
            "audit_event_id": new_id("audit"),
            "audit_trace_id": audit_trace_id,
            "agent_run_id": agent_run_id,
            "task_id": task_id,
            "event_type": event_type,
            "payload_redacted_json": payload,
            "occurred_at": utcnow(),
            "expire_at": expires_in(30),
        }
        self.audit_events.append(event)
        return event

    def create_instance(self, user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        instance_id = new_id("agt")
        config_version_id = new_id("cfg")
        now = utcnow()
        template_id = payload.get("template_id") or "tpl_sre_fast_recovery"
        workspace_id = payload.get("workspace_id") or "ws_pay_abc"
        workspace = self.workspaces.get(workspace_id, self.workspaces["ws_pay_abc"])
        instance = {
            "agent_team_instance_id": instance_id,
            "instance_name": payload.get("instance_name") or "新的感知快恢 AgentTeam",
            "owner_user_id": user["user_id"],
            "template_id": template_id,
            "template_name": self.templates[template_id]["display_name"],
            "workspace_id": workspace_id,
            "scope_revision": workspace["scope_revision"],
            "active_config_version_id": config_version_id,
            "status": "active",
            "run_status": "ready",
            "created_at": now,
            "updated_at": now,
        }
        config = {
            "config_version_id": config_version_id,
            "agent_team_instance_id": instance_id,
            "template_version_id": self.templates[template_id]["latest_version"]["template_version_id"],
            "version_no": 1,
            "main_role_append": payload.get("main_role_append") or "",
            "llm_config_id": payload.get("llm_config_id") or "llm_platform_default",
            "status": "active",
            "change_reason": "initial",
            "created_at": now,
        }
        self.instances[instance_id] = instance
        self.config_versions[config_version_id] = config
        self.add_audit("agent_team.created", {"agent_team_instance_id": instance_id, "workspace_id": workspace_id})
        return deepcopy(instance)

    def create_config_version(self, instance_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        instance = self.instances.get(instance_id)
        if not instance:
            return None
        versions = [v for v in self.config_versions.values() if v["agent_team_instance_id"] == instance_id]
        next_no = max([v["version_no"] for v in versions], default=0) + 1
        current = self.config_versions[instance["active_config_version_id"]]
        config_id = new_id("cfg")
        config = {
            **deepcopy(current),
            "config_version_id": config_id,
            "version_no": next_no,
            "main_role_append": payload.get("main_role_append", current.get("main_role_append", "")),
            "llm_config_id": payload.get("llm_config_id", current.get("llm_config_id", "llm_platform_default")),
            "change_reason": payload.get("change_reason") or "user_update",
            "created_at": utcnow(),
        }
        self.config_versions[config_id] = config
        instance["active_config_version_id"] = config_id
        instance["updated_at"] = utcnow()
        self.add_audit("config.version.created", {"agent_team_instance_id": instance_id, "config_version_id": config_id})
        return deepcopy(config)

    def create_run(self, user: dict[str, Any], instance_id: str) -> dict[str, Any] | None:
        instance = self.instances.get(instance_id)
        if not instance:
            return None
        run_id = new_id("run")
        now = utcnow()
        run = {
            "agent_run_id": run_id,
            "agent_team_instance_id": instance_id,
            "owner_user_id": user["user_id"],
            "config_version_id": instance["active_config_version_id"],
            "framework_session_id": new_id("as_session"),
            "audit_trace_id": new_id("trace"),
            "selected_model": self.config_versions[instance["active_config_version_id"]].get("llm_config_id", "llm_platform_default"),
            "run_status": "active",
            "active_task": None,
            "messages": [
                {"role": "assistant", "content": "我已就绪，可以开始巡检、定界或恢复前的风险确认。"}
            ],
            "timeline": [],
            "created_at": now,
            "updated_at": now,
        }
        self.runs[run_id] = run
        self.add_audit("run.created", {"agent_team_instance_id": instance_id}, agent_run_id=run_id)
        return deepcopy(run)

    def start_task(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        run = self.runs.get(run_id)
        if not run or run["run_status"] != "active":
            return None
        task_id = new_id("task")
        task = {
            "task_id": task_id,
            "status": "approval_pending",
            "prompt": payload.get("prompt") or "巡检当前工作范围",
            "started_at": utcnow(),
        }
        run["active_task"] = task
        run["messages"].append({"role": "user", "content": task["prompt"]})
        run["timeline"].extend(
            [
                {"type": "openops.task.started", "title": "任务已启动", "detail": task["prompt"], "time": utcnow()},
                {"type": "openops.scope.resolved", "title": "工作范围已解析", "detail": "APP-A、APP-B、APP-C", "time": utcnow()},
                {"type": "openops.approval.required", "title": "恢复类工具需要确认", "detail": "recover_execute 等待用户确认", "time": utcnow()},
            ]
        )
        approval_id = new_id("apr")
        approval = {
            "approval_request_id": approval_id,
            "agent_run_id": run_id,
            "task_id": task_id,
            "reply_id": new_id("reply"),
            "tool_call_id": "toolcall_recover_execute",
            "tool_call_name": "recover_execute",
            "decision": "pending",
            "summary": {
                "tool_name": "recover_execute",
                "action_summary": "对 APP-A 执行恢复预案",
                "target_appids": ["APP-A"],
                "impact": "可能重启支付应用的一个实例",
                "arguments_redacted": {"appid": "APP-A", "plan": "restart_single_instance"},
                "timeout_seconds": 300,
            },
            "created_at": utcnow(),
            "expire_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        }
        self.approvals[approval_id] = approval
        self.add_audit("task.started", {"task_id": task_id, "prompt_length": len(task["prompt"])}, agent_run_id=run_id, task_id=task_id)
        self.add_audit("approval.required", {"approval_request_id": approval_id, "tool_call_name": "recover_execute"}, agent_run_id=run_id, task_id=task_id)
        return deepcopy(task)

    def decide_approval(self, approval_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        approval = self.approvals.get(approval_id)
        if not approval:
            return None
        decision = payload.get("decision", "rejected")
        approval["decision"] = decision
        approval["reason"] = payload.get("reason")
        approval["decided_at"] = utcnow()
        run = self.runs.get(approval["agent_run_id"])
        if run:
            run["timeline"].append(
                {
                    "type": f"openops.approval.{decision}",
                    "title": "审批已处理",
                    "detail": f"{approval['tool_call_name']} -> {decision}",
                    "time": utcnow(),
                }
            )
            if decision == "approved":
                run["active_task"]["status"] = "completed"
                run["messages"].append(
                    {
                        "role": "assistant",
                        "content": "审批已通过，恢复工具已返回 execution_id=exec_20260708_001，任务完成。",
                    }
                )
                run["timeline"].append(
                    {
                        "type": "openops.tool.call.succeeded",
                        "title": "恢复类 MCP 调用成功",
                        "detail": "execution_id=exec_20260708_001",
                        "time": utcnow(),
                    }
                )
            else:
                run["active_task"]["status"] = "cancelled" if decision == "cancelled" else "failed"
        self.add_audit("approval.decided", {"approval_request_id": approval_id, "decision": decision}, approval["agent_run_id"], approval["task_id"])
        return deepcopy(approval)

    def cancel_task(self, task_id: str, reason: str | None) -> dict[str, Any] | None:
        for run in self.runs.values():
            task = run.get("active_task")
            if task and task["task_id"] == task_id:
                task["status"] = "cancelled"
                task["cancel_reason"] = reason
                run["timeline"].append(
                    {
                        "type": "openops.task.cancelled",
                        "title": "任务已取消",
                        "detail": reason or "用户取消当前任务",
                        "time": utcnow(),
                    }
                )
                for approval in self.approvals.values():
                    if approval["task_id"] == task_id and approval["decision"] == "pending":
                        approval["decision"] = "cancelled"
                        approval["decided_at"] = utcnow()
                self.add_audit("task.cancelled", {"task_id": task_id, "reason": reason}, run["agent_run_id"], task_id)
                return deepcopy(task)
        return None

    def close_run(self, run_id: str, reason: str | None) -> dict[str, Any] | None:
        run = self.runs.get(run_id)
        if not run:
            return None
        run["run_status"] = "closed"
        run["closed_at"] = utcnow()
        if run.get("active_task") and run["active_task"]["status"] in {"running", "approval_pending"}:
            run["active_task"]["status"] = "cancelled"
        run["timeline"].append(
            {"type": "openops.run.closed", "title": "Run 已关闭", "detail": reason or "用户关闭会话", "time": utcnow()}
        )
        self.add_audit("run.closed", {"reason": reason}, agent_run_id=run_id)
        return deepcopy(run)

    def run_state(self, run_id: str) -> dict[str, Any] | None:
        run = self.runs.get(run_id)
        if not run:
            return None
        instance = self.instances.get(run["agent_team_instance_id"])
        approvals = [a for a in self.approvals.values() if a["agent_run_id"] == run_id and a["decision"] == "pending"]
        return {
            "run": deepcopy(run),
            "instance": deepcopy(instance),
            "approvals": deepcopy(approvals),
            "runtime": {
                "status": "approval_pending" if approvals else ("run_closed" if run["run_status"] == "closed" else "ready_no_task"),
                "selected_model": run["selected_model"],
                "agui_connected": True,
            },
            "activity_timeline": deepcopy(run["timeline"]),
        }


store = MockStore()
