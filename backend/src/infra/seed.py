"""启动种子（幂等）：demo 用户/白名单、感知快恢模板、平台资产+tool 标注、沙箱配置、模型资产。"""
from __future__ import annotations

from infra.db import q_one
from infra.external import mcp_registry_client
from infra.repositories import assets, mcp_tools, model_assets, runtime_config, templates, users

SANDBOX_DEFAULTS: dict[str, tuple[object, str]] = {
    "max_user_containers_per_host": (26, "单机最大用户容器数"),
    "per_user_running_task_limit": (2, "每用户最多 running task"),
    "user_container_idle_ttl_minutes": (15, "idle 容器保留时间"),
    "capacity_full_policy": ("strict_ttl", "容量满策略"),
    "container_cpu_limit": (0.5, "新建容器 CPU 限额"),
    "container_memory_limit_mib": (2048, "新建容器内存限额"),
}

# 模型资产（B7：sre_model_asset 表；替代旧 sre_platform_runtime_config platform_model 域）
# (display_name, model_id, base_url, secret_env_var, access_scope, status)
MODEL_ASSETS = [
    ("Qwen3.5-千问", "qwen3.5-instruct", None, None, "all", "active"),
    ("GLM-5.1", "glm-5.1", "https://open.bigmodel.cn/api/paas/v4/chat/completions",
     "OPENOPS_PLATFORM_GLM_API_KEY", "all", "active"),
    ("GPT-4.1", "gpt-4.1", None, None, "all", "active"),
    ("DeepSeek-V3", "deepseek-chat", None, None, "all", "active"),
    ("Claude 3.5", "claude-3-5-sonnet", None, None, "all", "disabled"),
    # restricted 演示（部门私有模型接口，仅白名单授权用户可用）：授权 0026demo01
    ("交易大模型-TX", "tx-llm-v2", None, None, "restricted", "active"),
]

# D 块：worker 汇报纪律（37 号老 roles.yaml 口径翻译）——拼进每个 sub agent 的 system_prompt
SUB_REPORT_DISCIPLINE = (
    "缺参数时直接返回 blocker 说明，绝不凭空造参数。"
    "禁止无差别调用所有工具——按任务选择所需工具，同参数每个工具只调用一次；"
    "工具返回空结果/无数据 = 查询完成，严禁对同一条件重复调用或自行调整参数重试。"
    "必须在全部步骤执行完成后一次性汇报结果，禁止中途输出部分结论；"
    "只汇报查询结果本身，不要发散分析置信度、caveats 或建议——这些由主 Agent 判断。"
)

TEMPLATE_CONTENT = {
    "main": {
        "role": "理解用户任务，调度巡检/定界/恢复能力，工具调用前遵守平台安全策略。",
        # B7·二：RuntimePlan 只装配模板 default_tools 内的平台工具（恢复类照常受 ASK 标注管控）；
        # 含动态注册表工具——main 与 sub 同为白名单制，空集=零平台工具=纯编排派发（老 D6 效果）
        "default_tools": ["query_resource", "recover_execute"],
        # main 直连技能白名单（编排对称化）：**空/缺省=不限**（沿用 平台 active ∪ 实例绑定；
        # 与 default_tools「空=零」语义相反——skills 执行另有装配校验+沙箱受控，白名单是可选收窄）
        "skills": [],
        # D 块派发预算（老 D6 两层模型）：max_children=同时活跃子 Agent 上限；
        # delegation_max_spawns=单 task 累计派发兜底（防失败重派死循环）
        "max_children": 3,
        "delegation_max_spawns": 10,
    },
    # D 块：sub_agents = 可执行角色画像（权威载体，04 号口径）：skills=skill_key 白名单、
    # mcp_tools=平台工具白名单——子 Agent toolkit 按此裁剪（per-agent 工具隔离）。
    # E1 审批桥：写工具可绑到子 Agent（如恢复工具绑恢复 Agent）——触发时审批卡带子 task_id
    # 弹前端，批准后结果按 task_id 精确路由回该子 Agent 继续。
    # tool_result_limit：单条工具结果进上下文的保留 token（必须 < 模型窗口，经验 ≤1/3；E4 治理）。
    "sub_agents": [
        {"key": "inspect", "label": "巡检", "role": "基于应用范围查看健康状态、异常信号与风险，只做查询不做变更。",
         "skills": ["inspection"], "mcp_tools": ["query_resource"], "max_iters": 20, "tool_result_limit": 24000},
        {"key": "diagnose", "label": "定界", "role": "结合告警/指标/日志/链路/拓扑判断问题边界，输出证据与假设排行。",
         "skills": [], "mcp_tools": ["query_resource"], "max_iters": 20, "tool_result_limit": 24000},
        {"key": "recover", "label": "恢复", "role": "执行受控恢复动作：先核对目标与影响面，恢复类工具调用需人工批准后执行。",
         "skills": [], "mcp_tools": ["recover_execute"], "max_iters": 10, "tool_result_limit": 24000},
    ],
    "default_llm": {"provider": "platform", "model": "qwen3.5-instruct"},
}


async def seed() -> None:
    # 已播种则跳过（以模板存在为标志）
    if await q_one("select 1 ok from sre_agent_team_template where template_key='sensai_fast_recovery'"):
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

    # 模型资产（管理台「模型资产」页数据源；restricted 演示模型授权 demo 用户）
    for display_name, model_id, base_url, env_var, scope, status in MODEL_ASSETS:
        row = await model_assets.create(display_name, "openai_compatible", model_id, base_url, env_var, scope, status, "system")
        if scope == "restricted":
            await model_assets.replace_grants(str(row["model_asset_id"]), ["0026demo01"], "system")
