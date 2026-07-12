"""Skill 作 agent 工具的审计化编排（C1，28.3 号）：装配集校验 → 真 ZIP 投递 → 容器内执行。

在 agent 对话循环里，Agent 调 `run_platform_skill(skill_name)` → 本模块：校验 skill 在可执行集合
（平台 active ∪ 用户绑定，fail-closed）→ Skill Hub 投递真 ZIP（29.3 X-Checksum-SHA256 传输完整性）
→ run_skill 在该用户容器内经 entrypoint 执行 → 返回结构化结果。审计 skill.call.started/succeeded/failed。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from infra.external import skill_hub_client
from runtime.emit import emit
from runtime.task_registry import TaskState
from sandbox.executor import SkillResult
from sandbox.executor import executor as sandbox_executor

log = logging.getLogger("openops.skill")


async def run_bound_skill(st: TaskState, run: dict[str, Any], skill_name: str,
                          arguments: dict[str, Any] | None = None) -> str:
    """执行一个在装配集内的 Skill；返回给模型的文本结果。未绑定/未授权 → skill.call.blocked（fail-closed）。"""
    available = st.available_skills or {}
    meta = available.get(skill_name)
    if meta is None:
        st.tool_blocked = True  # B6-RT-001：未装配 Skill 不得执行/不得宣称成功
        await emit(st, run, "openops.skill.call.blocked", severity="warning", action=skill_name,
                   message=f"Skill 「{skill_name}」未绑定到当前实例，拒绝执行", reason_code="TOOL_BLOCKED",
                   payload={"skill": skill_name})
        return f"Skill 「{skill_name}」未在当前实例装配集中，未执行"

    tool_call_id = "sk_" + uuid.uuid4().hex[:10]
    await emit(st, run, "openops.skill.call.started", action=skill_name,
               message=f"Skill 执行：{skill_name}", payload={"skill": skill_name})
    try:
        pkg = await skill_hub_client.download_skill_package(skill_name, int(meta.get("version_no") or 1))
        res: SkillResult = await sandbox_executor.run_skill(
            st.user_id, task_id=st.task_id, tool_call_id=tool_call_id,
            entrypoint=pkg["entrypoint"], files=pkg["files"],
            expected_checksum=pkg["checksum"],  # 传输完整性（29.3 X-Checksum-SHA256）
        )
    except Exception as e:  # noqa: BLE001 — checksum/超时/容器缺失/下载失败等结构化收口
        st.tool_blocked = True
        code = getattr(e, "code", "SKILL_FAILED")
        # 根因必须三通道可见（内网教训：下载类失败曾全部塌缩成无信息的 SKILL_FAILED，
        # raise_with_body 保留的响应体/BadZipFile 文本被丢弃，无法定位）
        detail = f"{type(e).__name__}: {str(e)[:300]}"
        log.warning("[skill] %s 执行失败 %s", skill_name, detail)
        await emit(st, run, "openops.skill.call.failed", severity="error", action=skill_name,
                   message=f"Skill 执行失败：{code}", reason_code=str(code),
                   payload={"skill": skill_name, "error": detail})
        return f"Skill 「{skill_name}」执行失败：{code}（{detail}）"

    if res.status != "success":
        st.tool_blocked = True
        await emit(st, run, "openops.skill.call.failed", severity="error", action=skill_name,
                   message=f"Skill 非零退出（{res.exit_code}）", reason_code="SKILL_FAILED",
                   payload={"skill": skill_name, "exit_code": res.exit_code, "stderr": res.stderr[:1000]})
        return f"Skill 「{skill_name}」退出码 {res.exit_code}：{res.stderr[:500]}"

    await emit(st, run, "openops.skill.call.succeeded", action=skill_name,
               message=f"Skill 执行完成：{skill_name}",
               payload={"skill": skill_name, "result": res.result_json, "stdout": res.stdout[:1000]})
    body = res.result_json if res.result_json is not None else res.stdout[:1000]
    return f"Skill 「{skill_name}」结果：{body}"
