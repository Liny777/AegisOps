"""Skill 作 agent 工具的审计化编排（C1，28.3 号）：装配集校验 → 真 ZIP 投递 → 容器内执行。

在 agent 对话循环里，Agent 调 `run_platform_skill(skill_name)` → 本模块：校验 skill 在可执行集合
（平台 active ∪ 用户绑定，fail-closed）→ Skill Hub 投递真 ZIP（29.3 X-Checksum-SHA256 传输完整性）
→ run_skill 在该用户容器内经 entrypoint 执行 → 返回结构化结果。审计 skill.call.started/succeeded/failed。
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any

from domain.errors import ApiError, Err
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
        # 双形态分型（2026-07-14 内网 exit 2 实锤）：SkillHub 老形态 Skill 只有 SKILL.md 排查手册
        # （entrypoint=None）。统一进容器（2026-07-15 拍板）：手册型也把整包投递进沙箱——
        # references/ 等参考文件供 agent 经 run_container_command（只读命令自动放行）按需读取，
        # 不再只回 SKILL.md 正文（此前 references 全被丢弃 → agent「没按 skill 内容执行」）。
        if pkg.get("entrypoint") is None:
            files: dict[str, bytes] = pkg.get("files") or {}
            md = files.get("SKILL.md", b"").decode("utf-8", "replace").strip()
            if not md:
                raise ApiError(Err.SKILL_PACKAGE_INVALID, "Skill 包既无可执行 entrypoint 也无 SKILL.md 手册")
            cap = int(os.environ.get("OPENOPS_SKILL_MANUAL_CAP", "12000"))
            # 投递优雅降级：手册正文独立有值，容器抖动/校验失败不让整个调用失败，只在文本里说明
            n_refs = 0
            abs_dir: str | None = None  # 成功投递才有值；截断标记按有无落盘正文分别指引
            try:
                abs_dir, names = await sandbox_executor.stage_files(
                    st.user_id, task_id=st.task_id, tool_call_id=tool_call_id,
                    files=files, expected_checksum=pkg["checksum"],
                    run_id=st.run_id, cfg=st.sandbox_cfg,
                )
                n_refs = sum(1 for n in names if n != "SKILL.md")
                listing = "\n".join(f"  - {n}（{len(files[n]) / 1024:.1f}KB）" for n in names)
                example = next((n for n in names if n != "SKILL.md"), "SKILL.md")
                staged_note = (f"手册全文与参考文件已放入你的容器目录 {abs_dir}/：\n{listing}\n"
                               f"需要细则时用 Read 工具读取完整文件（如 Read file_path={abs_dir}/{example}，"
                               f"大文件用 offset/limit 分页），或 run_container_command。\n")
            except Exception as e:  # noqa: BLE001 — 投递失败降级：仅手册正文可用
                log.warning("[skill] %s 参考文件投递失败 %s: %s", skill_name, type(e).__name__, str(e)[:200])
                staged_note = f"⚠ 参考文件投递失败（{type(e).__name__}），仅手册正文可用。\n"
            await emit(st, run, "openops.skill.call.succeeded", action=skill_name,
                       message=f"Skill 手册已装载：{skill_name}（含 {n_refs} 个参考文件）",
                       payload={"skill": skill_name, "mode": "manual", "chars": len(md)})
            # 正文超 cap 时显式标记（此前静默丢尾，agent 不知正文被切）：落盘则指全文文件，否则通用提示
            trunc = ("" if len(md) <= cap else
                     (f"\n\n（⚠ 手册正文超 {cap} 字符已截断，完整正文在容器 {abs_dir}/SKILL.md，"
                      f"用 Read 工具读取全文）" if abs_dir else
                      f"\n\n（⚠ 手册正文超 {cap} 字符已截断，仅显示前 {cap} 字符）"))
            return (f"Skill 「{skill_name}」是手册型技能（无可执行脚本）。{staged_note}"
                    f"请先阅读以下手册正文，按其中的指引使用可用工具完成任务：\n\n{md[:cap]}{trunc}")
        # 脚本型：entrypoint 指向的脚本必须真的在包里——缺失给结构化错误，而不是让 python 的 exit 2 出面
        if not any(tok in pkg["files"] for tok in str(pkg["entrypoint"]).split()):
            raise ApiError(Err.SKILL_PACKAGE_INVALID,
                           f"Skill 包缺少 entrypoint 指向的脚本（entrypoint={pkg['entrypoint']}），请检查包结构")
        res: SkillResult = await sandbox_executor.run_skill(
            st.user_id, task_id=st.task_id, tool_call_id=tool_call_id,
            entrypoint=pkg["entrypoint"], files=pkg["files"],
            expected_checksum=pkg["checksum"],  # 传输完整性（29.3 X-Checksum-SHA256）
            run_id=st.run_id, cfg=st.sandbox_cfg,  # 容器缺失自愈重建（进程重启/idle 回收后）
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
    # 统一进容器口径：脚本型的包（含 references/ 等）本就随 run_skill 投递在容器里——把目录告知模型，
    # 需要时可 run_container_command 查阅参考文件（与手册型同一指引）
    c = sandbox_executor.get(st.user_id)
    note = (f"\n（Skill 包已投递至容器目录 {c.backend.workdir}/skills/{st.task_id}/{tool_call_id}/，"
            f"含 {len(pkg['files'])} 个文件，需要时可用 Read / run_container_command 查阅）") if c else ""
    return f"Skill 「{skill_name}」结果：{body}{note}"
