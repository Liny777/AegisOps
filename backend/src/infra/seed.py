"""启动种子（幂等）：demo 用户/白名单、感知快恢模板、平台资产+tool 标注、沙箱配置、模型资产。"""
from __future__ import annotations

import os

from infra.db import q_one
from infra.external import mcp_registry_client
from infra.repositories import assets, mcp_tools, model_assets, model_templates, runtime_config, templates, users

SANDBOX_DEFAULTS: dict[str, tuple[object, str]] = {
    "max_user_containers_per_host": (26, "单机最大用户容器数"),
    "per_user_running_task_limit": (2, "每用户最多 running task"),
    "interactive_queue_enabled": (True, "交互任务名额满时排队（关=退回 429 旧行为，应急回滚用）"),
    "interactive_queue_max": (20, "交互排队队列长度上限，超过才返回 429"),
    "interactive_queue_timeout_s": (300, "排队超时秒数，超时推 task.failed(QUEUE_TIMEOUT)"),
    "user_container_idle_ttl_minutes": (15, "idle 容器保留时间"),
    "run_idle_ttl_minutes": (30, "无活动 run 自动回收阈值（分钟）——兜底 run 泄漏，防废弃会话长期占容器名额"),
    "capacity_full_policy": ("strict_ttl", "容量满策略（V1 固定 strict_ttl：先回收已到 TTL 的 idle 腾位）"),
    "container_cpu_limit": (0.5, "新建容器 CPU 限额"),
    "container_memory_limit_mib": (2048, "新建容器内存限额"),
    # docker 档产品化（2026-07-15）：镜像/网络/进程数/Bash deny 前缀，新建容器生效
    "container_image": ("python:3.11-slim", "沙箱容器镜像（须已在宿主 docker load）"),
    "container_network_mode": ("bridge", "容器网络模式：bridge（默认，可出网）/ none（断网）"),
    "container_pids_limit": (256, "容器内进程数上限（防 fork 炸弹）"),
    # 逗号分隔字符串（管理台编辑框按字符串回传，数组一经编辑必被打成字符串）；token 词边界匹配防串联绕过
    "bash_deny_prefixes": ("docker,sudo,su,mount,umount,mkfs,shutdown,reboot,halt,poweroff",
                           "容器内 Bash 平台 deny 前缀（逗号分隔；即便用户批准也不放行的纵深项）"),
}


async def ensure_sandbox_defaults() -> None:
    """每次启动补缺沙箱配置键：只 insert 缺失键，绝不覆盖已有值。

    seed() 有「模板存在即早退」守卫，老库重启不会重跑种子——新增键须靠本函数在守卫**之前**补种；
    又因 runtime_config.upsert 对已存在键是覆盖，故先 select 现有键集、只补缺失键，保护管理员改过的值。
    """
    existing = {r["config_key"] for r in await runtime_config.get_domain(runtime_config.DOMAIN_SANDBOX)}
    for key, (val, desc) in SANDBOX_DEFAULTS.items():
        if key not in existing:
            await runtime_config.upsert(runtime_config.DOMAIN_SANDBOX, key, val, description=desc, reason="seed")

# 模型资产（B7：sre_model_asset 表；替代旧 sre_platform_runtime_config platform_model 域）
# (display_name, model_id, base_url, secret_env_var, status)
# 38.1：授权迁模板维度，资产不再带 access_scope（受限演示上移到模型模板「交易专用（受限演示）」）
MODEL_ASSETS = [
    ("Qwen3.5", "qwen3.5-instruct", None, None, "active"),
    ("GLM-5.1", "glm-5.1", "https://open.bigmodel.cn/api/paas/v4/chat/completions",
     "OPENOPS_PLATFORM_GLM_API_KEY", "active"),
    ("GPT-4.1", "gpt-4.1", None, None, "active"),
    ("DeepSeek-V3", "deepseek-chat", None, None, "active"),
    ("Claude 3.5", "claude-3-5-sonnet", None, None, "disabled"),
    ("交易大模型-TX", "tx-llm-v2", None, None, "active"),
]

# 模型模板（38 号：主/子 Agent 槽位）：
# (display_name, description, main_model_id, sub_model_id, is_default, access_scope)
# 选型依据 95.x 模型评估：GLM-5.1 全场景领先作生产默认；「经济」把子任务放 Qwen3.5 省成本。
MODEL_TEMPLATES = [
    ("均衡（推荐）", "主 / 子 Agent 均使用 GLM-5.1，效果优先", "glm-5.1", "glm-5.1", True, "all"),
    ("经济", "主 GLM-5.1 + 子 Qwen3.5-Instruct，子任务省成本", "glm-5.1", "qwen3.5-instruct", False, "all"),
    # restricted 演示（38.1：授权在模板维度）：仅白名单可见可绑，授权 0026demo01
    ("交易专用（受限演示）", "主 GLM-5.1 + 子 交易大模型-TX，部门私有组合仅白名单用户可用",
     "glm-5.1", "tx-llm-v2", False, "restricted"),
]


