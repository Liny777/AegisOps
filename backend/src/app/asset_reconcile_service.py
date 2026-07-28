"""资产对账（28.7 / B6）：Skill Hub / MCP Registry → OpenOps 资产与工具目录。

- 只拉 `source=openops` 资产（ASSET-001/002）。
- Skill：按 (source_type, skill_key) upsert；checksum 变化 → 追加新版本（历史版本不动）；
  上游列表**缺席**的平台 skill → 软删墓碑收敛（synced_from='skill_hub' 行、上游子集非空、过宽限期，
  三护栏防误删；个人面同款在 asset_registry_service.sync_user_skills）。MCP 侧缺口未修（create-if-missing）。
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

from infra import host_ip
from infra.external import mcp_registry_client, skill_hub_client
from infra.repositories import assets, audit, mcp_tools

log = logging.getLogger("openops.reconcile")

RECONCILE_TTL_S = float(os.environ.get("OPENOPS_RECONCILE_TTL_S", "300"))
_last_run: dict[str, float] = {"at": 0.0}


def _reset() -> None:  # 测试隔离
    _last_run["at"] = 0.0


def _due() -> bool:
    return (time.monotonic() - _last_run["at"]) >= RECONCILE_TTL_S


async def _fetch_skill_description(skill_key: str, version_no: Any) -> str | None:
    """**兜底**：下载 Skill 包解 SKILL.md frontmatter 取 description。仅在列表项没带描述时才走
    （见 _skill_description）。失败降级 None 不阻断对账。"""
    try:
        pkg = await skill_hub_client.download_skill_package(skill_key, int(version_no or 1))
        return pkg.get("description")
    except Exception as e:  # noqa: BLE001 —— 下载失败不炸整轮对账
        log.warning("skill description fetch failed (%s): %s", skill_key, str(e)[:200])
        return None


async def _skill_description(s: dict[str, Any]) -> str | None:
    """取 Skill 描述（发现链路）：**优先用列表项自带的 description**——29.3 §2.2 列表本就返回
    `latest_description`（经 _map_skill 映射），零额外请求；列表没给（老包/对端未填）才回退下载整包解
    SKILL.md。description 只影响 Agent 发现时的用途提示丰富度，取不到降级 None、不阻断对账。"""
    if s.get("description"):
        return str(s["description"])
    return await _fetch_skill_description(s["skill_key"], s.get("version_no"))


async def reconcile(*, force: bool = False, trigger: str = "manual") -> dict[str, Any]:
    """执行一轮对账；节流窗口内且非 force → {"skipped": True}。"""
    if not force and not _due():
        return {"skipped": True}
    _last_run["at"] = time.monotonic()
    summary: dict[str, Any] = {
        "trigger": trigger, "skills_created": 0, "skill_versions_added": 0, "skill_manifests_refreshed": 0,
        "skills_tombstoned": 0,
        "mcps_created": 0, "tools_created": 0, "tools_schema_changed": 0, "tools_unchanged": 0,
    }
    try:
        # ---- Skill Hub（ASSET-001：只 source=openops；且只收平台 skill） ----
        # 个人 skill（source_type='user'）改由 asset_registry_service.sync_user_skills 按「当前 viewer」
        # 同步：全局 reconcile 只有一个 cookie 身份、只能看到一个人的个人 skill 且 owner 必错（存成
        # created_by 而非 viewer id）→ 交给按用户路径，避免双写、错 owner 的半坏行。
        # 先整表物化：缺席墓碑（下方）必须建立在「上游列表完整」之上（list_skills 翻页取全、失败即 raise）
        listed = [s for s in await skill_hub_client.list_skills("system")
                  if s.get("source") == "openops" and s.get("source_type") == "platform"]
        for s in listed:
            # §2.2 semver + category + description 落进 manifest_json（零迁移展示/发现口径；
            # latest_version=SkillHub 原串）。description 优先取列表自带（§2.2 latest_description），
            # 列表没给才回退下载解包——见 _skill_description
            manifest = {"synced_from": "skill_hub", "latest_version": s.get("latest_version"),
                        "category": s.get("category"), "updated_date": s.get("updated_date")}
            row = await assets.get_skill_by_key(s["source_type"], s["skill_key"])
            if row is None:
                manifest["description"] = await _skill_description(s)
                await assets.create_skill(
                    None if s["source_type"] == "platform" else s.get("owner_user_id"),
                    s["source_type"], s["display_name"], s["skill_key"],
                    manifest, s["checksum_sha256"],
                )
                summary["skills_created"] += 1
                continue
            latest = await assets.latest_skill_version(str(row["skill_id"]))
            if latest is None or latest["checksum_sha256"] != s["checksum_sha256"]:
                manifest["description"] = await _skill_description(s)
                await assets.add_skill_version(
                    str(row["skill_id"]), (latest["version_no"] if latest else 0) + 1,
                    manifest, s["checksum_sha256"], "system",
                )
                summary["skill_versions_added"] += 1
            else:
                # checksum 未变→不新增版本；但若现有 manifest 缺/旧 semver（改动前的旧行、或 SkillHub 侧改了
                # latest_version/category）或从未抽过 description（键缺失）→ 原地合并回填（非新版本）。
                # 用「键是否存在」而非真值判断 description，避免无 description 的 skill 每轮都重复回源。
                cur = latest.get("manifest_json") or {}
                # 键缺失（从未抽过）或存量坏值（description 被旧解析器截成裸块标量指示符 `>`/`|`）→ 重解析回填。
                # 后者是自愈：修 parser 前入库的 `>` 坏行，下轮对账即被正确 description 覆盖。
                need_desc = ("description" not in cur
                             or skill_hub_client._looks_like_block_scalar_indicator(cur.get("description")))
                if (cur.get("latest_version") != s.get("latest_version")
                        or cur.get("category") != s.get("category")
                        or cur.get("updated_date") != s.get("updated_date") or need_desc):
                    desc = (await _skill_description(s) if need_desc else cur.get("description"))
                    await assets.update_skill_version_manifest(
                        str(latest["skill_version_id"]),
                        {**cur, "latest_version": s.get("latest_version"),
                         "category": s.get("category"), "updated_date": s.get("updated_date"),
                         "description": desc},
                    )
                    summary["skill_manifests_refreshed"] += 1

        # ---- 缺席即墓碑（平台面）：上游列表已不含的本地平台 skill → 软删收敛（修"hub 删了、本地永存"）。
        # 护栏同 sync_user_skills（个人面）：上游平台子集为空整段跳过（清空型操作要有非空上游证据）；
        # 只动 reconcile 自己写入的行（synced_from='skill_hub'——seed 行无此键天然豁免）；行龄须过宽限期。
        from app.asset_registry_service import _past_absent_grace  # 局部导入避免模块环

        upstream_platform = {str(s["skill_key"]) for s in listed}
        if not upstream_platform:
            summary["skills_tombstone_skipped"] = "empty_upstream"
        else:
            for prow in await assets.list_platform_skills():
                if (prow.get("manifest_json") or {}).get("synced_from") != "skill_hub":
                    continue
                if str(prow.get("skill_key") or "") in upstream_platform or not _past_absent_grace(prow):
                    continue
                await assets.delete_skill(str(prow["skill_id"]), "system")
                log.info("tombstoned absent platform skill: %s", prow.get("skill_key"))
                summary["skills_tombstoned"] += 1

        # ---- MCP Registry：注册表 server → 平台 MCP 资产入库（与 Skill 分支对称；内网实测缺口：
        # 此前只刷已有资产的 catalog，真 server（如 alarm-server）永不落库 → 设置页/管理台看不到）----
        try:
            existing = {str(m.get("display_name")) for m in await assets.list_platform_mcps()}
            for srv in await mcp_registry_client.list_servers():
                url = str(srv.get("server_url") or "")
                if mcp_registry_client.is_placeholder_endpoint(url):  # 占位防呆（同 discover_tools 口径）
                    continue
                name = str(srv.get("server_name") or srv.get("server_id") or "")
                if not name or name in existing:
                    continue  # V1 create-if-missing（改名/下线同步不做）
                await assets.create_mcp(None, "platform", name, "http", {"endpoint": url},
                                        {"synced_from": "mcp_registry", "server_id": srv.get("server_id"),
                                         "description": srv.get("description", "")})
                existing.add(name)
                summary["mcps_created"] += 1
        except Exception as e:  # noqa: BLE001 —— 注册表不可达不炸整轮（skill 已对账完，catalog 照刷）
            log.warning("mcp registry ingest failed: %s", str(e)[:200])
            summary["mcp_ingest_error"] = str(e)[:200]

        # ---- MCP Registry：平台 MCP tools/list → schema_hash 对账（ASSET-005） ----
        # 按 server 隔离异常（对齐上方 ingest 块口径）：一家 server 坏（握手拒/超时/不规范）
        # 只记错继续下一家——此前一家抛错会中断后续所有家的工具同步并把整轮标 failed（内网实测踩坑）
        # 方向守卫（2026-07-23 内网污染防呆）：mock 模式 discover_tools 无视 URL 恒回内置 _TOOLS
        # （设计内行为，勿当 bug 修——本地端到端依赖它），但把 _TOOLS 写进**真 endpoint 资产**的
        # catalog 就是污染（内网实锤：每个真 server 名下都多出 query_resource/recover_execute，
        # 引发标注同名冲突与模板编辑勾选联动）。口径：mock 只同步占位/种子资产（demo 目录的唯一
        # 合法宿主）；real 跳过占位资产（seed 已写过目录+标注，且真机三层口径本就把占位排除在
        # 展示/运行时之外）。占位判定与 discover_tools / 上方 ingest 防呆同一函数。
        mock_mode = os.getenv("OPENOPS_MCPREGISTRY", "mock").lower() != "real"
        for m in await assets.list_platform_mcps():
            server_url = (m.get("endpoint_config_json") or {}).get("endpoint", "")  # 29.3 proxy 必填 url（mock 忽略）
            if mcp_registry_client.is_placeholder_endpoint(server_url) != mock_mode:
                # mock×真 endpoint：防污染跳过；real×占位（含 endpoint 为空的错配行，意味着该
                # server 目录永不更新）：跳过并记 summary，让配置异味持续暴露在 asset.reconciled 审计里
                summary.setdefault("tools_skipped_guard", []).append(str(m.get("display_name")))
                continue
            try:
                # 平台支路（上面 list_platform_mcps 已按 source_type='platform' 过滤）：带 x-ec2-ip
                for t in await mcp_registry_client.discover_tools(server_url, host_ip.ec2_ip_headers()):
                    res = await mcp_tools.sync_catalog_tool(
                        str(m["mcp_version_id"]), t["tool_name"], t["description"],
                        t["input_schema"], t["schema_hash"],
                    )
                    summary[f"tools_{res}"] += 1
            except Exception as e:  # noqa: BLE001
                log.warning("mcp tools sync failed (%s): %s", m.get("display_name"), str(e)[:200])
                summary.setdefault("tool_sync_errors", {})[str(m.get("display_name"))] = str(e)[:120]

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
