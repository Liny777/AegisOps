"""启动种子（幂等）：demo 用户/白名单、感知快恢模板、平台资产+tool 标注、沙箱配置、平台模型。"""
from __future__ import annotations

from infra.db import q_one
from infra.external import mcp_registry_client
from infra.repositories import assets, mcp_tools, runtime_config, templates, users

SANDBOX_DEFAULTS: dict[str, tuple[object, str]] = {
    "max_user_containers_per_host": (26, "单机最大用户容器数"),
    "per_user_running_task_limit": (2, "每用户最多 running task"),
    "user_container_idle_ttl_minutes": (15, "idle 容器保留时间"),
    "capacity_full_policy": ("strict_ttl", "容量满策略"),
    "container_cpu_limit": (0.5, "新建容器 CPU 限额"),
    "container_memory_limit_mib": (2048, "新建容器内存限额"),
}

PLATFORM_MODELS = [
    {"name": "Qwen3.5-千问", "protocol": "OpenAI 兼容", "model_id": "qwen3.5-instruct", "status": "active", "probe": "探测通过 · tool calling"},
    {
        "name": "GLM-5.1",
        "protocol": "OpenAI 兼容",
        "model_id": "glm-5.1",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "status": "active",
        "probe": "探测通过 · tool calling",
        "secret_env_var": "OPENOPS_PLATFORM_GLM_API_KEY",
    },
    {"name": "GPT-4.1", "protocol": "OpenAI 兼容", "model_id": "gpt-4.1", "status": "active", "probe": "探测通过 · tool calling"},
    {"name": "DeepSeek-V3", "protocol": "OpenAI 兼容", "model_id": "deepseek-chat", "status": "active", "probe": "探测通过 · tool calling"},
    {"name": "Claude 3.5", "protocol": "OpenAI 兼容", "model_id": "claude-3-5-sonnet", "status": "disabled", "probe": "Secret 缺失"},
]

TEMPLATE_CONTENT = {
    "main": {
        "role": "理解用户任务，调度巡检/定界/恢复能力，工具调用前遵守平台安全策略。",
        "default_tools": ["query_resource"],
    },
    "sub_agents": [
        {"key": "inspect", "label": "巡检", "role": "基于应用范围查看健康状态、异常信号与风险"},
        {"key": "diagnose", "label": "定界", "role": "结合告警/指标/日志/链路/拓扑判断问题边界"},
        {"key": "recover", "label": "恢复", "role": "给出受控恢复建议，用户确认后执行"},
    ],
    "default_llm": {"provider": "platform", "model": "qwen3.5-instruct"},
}


async def seed() -> None:
    # 已播种则跳过（以模板存在为标志）
    if await q_one("select 1 ok from agent_team_template where template_key='sensai_fast_recovery'"):
        return

    # 用户 + 白名单
    await users.upsert_user("0026demo01", "林一", "user")
    await users.upsert_user("admin", "李四（管理员）", "platform_admin")
    await users.add_whitelist("0026demo01", "system")
    await users.add_whitelist("admin", "system")

    # 模板（V1 唯一：感知快恢）
    await templates.create_template_with_version(
        "sensai_fast_recovery", "感知快恢 Agent",
        "面向 SRE 巡检 / 定界 / 恢复闭环的平台模板。", TEMPLATE_CONTENT, "system",
    )

    # 平台 Skill
    await assets.create_skill(None, "platform", "巡检 inspection", "inspection", {"entrypoint": "run.py"}, "c0ffee")

    # 平台 MCP + tool catalog + 标注（query_resource 免审批 / recover_execute 需审批，均 scope required）
    mcp = await assets.create_mcp(None, "platform", "oModel 查询与恢复", "http", {"endpoint": "http://mock"}, {})
    for tool in await mcp_registry_client.discover_tools("platform"):
        tcid = await mcp_tools.upsert_catalog_tool(
            mcp["mcp_version_id"], tool["tool_name"], tool["description"], tool["input_schema"], tool["schema_hash"]
        )
        await mcp_tools.save_annotation(
            tcid,
            is_approval_required=(tool["tool_name"] == "recover_execute"),
            is_secret_required=False,
            scope_mode="required",
            appid_arg_path="$.appid",
            status="allowed",
            blocked_reason=None,
            by="system",
        )

    # 沙箱运行配置
    for key, (val, desc) in SANDBOX_DEFAULTS.items():
        await runtime_config.upsert(runtime_config.DOMAIN_SANDBOX, key, val, description=desc, reason="seed")

    # 平台模型注册（管理台「模型」Tab 数据源）
    for m in PLATFORM_MODELS:
        await runtime_config.upsert(runtime_config.DOMAIN_MODEL, m["model_id"], m, description=m["name"], reason="seed")