async def ensure_model_template_seed() -> None:
    """模型模板补种（幂等）：表空才种、按 model_id 解析资产、解析不到即整条跳过。

    不覆盖管理员已建/已改模板（表非空即整体跳过，对齐 ensure_sandbox_defaults 只补缺哲学）；
    旧库未跑 migrate-2026-07-29-model-template.sql（表缺失）时静默跳过不阻断启动。
    seed() 调用两次：守卫前覆盖存量库（跑完 migrate 重启即得种子），末尾覆盖全新库/pytest 库
    （首次启动守卫前那次因模型资产尚未种下会整体跳过，资产种完后这次才解析得到 asset_id）。
    """
    try:
        if await q_one("select 1 ok from sre_model_template limit 1"):
            return
    except Exception:  # noqa: BLE001 —— 表未迁移（旧库未跑 migrate-2026-07-29）
        return
    for name, desc, main_mid, sub_mid, is_default, scope in MODEL_TEMPLATES:
        main = await model_assets.get_by_model_id(main_mid)
        sub = await model_assets.get_by_model_id(sub_mid)
        if main is None or sub is None:
            continue
        row = await model_templates.create(name, desc, str(main["model_asset_id"]),
                                           str(sub["model_asset_id"]), "system", access_scope=scope)
        if is_default:
            await model_templates.set_default(str(row["model_template_id"]), "system")
        if scope == "restricted":
            # 受限演示模板授权 demo 用户（38.1：授权在模板维度）
            await model_templates.replace_grants(str(row["model_template_id"]), ["0026demo01"], "system")

TEMPLATE_CONTENT = {
    "main": {
        "role": "理解用户任务，调度巡检/诊断/恢复能力，工具调用前遵守平台安全策略。",
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
        {"key": "diagnose", "label": "诊断",
         "role": "结合告警/指标/日志/链路/拓扑判断问题边界，输出证据与假设排行；按五步法诊断时，"
                 "每进入或完成一步调用 update_diagnosis_board 上报进度与阶段产出。",
         "skills": [], "mcp_tools": ["query_resource"], "max_iters": 20, "tool_result_limit": 24000},
        {"key": "recover", "label": "恢复", "role": "执行受控恢复动作：先核对目标与影响面，恢复类工具调用需人工批准后执行。",
         "skills": [], "mcp_tools": ["recover_execute"], "max_iters": 10, "tool_result_limit": 24000},
    ],
    # DEPRECATED（2026-07-29，38 号）：default_llm 从未被运行时消费（run_state_service 只读实例
    # overlay 三键）。模型改由 sre_model_template（管理台「模型模板」页）编排；字段保留仅为
    # 兼容存量模板版本 content_json 的形状（test_templates 夹具仍含它），勿新增消费方。
    "default_llm": {"provider": "platform", "model": "qwen3.5-instruct"},
}


async def seed() -> None:
    # 沙箱配置补缺（守卫之前）：老库重启也能自动补新增键，不覆盖管理员改过的值
    await ensure_sandbox_defaults()
    # 模型模板补种（守卫之前）：存量库跑完 migrate 重启即得种子模板（全新库靠 seed 末尾的第二次调用）
    await ensure_model_template_seed()
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
        "面向 SRE 巡检 / 诊断 / 恢复闭环的平台模板。", TEMPLATE_CONTENT, "system",
    )

    # demo 平台 Skill（巡检 inspection）：**真环境不种**——技能一律来自真 SkillHub 对账，
    # 种一个假的会永远赖在插件页/Skill 基线里（reconcile 是「有则更新、无则不管」、无删除/无墓碑，
    # 上游不列它就永不清除）。mock/默认模式才种：本地端到端与大量用例（test_assets / test_run_task /
    # test_sandbox / test_templates …）都依赖它。门控复用既有的 OPENOPS_SKILLHUB 开关，不新造 flag。
    # description 供发现链路：注入 run_platform_skill 工具描述；首轮 reconcile 会按真 checksum 补真版本。
    if os.getenv("OPENOPS_SKILLHUB", "mock").lower() != "real":
        await assets.create_skill(None, "platform", "巡检 inspection", "inspection",
                                  {"entrypoint": "run.py", "description": "巡检 Skill——检查资源健康度（如 redis 连接池、p99 时延），产出结构化巡检发现"},
                                  "c0ffee")

    # 平台 MCP + tool catalog + 标注（query_resource 免审批 / recover_execute 需审批，均 scope required）：
    # **真环境不种**——同上方 demo Skill 口径：真实部署的平台 MCP 一律由 MCP Registry 对账入库
    # （asset_reconcile_service），种一个 endpoint=http://mock 的假资产会永远赖在插件页/管理台
    # （reconcile 只增不删、无墓碑，上游不列它就永不清除）。mock/默认模式才种：本地端到端与
    # test_templates / test_mcp_dynamic 等用例依赖它提供 query_resource/recover_execute 目录与标注。
    # 门控复用既有 OPENOPS_MCPREGISTRY 开关，不新造 flag。
    if os.getenv("OPENOPS_MCPREGISTRY", "mock").lower() != "real":
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

    # 沙箱运行配置已由 ensure_sandbox_defaults()（守卫前）补齐，此处不再重复种

    # 模型资产（管理台「模型资产」页数据源；38.1 无授权位，受限演示见模型模板 seed）
    for display_name, model_id, base_url, env_var, status in MODEL_ASSETS:
        await model_assets.create(display_name, "openai_compatible", model_id, base_url, env_var, status, "system")

    # 模型模板（38 号）：第二次调用覆盖全新库/pytest 库——守卫前那次因资产未种会整体跳过
    await ensure_model_template_seed()
