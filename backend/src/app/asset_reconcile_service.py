"""资产对账（28.7 / B6）：Skill Hub / MCP Registry → OpenOps 资产与工具目录。

- 只拉 `source=openops` 资产（ASSET-001/002）。
- Skill：按 (source_type, skill_key) upsert；checksum 变化 → 追加新版本（历史版本不动）。
- 平台 MCP：`tools/list` 经 Registry 拉取 → `schema_hash` 对比 → 变化则旧 catalog 行 superseded、
  新行入库且**标注不继承**（未标注 → Tool Gateway fail-closed，需管理员重新标注；ASSET-005）。
- 触发：登录（节流 fire-and-forget）、配置页 refresh（POST /assets:reconcile，force）、
  后台循环（OPENOPS_RECONCILE_INTERVAL_S > 0 时启用）。
- 失败：写 `asset.reconcile_failed` 审计；运行边界继续按既有缓存/标注（ASSET-006）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any

from infra.external import mcp_registry_client, skill_hub_client
from infra.repositories import assets, audit, mcp_tools

log = logging.getLogger("openops.reconcile")

RECONCILE_TTL_S = float(os.environ.get("OPENOPS_RECONCILE_TTL_S", "300"))
_last_run: dict[str, float] = {"at": 0.0}


def _reset() -> None:  # 测试隔离
    _last_run["at"] = 0.0


def _due() -> bool:
    return (time.monotonic() - _last_run["at"]) >= RECONCILE_TTL_S


async def reconcile(*, force: bool = False, trigger: str = "manual") -> dict[str, Any]:
    """执行一轮对账；节流窗口内且非 force → {"skipped": True}。"""
    if not force and not _due():
        return {"skipped": True}
    _last_run["at"] = time.monotonic()
    summary: dict[str, Any] = {
        "trigger": trigger, "skills_created": 0, "skill_versions_added": 0,
        "tools_created": 0, "tools_schema_changed": 0, "tools_unchanged": 0,
    }
    try:
        # ---- Skill Hub（ASSET-001：只 source=openops） ----
        for s in await skill_hub_client.list_skills("system"):
            if s.get("source") != "openops":
                continue
            row = await assets.get_skill_by_key(s["source_type"], s["skill_key"])
            if row is None:
                await assets.create_skill(
                    None if s["source_type"] == "platform" else s.get("owner_user_id"),
                    s["source_type"], s["display_name"], s["skill_key"],
                    {"synced_from": "skill_hub"}, s["checksum_sha256"],
                )
                summary["skills_created"] += 1
                continue
            latest = await assets.latest_skill_version(str(row["skill_id"]))
            if latest is None or latest["checksum_sha256"] != s["checksum_sha256"]:
                await assets.add_skill_version(
                    str(row["skill_id"]), (latest["version_no"] if latest else 0) + 1,
                    {"synced_from": "skill_hub"}, s["checksum_sha256"], "system",
                )
                summary["skill_versions_added"] += 1

        # ---- MCP Registry：平台 MCP tools/list → schema_hash 对账（ASSET-005） ----
        for m in await assets.list_platform_mcps():
            for t in await mcp_registry_client.discover_tools(m["display_name"]):
                res = await mcp_tools.sync_catalog_tool(
                    str(m["mcp_version_id"]), t["tool_name"], t["description"],
                    t["input_schema"], t["schema_hash"],
                )
                summary[f"tools_{res}"] += 1

        await audit.insert_event(
            audit_trace_id=str(uuid.uuid4()), event_type="asset.reconciled", user_id="system",
            action=trigger, payload_redacted=summary,
        )
        return summary
    except Exception as e:
        log.warning("asset reconcile failed (%s): %s", trigger, str(e)[:200])
        try:
            await audit.insert_event(
                audit_trace_id=str(uuid.uuid4()), event_type="asset.reconcile_failed", user_id="system",
                action=trigger, reason_code="RECONCILE_FAILED", payload_redacted={"error": str(e)[:200]},
            )
        except Exception:  # pragma: no cover - 审计不可用时仅日志
            log.exception("reconcile failure audit write failed")
        return {"failed": True, "trigger": trigger}


def kick_async(trigger: str) -> None:
    """节流的 fire-and-forget（登录对账用）：TTL 窗口内为 no-op，不阻塞调用方。"""
    if not _due():
        return
    asyncio.get_running_loop().create_task(reconcile(trigger=trigger))


async def background_loop(interval_s: float) -> None:
    """后台 reconciler（28.7 定期对账）：main lifespan 在 OPENOPS_RECONCILE_INTERVAL_S>0 时启动。"""
    while True:
        await asyncio.sleep(interval_s)
        await reconcile(force=True, trigger="background")
