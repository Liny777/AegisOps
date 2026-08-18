"""管理台平台级资产写闭环（29.9）：平台 Skill 上传/删除、平台 MCP 注册/删除。

与 asset_registry_service（用户面）的分工：本模块只处理 `source_type='platform'` 的行，
上游一律带 `is_system=true`（29.9 §2.1/§3.1，需 console 超级管理员权限）。用户面的四个热函数
（sync_user_skills / upload_skill_package / delete_skill / register_mcp）不受本模块影响。

**本模块的核心是「同名收敛」**（_converge_platform_skills）：我们库用自己的 UUID 做主键、靠
`skill_key`（= Hub 命名空间化 skill_id）与上游对齐。Hub 侧同名上传是覆盖（同一条记录换版本），
但只要 skill_key 发生过迁移（存量裸名 `foo` → `system-foo`），本地就会留下两条 display_name
相同的活行，且谁都清不掉——平台面墓碑只认 synced_from='skill_hub'，个人面墓碑够不着平台行。
危害不止于管理台多一行：resolve_skill_alias 是精确键优先，两行并存时 `/foo` 会解析到**过期的旧行**，
run_platform_skill 下载老包。故上传成功后立即收敛到单行。
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

from app import template_service
from domain import tool_key
from domain.errors import ApiError, Err
from infra import egress
from infra.db import row_json
from infra.repositories import assets, audit

log = logging.getLogger("openops.asset_admin")

# 平台 skill 行的来源标记：'skill_hub'=全局 reconcile 落的，'platform_upload'=管理台上传的。
# 两者都必须在 asset_reconcile_service 的缺席墓碑允许集里，否则管理台上传的行在 Hub 侧被删后
# 永远收敛不掉。（下一轮 reconcile 发现 checksum 不同会 add_skill_version 写入 'skill_hub'，
# 行被 reconcile「收养」——这正是两个值都得在集合里的原因。）
PLATFORM_SKILL_SOURCES: tuple[str, ...] = ("skill_hub", "platform_upload")
# 平台 MCP 行的来源标记，语义同上（'mcp_registry'=reconcile ingest，'platform_register'=管理台注册）
PLATFORM_MCP_SOURCES: tuple[str, ...] = ("mcp_registry", "platform_register")

# 管理员改 URL 后的全量拦停标记（本轮拍板：URL 变了视作换了一台服务器，schema 没变的工具也不免审）。
# 待标注计数与测试都引用本常量做精确匹配——管理员手动拉黑的行（别的 reason）不算「待标注」。
URL_RESET_REASON = "URL 变更，待重新标注"

# 上游 delete「已无此资源」的业务码：命中视作已删、继续本地删（口径同 asset_registry_service）
_DELETE_ABSENT_CODES: tuple[int, ...] = (1002,)


def _audit_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------- Skill 上传

def _map_upload_error(e: Any) -> ApiError:
    """SkillHub 上传异常 → ApiError（29.9 业务码表）。

    1003（非超级管理员）单拎出来给 403：在管理面这是**真实可操作的授权事实**（你的 console 账号
    不是 Hub 超管），不是上游故障，压成 502 会让管理员以为是网关挂了。403 对前端安全——
    client.ts 只在 401 跳登录页。"""
    if e.kind == "biz":
        if e.biz_code == 1003:
            return ApiError(Err.FORBIDDEN,
                            f"平台级 Skill 上传需 Skill Hub 超级管理员权限：{e.message}"
                            "（请联系 Skill Hub 管理员开通后重试）")
        if e.biz_code in (2003, 2004):  # 他人已发布 / 跨作用域同名——用户可自救（改 SKILL.md name）
            return ApiError(Err.SKILL_NAME_CONFLICT,
                            f"Skill 名称冲突：{e.message}（请修改 SKILL.md 的 name 后重新上传）")
        if e.biz_code in (2002, 2005):  # 版本号未递增 / 该版本已存在
            return ApiError(Err.VALIDATION_FAILED,
                            f"版本号不可用：{e.message}（请提高 SKILL.md 的 version 后重新打包上传）")
    if e.kind == "network":
        return ApiError(Err.IAM_UPSTREAM, "SkillHub 不可达，上传未执行，请稍后重试", retryable=True)
    return ApiError(Err.IAM_UPSTREAM, f"SkillHub 上传失败：{e}")


async def upload_skill_package(admin: dict[str, Any], filename: str, zip_bytes: bytes,
                               category: str, tags: list[str]) -> dict[str, Any]:
    """上传**平台级** Skill ZIP（29.9 §2.1，is_system=true）→ 写本地 platform 行 → 同名收敛。

    顺序是承重的：先打上游（上游是事实源，它没成功前不碰本地），再 upsert 幸存行，最后扫同名旧行。
    反过来「先扫后写」一旦写失败，Hub 有而我们没有，管理台那一行会空到下一轮 reconcile。"""
    from infra.external import skill_hub_client

    try:
        meta = skill_hub_client.parse_skill_meta(zip_bytes)  # 含 system-/user- 保留前缀预拦截（29.9 §7）
    except ValueError as e:
        raise ApiError(Err.SKILL_PACKAGE_INVALID, str(e)) from None
    checksum = hashlib.sha256(zip_bytes).hexdigest()  # ZIP 原始字节 sha256（真 checksum）

    try:
        result = await skill_hub_client.upload_skill(
            filename, zip_bytes, category, tags, source="openops", is_system=True,
            uploader_id=admin["user_id"])
    except skill_hub_client.SkillHubError as e:
        raise _map_upload_error(e) from None
    except RuntimeError as e:  # 兜底（base 未配等非 SkillHubError 的 RuntimeError）
        raise ApiError(Err.IAM_UPSTREAM, f"SkillHub 上传失败：{e}") from None

    # 键口径：本地 skill_key 必须取上游返回的命名空间化 skill_id（执行下载/后续 sync 都按它对齐），
    # SKILL.md 裸名仅在响应缺失时按平台级规则回退（29.9 未上线的旧网关）
    skill_key = str(result.get("skill_id") or f"system-{meta['name']}")
    display_name = str(result.get("name") or meta["name"])
    manifest = {
        "entrypoint": meta.get("entrypoint") or "python3 run.py",
        "category": category or None, "tags": tags,
        "synced_from": "platform_upload",
        "description": meta.get("description"),  # 发现链路：注入工具描述让 Agent 主动发现
        "latest_version": result.get("version"),
        "uploaded_by": admin["user_id"],
    }

    survivor_id, action = await _upsert_platform_skill(
        skill_key, display_name, manifest, checksum, admin["user_id"])
    merged = await _converge_platform_skills(survivor_id, skill_key, display_name, admin["user_id"])

    await audit.insert_event(
        audit_trace_id=_audit_id(), event_type="skill.uploaded", user_id=admin["user_id"],
        action=action,
        payload_redacted={"scope": "platform", "skill_id": survivor_id, "skill_key": skill_key,
                          "display_name": display_name, "skillhub_action": result.get("action"),
                          "merged": merged},
    )
    return {"skill_id": survivor_id, "skill_key": skill_key, "display_name": display_name,
            "action": action, "skillhub_action": result.get("action"), "merged": len(merged)}


async def _upsert_platform_skill(skill_key: str, display_name: str, manifest: dict[str, Any],
                                 checksum: str, by: str) -> tuple[str, str]:
    """按 (source_type='platform', skill_key) upsert → (survivor_skill_id, action)。

    create 包一层唯一索引冲突捕获：ux_skill_asset_platform_key 会让并发同名上传的落败者撞
    UniqueViolation，重读后落到「加版本」分支——良性竞态不该变成 500。"""
    existing = await assets.get_skill_by_key("platform", skill_key)
    if existing is None:
        try:
            row = await assets.create_skill(None, "platform", display_name, skill_key, manifest, checksum)
            return str(row["skill_id"]), "created"
        except Exception as e:  # noqa: BLE001 —— 只吞唯一索引冲突，其余原样抛
            if "ux_skill_asset_platform_key" not in str(e):
                raise
            log.info("platform skill 并发创建撞唯一索引，转为加版本：%s", skill_key)
            existing = await assets.get_skill_by_key("platform", skill_key)
            if existing is None:  # 理论不可达（索引冲突意味着有活行）；真发生就 fail-loud
                raise
    latest = await assets.latest_skill_version(str(existing["skill_id"]))
    await assets.add_skill_version(
        str(existing["skill_id"]), (int(latest["version_no"]) if latest else 0) + 1,
        manifest, checksum, by)
    return str(existing["skill_id"]), "version_updated"


async def _converge_platform_skills(survivor_id: str, skill_key: str, display_name: str,
                                    by: str) -> list[dict[str, Any]]:
    """同名收敛：软删除幸存行以外、指向同一个逻辑 skill 的活平台行。返回被合并行的明细。

    命中两类：
    - `duplicate_key`：skill_key 相同（并发上传/历史竞态留下的双行）。
    - `same_name_rekeyed`：display_name 相同但 skill_key 不同——Hub 侧改键留下的孤儿
      （存量裸名 `foo` → `system-foo`）。**这就是「同名 skill 覆盖后旧行没清掉」那条。**

    为什么 display_name 规则安全：平台行的 display_name 恒等于 Hub 的 `name` 字段
    （_map_skill 与上传响应都是），从不是本地人工标签；Hub 在系统级作用域内保证 name → skill_id
    一对一（2004 冲突码就是干这个的）。所以「同 name 不同 key 的两条活平台行」只可能是改键孤儿：
    上游已不再列出它，reconcile 的缺席墓碑本来也会清，我们只是拿正向证据（一次成功上传返回了
    权威 key）立即清。

    护栏（防误删优先于清理）：只动确实来自 hub 的行（synced_from ∈ PLATFORM_SKILL_SOURCES）——
    seed 行的 manifest 没有这个键，天然豁免；老 JSON 端点手造行同理。
    **刻意不套 _past_absent_grace**：宽限期是防「从可能陈旧的列表推断已删」的，此处有正向证据、
    无陈旧竞态；套上反而让新产生的重复永远清不掉，正是要修的 bug。
    **刻意不做 asset_in_use 检查**：被墓碑的是同一个 skill 的过期别名，幸存行还在；因为有绑定
    就拒绝收敛，等于让重复永久存在。绑定行由 list_instance_bindings 的 ghost 检测标注「已删除」。"""
    merged: list[dict[str, Any]] = []
    for prow in await assets.list_platform_skills():
        if str(prow["skill_id"]) == survivor_id:
            continue  # 按 UUID 排除而非按 key：两条共享同一 key 的行也能在这一遍里自愈
        if (prow.get("manifest_json") or {}).get("synced_from") not in PLATFORM_SKILL_SOURCES:
            continue
        same_key = str(prow.get("skill_key") or "") == skill_key
        same_name = str(prow.get("display_name") or "") == display_name
        if not (same_key or same_name):
            continue
        await assets.delete_skill(str(prow["skill_id"]), by)
        entry = {"skill_id": str(prow["skill_id"]), "skill_key": prow.get("skill_key"),
                 "reason": "duplicate_key" if same_key else "same_name_rekeyed"}
        merged.append(entry)
        log.info("converged platform skill %s (%s) → %s", prow.get("skill_key"), entry["reason"], skill_key)
        await audit.insert_event(
            audit_trace_id=_audit_id(), event_type="skill.deleted", user_id=by, action="converged",
            payload_redacted={**entry, "superseded_by": survivor_id},
        )
    return merged


# ---------------------------------------------------------------- Skill 删除

def _raise_delete_error(e: Any, *, noun: str, upstream_label: str) -> None:
    """上游删除异常的「明确拒绝」分支 → ApiError（调用方已先处理 404/1002 两条降级）。
    共用于 Skill 与 MCP：两条梯子完全同构，只有名词不同。"""
    if e.kind == "network":
        raise ApiError(Err.IAM_UPSTREAM, f"{upstream_label} 不可达，删除未执行，请稍后重试",
                       retryable=True) from None
    if e.kind == "biz" and e.biz_code == 1003:
        raise ApiError(Err.FORBIDDEN,
                       f"平台级 {noun} 删除需 {upstream_label} 超级管理员权限：{e.message}") from None
    # 上游明确拒绝（无权/参数错等）：不本地删——否则本地消失、下轮同步复活，管理员更困惑
    raise ApiError(Err.IAM_UPSTREAM, f"{upstream_label} 拒绝删除：{e.message}") from None


async def delete_skill(admin: dict[str, Any], skill_id: str) -> dict[str, Any]:
    """删除平台级 Skill：先回删上游（29.9 §2.9），再本地软删。

    降级梯子同 asset_registry_service.delete_skill：上游未上线该接口（HTTP 404）→ 降级仅本地删；
    上游已无此 skill（1002）→ 视作已删继续；上游明确拒绝/不可达 → **不本地删**（避免制造复活假象）。
    与用户面的差异：不做 asset_in_use 拦截（管理员不该被某个用户的陈旧绑定卡住；绑定行走
    list_instance_bindings 的 ghost 降级显示「已删除」，运行时 resolve_available_skills 立即剔除）。"""
    from infra.external import skill_hub_client

    row = await assets.get_skill(skill_id)
    if row is None:
        raise ApiError(Err.NOT_FOUND, "Skill 不存在")
    if row["source_type"] != "platform":
        raise ApiError(Err.FORBIDDEN, "该端点仅删除平台级 Skill；个人 Skill 请在插件页删除")

    upstream = "skipped"  # 非 hub 行（seed/手造）：无上游对应，只本地删
    latest = await assets.latest_skill_version(skill_id)
    synced_from = ((latest or {}).get("manifest_json") or {}).get("synced_from")
    if row.get("skill_key") and synced_from in PLATFORM_SKILL_SOURCES:
        try:
            await skill_hub_client.delete_skill(str(row["skill_key"]))  # skill_key == 上游命名空间化 id
            upstream = "deleted"
        except skill_hub_client.SkillHubError as e:
            if e.kind == "http" and e.status_code == 404:
                log.warning("skillhub delete 接口不存在（29.9 未上线），降级仅本地删：%s", row["skill_key"])
                upstream = "endpoint_missing"
            elif e.kind == "biz" and e.biz_code in _DELETE_ABSENT_CODES:
                upstream = "already_absent"  # 上游已无此 skill → 视作已删，继续本地删
            else:
                _raise_delete_error(e, noun="Skill", upstream_label="Skill Hub")

    await assets.delete_skill(skill_id, admin["user_id"])
    await audit.insert_event(
        audit_trace_id=_audit_id(), event_type="skill.deleted", user_id=admin["user_id"],
        action="admin_delete",
        payload_redacted={"scope": "platform", "skill_id": skill_id,
                          "skill_key": row.get("skill_key"), "upstream": upstream},
    )
    return {"deleted": True, "upstream": upstream}


# ------------------------------------------------- 待标注口径（列表列 + 右上角邮箱共用）

async def _pending_by_server() -> dict[str, dict[str, Any]]:
    """按 server（mcp_display_name）归并「需要管理员标注」的工具数，两类分开数：

    - `unannotated`：annotation_id IS NULL —— 新发现从未标注过的（含 schema 变更后被 sync 软删的）；
    - `url_reset`：status='blocked' 且 reason 恰为 URL_RESET_REASON —— 管理员改 URL 触发的全量拦停。
      管理员**手动**拉黑的行（别的 reason）不算——那是已处理完的决策，不是待办。

    list_mcps 的「待标注」列与 GET /admin/annotation-inbox 共用本口径——两处各自实现必然漂移。"""
    from infra.repositories import mcp_tools

    out: dict[str, dict[str, Any]] = {}
    for c in await mcp_tools.list_catalog_with_annotation():
        name = str(c.get("mcp_display_name") or "")
        slot = out.setdefault(name, {"unannotated": 0, "url_reset": 0})
        if c.get("annotation_id") is None:
            slot["unannotated"] += 1
        elif c.get("annotation_status") == "blocked" and c.get("blocked_reason") == URL_RESET_REASON:
            slot["url_reset"] += 1
    return out


async def annotation_inbox(admin: dict[str, Any]) -> dict[str, Any]:
    """右上角「待标注」邮箱的数据源：派生状态现算，不建通知表。

    「工具待标注」是全局平台状态（不分管理员个人），正确生命周期是**标完自动消失**而非「已读就
    消失」——现算零状态维护，也不会出现"已读但没处理"的悬空。只返回 pending>0 的 server，
    附本地 mcp_id 供前端直达该 server 的 Tool 标注钻取。"""
    by_server = await _pending_by_server()
    id_by_name = {str(m.get("display_name") or ""): str(m["mcp_id"])
                  for m in await assets.list_platform_mcps_with_manifest()}
    items = [{"display_name": name, "mcp_id": id_by_name.get(name),
              "unannotated": p["unannotated"], "url_reset": p["url_reset"],
              "pending": p["unannotated"] + p["url_reset"]}
             for name, p in sorted(by_server.items()) if p["unannotated"] + p["url_reset"] > 0]
    return {"total": sum(i["pending"] for i in items), "items": items}


# ---------------------------------------------------------------- MCP 列表（管理面）

async def list_mcps(admin: dict[str, Any], *, page: int = 1, page_size: int = 20,
                    q: str | None = None) -> dict[str, Any]:
    """管理台平台 MCP 列表。与用户面 `/assets/mcps` 的两点差别，都是管理语义要求的：

    1. **endpoint 不脱敏**：用户面按 30.5 截断展示（那里 endpoint 可能含用户自填的凭据）；
       平台 MCP 的地址是管理员自己录进去的基础设施 URL，本端点又是 Admin 门控，
       对录入者藏他自己填的地址没有意义，反而没法核对/排障。
    2. **不隐藏占位资产**：用户面真机模式会滤掉 endpoint host=mock 的 demo 种子行；管理面要能看见
       它们才能删掉，故 hide_placeholder=False。
    """
    rows, total = await assets.list_mcps_page(
        admin["user_id"], source_type="platform", q=q,
        limit=page_size, offset=(page - 1) * page_size, hide_placeholder=False,
    )
    # 待标注计数：未标注的工具在 Tool Gateway 是 fail-closed 的，管理员必须能一眼看到还欠几个。
    by_server = await _pending_by_server()
    pending = {name: p["unannotated"] + p["url_reset"] for name, p in by_server.items()}

    items: list[dict[str, Any]] = []
    for r in rows:
        d = row_json(r)
        cfg = d.pop("endpoint_config_json", None) or {}
        d["endpoint"] = str(cfg.get("endpoint") or "") if isinstance(cfg, dict) else ""
        manifest = d.pop("manifest_json", None) or {}  # 内部 manifest 不整包透前端，只抽展示字段
        if isinstance(manifest, dict):
            d["description"] = manifest.get("description")
            d["category"] = manifest.get("category")
            d["server_id"] = manifest.get("server_id")  # 上游唯一标识（删除/重注册按它对齐）
            d["transport"] = manifest.get("transport") or d.get("transport")  # 上游词汇优先于本地 'http' 列
            d["synced_from"] = manifest.get("synced_from")  # 让管理员看得出哪条是 seed（无值）哪条来自上游
            # 上游现名：本地 display_name 是复合键左半、刻意不跟随上游改名（否则断掉全部模板绑定），
            # 所以改名这件事必须在管理台显性化，否则管理员只会看到"名字没更新"而无从判断。
            d["upstream_server_name"] = manifest.get("upstream_server_name")
            d["tags"] = manifest.get("tags")  # 编辑弹窗预填用（description 上面已投影）
            d["version"] = manifest.get("version")
        d["tools_unannotated"] = pending.get(str(d.get("display_name") or ""), 0)
        items.append(d)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ---------------------------------------------------------------- MCP 注册

def _map_register_error(e: Any) -> ApiError:
    """MCP Registry 注册异常 → ApiError（29.9 §3.1 业务码表）。"""
    if e.kind == "biz":
        if e.biz_code == 1003:
            return ApiError(Err.FORBIDDEN,
                            f"平台级 MCP 注册需 MCP Registry 超级管理员权限：{e.message}")
        if e.biz_code == 2001:  # server_id 已存在（server_name 兼作 server_id）
            return ApiError(Err.ASSET_IN_USE,
                            f"server_name 已被占用：{e.message}（请换名，或删除同名 server 后重注册）")
        if e.biz_code == 1005:
            return ApiError(Err.VALIDATION_FAILED, f"MCP Registry 拒绝该 transport：{e.message}")
    if e.kind == "network":
        return ApiError(Err.IAM_UPSTREAM, "MCP Registry 不可达，注册未执行，请稍后重试", retryable=True)
    return ApiError(Err.IAM_UPSTREAM, f"MCP Registry 注册失败：{e}")


async def register_mcp(admin: dict[str, Any], req: Any) -> dict[str, Any]:
    """注册**平台级** MCP Server（29.9 §3.1，is_system=true）→ 写/更新本地 platform 行 → 同名收敛。

    命中已有行时**原地更新，绝不重建、绝不改名**：tool catalog 挂在 mcp_version_id 上、模板绑定用
    `{display_name}::{tool_name}` 复合键——重建或派生新版本会让整套标注失联、所有工具掉回
    fail-closed 直到管理员重标注。而重注册（改 URL / 升版本）是常规操作，不能有这种副作用。"""
    from infra.external import mcp_registry_client

    name = (req.server_name or "").strip()
    url = (req.server_url or "").strip()
    if not name:
        raise ApiError(Err.VALIDATION_FAILED, "server_name 不能为空")
    if not url:
        raise ApiError(Err.VALIDATION_FAILED, "server_url 不能为空")  # 空 endpoint 运行时不可达
    if req.transport not in mcp_registry_client.MCP_TRANSPORTS:
        raise ApiError(Err.VALIDATION_FAILED,
                       "transport 仅支持 jsonrpc / sse / streamable_http（29.9 §3.1）")
    # SSRF：平台 MCP 的 URL 同样是人手填的，且运行时真出站（与用户面同一道闸）
    egress.check_mcp_egress(url)

    try:
        result = await mcp_registry_client.register_server(
            server_name=name, server_url=url, description=req.description, version=req.version,
            category=req.category, tags=req.tags, source="openops", is_system=True,
            transport=req.transport)
    except mcp_registry_client.McpRegistryError as e:
        raise _map_register_error(e) from None
    except RuntimeError as e:  # 兜底（base 未配等）
        raise ApiError(Err.IAM_UPSTREAM, f"MCP Registry 注册失败：{e}") from None

    server_id = str(result.get("server_id") or name)  # 契约：server_name 兼作 server_id
    local_name = tool_key.sanitize_server_name(name)  # create_mcp 内部会做同样归一，先算出来才能比对
    manifest = {"synced_from": "platform_register", "server_id": server_id,
                "description": req.description, "category": req.category, "tags": req.tags,
                "transport": req.transport, "version": req.version,
                "upstream_server_name": name, "registered_by": admin["user_id"]}

    candidates = await assets.list_platform_mcps_with_manifest()
    match = (next((m for m in candidates
                   if str((m.get("manifest_json") or {}).get("server_id") or "") == server_id), None)
             or next((m for m in candidates if str(m.get("display_name") or "") == local_name), None))
    if match is None:
        # 本地 transport 列恒为 'http'：运行时全链认这个值，把上游词汇（jsonrpc/sse/streamable_http）
        # 写进列会静默破坏 _MCP_FILTERS 消费方与发现链路；上游值存 manifest。
        row = await assets.create_mcp(None, "platform", name, "http", {"endpoint": url}, manifest)
        survivor_id, action = str(row["mcp_id"]), "created"
    else:
        await assets.update_mcp_endpoint(str(match["mcp_id"]), {"endpoint": url}, admin["user_id"])
        if match.get("mcp_version_id"):
            await assets.update_mcp_version_manifest(
                str(match["mcp_version_id"]), {**(match.get("manifest_json") or {}), **manifest})
        survivor_id, action = str(match["mcp_id"]), "updated"

    merged = await _converge_platform_mcps(candidates, survivor_id, server_id, local_name,
                                           admin["user_id"])
    # 级联：合并掉的 server 其模板引用要摘（口径同 delete_mcp）
    scrubbed = await template_service.renormalize_drafts() if merged else 0
    await audit.insert_event(
        audit_trace_id=_audit_id(), event_type="mcp.registered", user_id=admin["user_id"],
        action=action,
        payload_redacted={"scope": "platform", "mcp_id": survivor_id, "server_id": server_id,
                          "display_name": local_name, "transport": req.transport,
                          "merged": merged, "templates_scrubbed": scrubbed},
    )
    return {"mcp_id": survivor_id, "server_id": server_id, "display_name": local_name,
            "action": action, "merged": len(merged)}


async def _converge_platform_mcps(candidates: list[dict[str, Any]], survivor_id: str,
                                  server_id: str, local_name: str, by: str) -> list[dict[str, Any]]:
    """MCP 侧同名收敛（口径对称 _converge_platform_skills）：软删幸存行以外、同 server_id 或同
    display_name 的活平台行。护栏同样是 synced_from ∈ PLATFORM_MCP_SOURCES——seed 的占位资产
    （manifest 为 {}）天然豁免。"""
    merged: list[dict[str, Any]] = []
    for m in candidates:
        if str(m["mcp_id"]) == survivor_id:
            continue
        if (m.get("manifest_json") or {}).get("synced_from") not in PLATFORM_MCP_SOURCES:
            continue
        same_id = str((m.get("manifest_json") or {}).get("server_id") or "") == server_id
        if not (same_id or str(m.get("display_name") or "") == local_name):
            continue
        tool_names = await assets.tool_names_for_mcp(str(m["mcp_id"]))  # 审计留痕
        await assets.delete_mcp(str(m["mcp_id"]), by)
        entry = {"mcp_id": str(m["mcp_id"]), "display_name": m.get("display_name"),
                 "tool_names": tool_names,
                 "reason": "duplicate_server_id" if same_id else "same_name_rekeyed"}
        merged.append(entry)
        log.info("converged platform mcp %s (%s) → %s", m.get("display_name"), entry["reason"], server_id)
        await audit.insert_event(
            audit_trace_id=_audit_id(), event_type="mcp.deleted", user_id=by, action="converged",
            payload_redacted={**entry, "superseded_by": survivor_id},
        )
    return merged


# ---------------------------------------------------------------- MCP 编辑（29.9 §3.4）

async def update_mcp(admin: dict[str, Any], mcp_id: str, req: Any) -> dict[str, Any]:
    """编辑平台级 MCP 配置（URL/描述/版本/分类/标签/transport）：推 Hub §3.4 → 更新本地
    sre_mcp_asset(endpoint) + sre_mcp_asset_version(manifest) → **URL 变更时**重新发现工具并
    「全量重置全量拦」（本轮拍板：URL 变了视作换了一台服务器，schema 没变的工具也不免审）。

    三条铁律（沿袭 PR74/80）：
    - 不动 display_name（复合键 `{display_name}::{tool}` 左半，改了断掉全部模板绑定）；
    - 不动 mcp_id / mcp_version_id（tool catalog 挂 version_id，换了整套标注失联）；
    - 只改本地不推 Hub 流量不会切——运行时 _dynamic_mcp_specs 的 server_url 来自 Hub 实拉列表，
      推 §3.4 才是让 Agent 真正走新地址的动作。

    **刻意不调 renormalize_drafts**：blocked 是「待重标」不是终态，现在扫掉 draft 引用，重标完还得
    逐个重勾。运行态由 Gateway 热读拦（不靠 scrub）；重标完成 → 绑定原样恢复，零重勾。
    （对照 delete_mcp 调 renormalize：那是整台 server 永久没了，情形不同。）"""
    from infra.external import mcp_registry_client
    from infra.repositories import mcp_tools

    row = await assets.get_mcp(mcp_id)
    if row is None:
        raise ApiError(Err.NOT_FOUND, "MCP 不存在")
    if row["source_type"] != "platform":
        raise ApiError(Err.FORBIDDEN, "该端点仅编辑平台级 MCP；自定义 MCP 请在插件页管理")
    new_url = (req.server_url or "").strip()
    if not new_url:
        raise ApiError(Err.VALIDATION_FAILED, "server_url 不能为空")
    if req.transport and req.transport not in mcp_registry_client.MCP_TRANSPORTS:
        raise ApiError(Err.VALIDATION_FAILED, "transport 仅支持 jsonrpc / sse / streamable_http（29.9 §3.4）")
    egress.check_mcp_egress(new_url)  # SSRF：人手填的 URL，运行时真出站（与注册同一道闸）

    latest = await assets.latest_mcp_version(mcp_id)
    manifest = (latest or {}).get("manifest_json") or {}
    old_url = str((row.get("endpoint_config_json") or {}).get("endpoint") or "")
    url_changed = new_url != old_url
    server_id = str(manifest.get("server_id") or row.get("display_name") or "")

    # ---- ① 推 Hub（仅确实来自上游的行；seed/手造行无上游对应，只改本地）----
    upstream = "skipped"
    if server_id and manifest.get("synced_from") in PLATFORM_MCP_SOURCES:
        try:
            await mcp_registry_client.update_server_config(
                server_id, server_url=new_url, description=req.description or None,
                version=req.version or None, category=req.category or None,
                tags=req.tags or None, transport=req.transport or None)
            upstream = "updated"
        except mcp_registry_client.McpRegistryError as e:
            if e.kind == "http" and e.status_code == 404:
                # 对端 §3.4 未上线：降级只改本地（同 delete 的 endpoint_missing 口径）。
                # 注意此形态下 Agent 流量仍走 Hub 里的旧地址——文案说清，别让管理员以为已切换。
                log.warning("mcps/config/update 接口不存在（29.9 未上线），降级仅本地改：%s", server_id)
                upstream = "endpoint_missing"
            elif e.kind == "biz" and e.biz_code in _DELETE_ABSENT_CODES:
                # 上游已无此 server：编辑幽灵毫无意义（运行时流量走 Hub 列表，本地改了也不生效）
                raise ApiError(Err.NOT_FOUND,
                               f"MCP Registry 已无此 server（{server_id}）：请先重新注册，或删除本地记录"
                               ) from None
            elif e.kind == "biz" and e.biz_code == 1003:
                raise ApiError(Err.FORBIDDEN,
                               f"平台级 MCP 修改需 MCP Registry 超级管理员权限：{e.message}") from None
            elif e.kind == "biz" and e.biz_code == 1005:
                raise ApiError(Err.VALIDATION_FAILED, f"MCP Registry 拒绝该 transport：{e.message}") from None
            elif e.kind == "network":
                raise ApiError(Err.IAM_UPSTREAM, "MCP Registry 不可达，修改未执行，请稍后重试",
                               retryable=True) from None
            else:
                raise ApiError(Err.IAM_UPSTREAM, f"MCP Registry 拒绝修改：{e.message}") from None

    # ---- ② 本地更新（保 display_name / mcp_id / mcp_version_id）----
    if url_changed:
        await assets.update_mcp_endpoint(mcp_id, {"endpoint": new_url}, admin["user_id"])
    # PUT 语义：弹窗全量预填后提交，发来的就是管理员的完整意图（空串=清空）；
    # 仅 transport 保底回退（schema pattern 保证合法值，空串只可能是旧客户端缺字段）。
    new_manifest = {**manifest,
                    "description": req.description, "version": req.version,
                    "category": req.category,
                    "tags": req.tags if req.tags is not None else manifest.get("tags"),
                    "transport": req.transport or manifest.get("transport")}
    if latest and new_manifest != manifest:
        await assets.update_mcp_version_manifest(str(latest["mcp_version_id"]), new_manifest)

    # ---- ③ URL 变更 → 工具刷新 + 全量拦停（只改元数据不折腾标注）----
    tools_summary: dict[str, Any] = {}
    if url_changed and latest:
        vid = str(latest["mcp_version_id"])
        counts = {"created": 0, "schema_changed": 0, "unchanged": 0}
        removed: list[str] = []
        refresh_error: str | None = None
        try:
            from infra import host_ip
            from infra.iam_headers import iam_auth_headers

            discovered = await mcp_registry_client.discover_tools(
                new_url, {**host_ip.ec2_ip_headers(), **iam_auth_headers()})
        except Exception as e:  # noqa: BLE001 —— 发现失败不清扫（防瞬时故障误清全部），但仍全拦（见下）
            refresh_error = f"{type(e).__name__}: {str(e)[:200]}"
            log.warning("[mcp-update] 新 URL 工具发现失败（不清扫、仍全量拦停）：%s", refresh_error)
            discovered = None
        if discovered is not None:
            for t in discovered:
                res = await mcp_tools.sync_catalog_tool(
                    vid, t["tool_name"], t["description"], t["input_schema"], t["schema_hash"])
                counts[res] += 1
            # 发现**成功**才有资格判缺席：拿到完整新列表 → 不在列表里的 = 该 server 已没有这个工具
            removed = await mcp_tools.remove_absent_catalog_tools(
                vid, {str(t["tool_name"]) for t in discovered})
        # 全量拦停（拍板口径）：URL 已变是正向证据，旧工具面必须停——发现失败也照拦。
        # 写 blocked 行而非软删标注：动态平台工具对「标注缺失」有合成 allowed 的豁免
        # （tool_gateway origin=dynamic），软删=放行；blocked 行经 Gateway 热读**立即**拦（含进行中任务）。
        blocked = 0
        for t in await mcp_tools.list_live_catalog_tools(vid):
            await mcp_tools.save_annotation(
                str(t["tool_catalog_id"]), True, False, "none", None,
                "blocked", URL_RESET_REASON, admin["user_id"])
            blocked += 1
        tools_summary = {**counts, "removed": removed, "blocked": blocked}
        if refresh_error:
            tools_summary["refresh_error"] = refresh_error

    await audit.insert_event(
        audit_trace_id=_audit_id(), event_type="mcp.updated", user_id=admin["user_id"],
        action="admin_update",
        payload_redacted={"scope": "platform", "mcp_id": mcp_id, "server_id": server_id,
                          "url_changed": url_changed, "old_url": old_url, "new_url": new_url,
                          "upstream": upstream, "tools": tools_summary},
    )
    return {"mcp_id": mcp_id, "server_id": server_id, "upstream": upstream,
            "url_changed": url_changed, "tools": tools_summary}


# ---------------------------------------------------------------- MCP 删除

async def delete_mcp(admin: dict[str, Any], mcp_id: str) -> dict[str, Any]:
    """删除平台级 MCP：先回删上游（29.9 §3.6 物理删除），再本地软删 + 模板引用级联清理。
    降级梯子与 delete_skill 完全同构。不做 asset_in_use 拦截（同 delete_skill 的理由）。"""
    from infra.external import mcp_registry_client

    row = await assets.get_mcp(mcp_id)
    if row is None:
        raise ApiError(Err.NOT_FOUND, "MCP 不存在")
    if row["source_type"] != "platform":
        raise ApiError(Err.FORBIDDEN, "该端点仅删除平台级 MCP；自定义 MCP 请在插件页删除")

    latest = await assets.latest_mcp_version(mcp_id)
    manifest = (latest or {}).get("manifest_json") or {}
    # server_id 取自版本 manifest（reconcile ingest / 管理台注册落的），缺失回退 display_name
    # （=注册时的 server_name）——与 asset_registry_service.mcp_detail 同一回退口径
    server_id = str(manifest.get("server_id") or row.get("display_name") or "")
    upstream = "skipped"
    if server_id and manifest.get("synced_from") in PLATFORM_MCP_SOURCES:
        try:
            await mcp_registry_client.delete_server(server_id)
            upstream = "deleted"
        except mcp_registry_client.McpRegistryError as e:
            if e.kind == "http" and e.status_code == 404:
                log.warning("mcps/delete 接口不存在（29.9 未上线），降级仅本地删：%s", server_id)
                upstream = "endpoint_missing"
            elif e.kind == "biz" and e.biz_code in _DELETE_ABSENT_CODES:
                upstream = "already_absent"
            else:
                _raise_delete_error(e, noun="MCP", upstream_label="MCP Registry")

    tool_names = await assets.tool_names_for_mcp(mcp_id)  # 审计留痕（catalog 随 asset 保留）
    await assets.delete_mcp(mcp_id, admin["user_id"])
    # 级联清理：删后对全部 draft 重跑归一化——被删 server 的引用被摘。解析基于 runtime_annotations，
    # 其 catalog 查询已排除已删资产的行，故删除即刻生效。
    scrubbed = await template_service.renormalize_drafts()
    await audit.insert_event(
        audit_trace_id=_audit_id(), event_type="mcp.deleted", user_id=admin["user_id"],
        action="admin_delete",
        payload_redacted={"scope": "platform", "mcp_id": mcp_id, "server_id": server_id,
                          "tool_names": tool_names, "templates_scrubbed": scrubbed,
                          "upstream": upstream},
    )
    return {"deleted": True, "upstream": upstream}
