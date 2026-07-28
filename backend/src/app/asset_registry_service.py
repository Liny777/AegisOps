"""Asset Registry：用户 Skill / HTTP MCP 资产 + 实例绑定（30.5 语义）。"""
from __future__ import annotations

import hashlib
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app import agent_team_service, template_service
from domain.errors import ApiError, Err
from infra import egress
from infra.db import row_json
from infra.repositories import agent_teams, assets, audit

log = logging.getLogger("openops.user_skill_sync")

# 个人 skill「按用户」同步：SkillHub 已按 cookie 把个人 skill 限定为调用者本人，故列表时用 viewer
# cookie 拉到的 source_type='user' 即他自己的 → 归属 viewer 的 user_id（过读过滤、可绑定）。
# 全局 reconcile 只收平台 skill（见 asset_reconcile_service）。per-user 节流避免每次 GET 都打 SkillHub。
_USER_SKILL_TTL_S = float(os.environ.get("OPENOPS_USER_SKILL_TTL_S", os.environ.get("OPENOPS_RECONCILE_TTL_S", "300")))
_user_skill_synced: dict[str, float] = {}  # user_id -> 上次同步的 monotonic 时刻

# 缺席墓碑宽限期：本地行创建后至少存活这么久才允许因"上游列表缺席"被软删——防"刚上传、
# 并发同步用的还是旧列表"的竞态误删（上传与同步无互锁，靠时间窗兜底）。
_ABSENT_GRACE_S = float(os.environ.get("OPENOPS_SKILL_ABSENT_GRACE_S", "600"))


def _past_absent_grace(row: dict[str, Any]) -> bool:
    """行龄是否已过缺席墓碑宽限期（creation_date 为 timestamptz → tz-aware 比较）。"""
    created = row.get("creation_date")
    if created is None:
        return False  # 无创建时间（异常形状）：保守不删
    return datetime.now(timezone.utc) - created >= timedelta(seconds=_ABSENT_GRACE_S)


def _reset_user_skill_sync() -> None:  # 测试隔离（对齐 asset_reconcile_service._reset）
    _user_skill_synced.clear()


def invalidate_user_skill_sync(user_id: str) -> None:
    """使某用户的同步节流失效：下次 list_skills 强制重拉（手动「同步资产」用）。"""
    _user_skill_synced.pop(user_id, None)


async def sync_user_skills(user: dict[str, Any]) -> None:
    """请求内按 viewer 同步个人 skill：用 viewer cookie 拉 SkillHub，把 source_type='user' 的 skill
    upsert 进本地表、归属 viewer 的 user_id；上游列表**缺席**的本地行走墓碑收敛
    （_tombstone_absent_user_skills，三护栏防误删）。per-user 节流；失败只记日志不阻断列表读（读本地兜底）。"""
    uid = user["user_id"]
    now = time.monotonic()
    if now - _user_skill_synced.get(uid, 0.0) < _USER_SKILL_TTL_S:
        return
    _user_skill_synced[uid] = now  # await 前乐观写入：并发同用户塌缩为一次
    try:
        from infra.external import skill_hub_client

        # 先整表物化：翻页取全、失败即 raise（进不了下方任何写路径）。缺席墓碑必须建立在
        # 「上游列表完整」之上——边迭代边判缺席，半途失败会把没读到的当"已删"误清。
        listed = [s for s in await skill_hub_client.list_skills(uid)  # viewer cookie 经 console_cookie 请求上下文透传
                  if s.get("source") == "openops" and s.get("source_type") == "user"]  # 只收个人 skill；平台走全局 reconcile
        for s in listed:
            manifest = {"synced_from": "skill_hub_user", "latest_version": s.get("latest_version"),
                        "category": s.get("category"), "updated_date": s.get("updated_date")}
            existing = await assets.get_user_skill_by_key(uid, s["skill_key"])
            if existing is None:
                await assets.create_skill(uid, "user", s["display_name"], s["skill_key"],
                                          manifest, s["checksum_sha256"])
                continue
            latest = await assets.latest_skill_version(str(existing["skill_id"]))
            if latest is None or latest["checksum_sha256"] != s["checksum_sha256"]:
                await assets.add_skill_version(
                    str(existing["skill_id"]), (latest["version_no"] if latest else 0) + 1,
                    manifest, s["checksum_sha256"], uid)
            else:
                cur = latest.get("manifest_json") or {}
                if (cur.get("latest_version") != s.get("latest_version")
                        or cur.get("category") != s.get("category")
                        or cur.get("updated_date") != s.get("updated_date")):
                    await assets.update_skill_version_manifest(
                        str(latest["skill_version_id"]),
                        {**cur, "latest_version": s.get("latest_version"), "category": s.get("category"),
                         "updated_date": s.get("updated_date")})
        await _tombstone_absent_user_skills(uid, {str(s["skill_key"]) for s in listed})
    except Exception as e:  # noqa: BLE001 —— SkillHub 挂/cookie 失效不阻断列表读
        log.warning("user skill sync failed (%s): %s", uid, str(e)[:200])


async def _tombstone_absent_user_skills(uid: str, upstream_keys: set[str]) -> None:
    """缺席即墓碑（个人面）：上游列表里已不存在的本地个人 skill → 软删收敛（修"hub 删了、本地永存"）。

    护栏（防误删优先于清理）：
    - 上游个人子集为**空**时整段跳过——hub 的 list 不要求认证，cookie 失效会 200 但个人子集为空，
      误当"全删了"会清光该用户个人 skill 并连带丢 mute 关系。代价：删到只剩 0 个时不自动收敛，
      由手动删除兜底（_DELETE_ABSENT_CODES 已能删上游缺席行）。
    - 只动确实来自 hub 的行（synced_from ∈ upload/skill_hub_user）；老 JSON 端点手造行无上游对应，恒不动。
    - 行龄须过宽限期（_ABSENT_GRACE_S）——防刚上传的行被并发同步的旧列表误删。
    不做 asset_in_use 检查：上游已删，本地留着也执行不了；绑定行由 list_instance_bindings 标注"deleted"。"""
    if not upstream_keys:
        log.info("skip absent-tombstone (%s): 上游个人子集为空（可能 cookie 失效），不做清理", uid)
        return
    for row in await assets.list_skills(uid, include_platform=False):
        synced_from = (row.get("manifest_json") or {}).get("synced_from")
        if synced_from not in ("upload", "skill_hub_user"):
            continue
        if str(row.get("skill_key") or "") in upstream_keys or not _past_absent_grace(row):
            continue
        await assets.delete_skill(str(row["skill_id"]), "sync")
        log.info("tombstoned absent user skill (%s): %s", uid, row.get("skill_key"))
        await audit.insert_event(
            audit_trace_id=str(uuid.uuid4()), event_type="skill.deleted", user_id=uid,
            action="upstream_absent",
            payload_redacted={"skill_id": str(row["skill_id"]), "skill_key": row.get("skill_key")},
        )


async def list_skills(user: dict[str, Any], *, page: int = 1, page_size: int = 20,
                      source_type: str | None = None, q: str | None = None) -> dict[str, Any]:
    """UI 分页读（对齐 29.3 §2.2 口径）→ {items,total,page,page_size}。

    source_type/q 过滤走服务端：分页后再在客户端过滤，每页数量必然错乱（管理台按 platform 过滤、
    插件页按来源分组+搜索都吃这条）。"""
    await sync_user_skills(user)  # 读本地前先按 viewer 同步个人 skill（节流内为 no-op）
    rows, total = await assets.list_skills_page(
        user["user_id"], source_type=source_type, q=q,
        limit=page_size, offset=(page - 1) * page_size,
    )
    items: list[dict[str, Any]] = []
    for r in rows:
        d = row_json(r)
        # §2.2 semver / 分类 / 描述都存在最新版本的 manifest_json 里（reconcile/upload 落库）；抽到行顶层供 UI
        manifest = d.pop("manifest_json", None) or {}  # 抽完即弃，不把内部 manifest 透给前端
        if isinstance(manifest, dict):
            d["latest_version"] = manifest.get("latest_version")  # None → 前端回退 v{version_no}
            d["category"] = manifest.get("category")
            d["updated_date"] = manifest.get("updated_date")  # SkillHub §2.2 更新时间（管理台「更新时间」列；未同步到 → 前端回退「—」）
            d["description"] = manifest.get("description")  # 插件页「说明」的真描述（此前被 pop 一并丢弃）
        items.append(d)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def skill_detail(user: dict[str, Any], skill_key: str) -> dict[str, Any]:
    """Skill 真详情（插件页「说明」用）：先按 owner 作用域定位本地行（个人 skill 只能看自己的；平台 skill 公有），
    再取上游 29.3 §2.4 详情（含 SKILL.md 全文）。**上游失败降级本地 manifest 的 description**，不阻断 UI。"""
    row = (await assets.get_user_skill_by_key(user["user_id"], skill_key)
           or await assets.get_skill_by_key("platform", skill_key))
    if row is None:
        raise ApiError(Err.NOT_FOUND, "Skill 不存在")
    latest = await assets.latest_skill_version(str(row["skill_id"]))
    local = (latest or {}).get("manifest_json") or {}
    out: dict[str, Any] = {
        "skill_key": skill_key, "display_name": row.get("display_name"), "source_type": row.get("source_type"),
        "version": local.get("latest_version"), "category": local.get("category"),
        "description": local.get("description"), "content": None, "detail_source": "local",
    }
    try:
        from infra.external import skill_hub_client

        d = await skill_hub_client.get_skill_detail(skill_key)
        out.update({"description": d.get("description") or out["description"],
                    "content": d.get("content"), "tags": d.get("tags"),
                    "version": d.get("version") or out["version"],
                    "category": d.get("category") or out["category"], "detail_source": "skillhub"})
    except Exception as e:  # noqa: BLE001 —— 上游挂/cookie 失效不阻断：本地描述照常展示
        log.warning("skill detail fetch failed (%s): %s", skill_key, str(e)[:200])
    return out


async def mcp_detail(user: dict[str, Any], mcp_id: str) -> dict[str, Any]:
    """MCP 真详情（插件页「说明」用）：owner 校验后取上游 29.3 §3.3；上游失败降级本地 manifest 的 description。
    server_id 取自本地版本 manifest（reconcile ingest 落的），缺失回退 display_name（=注册时的 server_name）。"""
    row = await assets.get_mcp(mcp_id)
    if row is None:
        raise ApiError(Err.NOT_FOUND, "MCP 不存在")
    if row["source_type"] == "user" and row["owner_user_id"] != user["user_id"]:
        raise ApiError(Err.FORBIDDEN, "无权查看该 MCP")
    latest = await assets.latest_mcp_version(str(row["mcp_id"]))
    local = (latest or {}).get("manifest_json") or {}
    server_id = str(local.get("server_id") or row.get("display_name") or "")
    out: dict[str, Any] = {
        "mcp_id": mcp_id, "display_name": row.get("display_name"), "source_type": row.get("source_type"),
        "server_id": server_id, "description": local.get("description"),
        "category": local.get("category"), "detail_source": "local",
    }
    try:
        from infra.external import mcp_registry_client

        d = await mcp_registry_client.get_mcp_detail(server_id)
        out.update({"description": d.get("description") or out["description"],
                    "transport": d.get("transport"), "version": d.get("version"),
                    "tags": d.get("tags"), "status": d.get("status"),
                    "category": d.get("category") or out["category"], "detail_source": "mcp_registry"})
    except Exception as e:  # noqa: BLE001 —— 注册表挂不阻断：本地描述照常展示
        log.warning("mcp detail fetch failed (%s): %s", server_id, str(e)[:200])
    return out


async def upload_skill(user: dict[str, Any], req: Any) -> dict[str, Any]:
    checksum = req.checksum_sha256 or hashlib.sha256(req.display_name.encode()).hexdigest()
    key = req.display_name.strip().lower().replace(" ", "-")
    return await assets.create_skill(user["user_id"], "user", req.display_name, key, req.manifest_json, checksum)


async def upload_skill_package(user: dict[str, Any], filename: str, zip_bytes: bytes,
                               category: str, tags: list[str]) -> dict[str, Any]:
    """上传 Skill ZIP（29.3 §2.1）：转发 SkillHub（real 真传 / mock 合成）+ 写本地目录即时可见。
    本地按 skill_key 幂等（已存在→加版本）；reconcile 后续按 skill_key 收敛，避免重复行。"""
    from infra.external import skill_hub_client

    try:
        meta = skill_hub_client.parse_skill_meta(zip_bytes)
    except ValueError as e:
        raise ApiError(Err.SKILL_PACKAGE_INVALID, str(e)) from None
    checksum = hashlib.sha256(zip_bytes).hexdigest()  # ZIP 原始字节 sha256（真 checksum，非名称假造）
    try:
        result = await skill_hub_client.upload_skill(
            filename, zip_bytes, category, tags, source="openops", is_system=False,
            uploader_id=user["user_id"])
    except skill_hub_client.SkillHubError as e:
        # 29.9：2003 他人已发布 / 2004 跨作用域同名——用户可自救（改 SKILL.md name），透传上游说法转 409；
        # 其余（network/http/别的业务码）维持 502 上游错误口径
        if e.kind == "biz" and e.biz_code in (2003, 2004):
            raise ApiError(Err.SKILL_NAME_CONFLICT,
                           f"Skill 名称冲突：{e.message}（请修改 SKILL.md 的 name 后重新上传）") from None
        raise ApiError(Err.IAM_UPSTREAM, f"SkillHub 上传失败：{e}") from None
    except RuntimeError as e:  # 兜底（base 未配等非 SkillHubError 的 RuntimeError）
        raise ApiError(Err.IAM_UPSTREAM, f"SkillHub 上传失败：{e}") from None

    # 29.9 键口径：本地 skill_key 必须取上游返回的命名空间化 skill_id（执行下载/后续 sync 都按它对齐），
    # SKILL.md 裸 name 仅在响应缺失时回退（29.9 未上线的旧网关）；display_name 恒为原始名（展示/别名解析）
    skill_key = str(result.get("skill_id") or meta["skill_key"])
    display_name = str(result.get("name") or meta["name"])
    manifest = {"entrypoint": meta.get("entrypoint") or "python3 run.py",
                "category": category or None, "tags": tags, "synced_from": "upload",  # 分类/标签可空（上传流程已移除该输入）→ 列表回退「—」
                "description": meta.get("description"),  # SKILL.md frontmatter 的用途（发现链路：注入工具描述让 Agent 主动发现）
                "latest_version": result.get("version")}  # §2.1 上传响应 version → 上传后即刻可展示 semver
    existing = await assets.get_user_skill_by_key(user["user_id"], skill_key)  # owner 作用域：同 (owner, key) 才认作同一 skill
    if existing is None:
        row = await assets.create_skill(user["user_id"], "user", display_name, skill_key, manifest, checksum)
        action = "created"
    else:
        latest = await assets.latest_skill_version(str(existing["skill_id"]))
        next_no = int(latest["version_no"]) + 1 if latest else 1
        vid = await assets.add_skill_version(str(existing["skill_id"]), next_no, manifest, checksum, user["user_id"])
        row = {"skill_id": str(existing["skill_id"]), "skill_version_id": vid}
        action = "version_updated"
    # action 以本地目录实际动作为准（UI 反映的就是本地行）；SkillHub 侧动作另附参考
    return {**row, "skill_key": skill_key, "display_name": display_name,
            "action": action, "skillhub_action": result.get("action")}


# 29.9 delete「上游已无此 skill」的业务码：命中视作已删、继续本地删。
# 1002 文档语义混杂"不存在/无权限"，但走到上游调用前本地已做 owner 校验（FORBIDDEN 前置），
# 此时 1002 几乎必是"资源不存在"（如 hub 侧已被删——本地僵尸行正需要能删掉）；即使误判成
# "无权限"，skill 仍在上游 → 下轮 sync 的 upsert 会把行拉回来，与上游保持自洽，无永久僵尸。
_DELETE_ABSENT_CODES: tuple[int, ...] = (1002,)


async def delete_skill(user: dict[str, Any], skill_id: str) -> None:
    """删除 Skill：个人级且确实来自 SkillHub 的行先回删上游（29.9 `POST /skills/delete`），再本地软删。

    只软删本地会被 sync_user_skills 在 TTL 后原样拉回（复活）；回删上游后闭环。上游未上线该接口
    （HTTP 404）→ 降级仅本地删（=今日行为）；上游明确拒绝/不可达 → 不本地删（避免制造复活假象）。"""
    row = await assets.get_skill(skill_id)
    if row is None:
        raise ApiError(Err.NOT_FOUND, "Skill 不存在")
    if row["source_type"] == "user" and row["owner_user_id"] != user["user_id"]:
        raise ApiError(Err.FORBIDDEN, "无权删除该 Skill")
    if await agent_teams.asset_in_use("skill", skill_id):
        raise ApiError(Err.ASSET_IN_USE, "该资产仍被 active 配置引用，请先解绑")  # CFG-006
    upstream = "skipped"  # 非 hub 行/平台行：不回删上游
    if row["source_type"] == "user" and row.get("skill_key"):
        from infra.external import skill_hub_client

        latest = await assets.latest_skill_version(str(row["skill_id"]))
        synced_from = ((latest or {}).get("manifest_json") or {}).get("synced_from")
        # 只有真来自 SkillHub 的行（上传/按用户同步）才回删上游；本地手造行（旧 JSON 端点）没有上游对应
        if synced_from in ("upload", "skill_hub_user"):
            try:
                await skill_hub_client.delete_skill(str(row["skill_key"]))
                upstream = "deleted"
            except skill_hub_client.SkillHubError as e:
                if e.kind == "http" and e.status_code == 404:
                    log.warning("skillhub delete 接口不存在（29.9 未上线），降级仅本地删：%s", row["skill_key"])
                    upstream = "endpoint_missing"
                elif e.kind == "biz" and e.biz_code in _DELETE_ABSENT_CODES:
                    upstream = "already_absent"  # 上游已无此 skill → 视作已删，继续本地删
                elif e.kind == "network":
                    raise ApiError(Err.IAM_UPSTREAM, "SkillHub 不可达，删除未执行，请稍后重试",
                                   retryable=True) from None
                else:
                    # 上游明确拒绝（无权/参数错等）：不本地删——否则本地消失、下轮同步复活，用户更困惑
                    raise ApiError(Err.IAM_UPSTREAM, f"SkillHub 拒绝删除：{e.message}") from None
    await assets.delete_skill(skill_id, user["user_id"])
    invalidate_user_skill_sync(user["user_id"])  # 删后强制下轮重拉：节流窗口内不读陈旧集合
    # 审计对齐 mcp.deleted（此前 skill 删除无任何审计）；upstream 让 404 降级/缺席路径可观测
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="skill.deleted", user_id=user["user_id"],
        action="delete",
        payload_redacted={"skill_id": skill_id, "skill_key": row.get("skill_key"), "upstream": upstream},
    )


async def list_mcps(user: dict[str, Any], *, page: int = 1, page_size: int = 20,
                    source_type: str | None = None, q: str | None = None) -> dict[str, Any]:
    """UI 分页读 → {items,total,page,page_size}。口径同 list_skills。"""
    # 真机（OPENOPS_MCPREGISTRY=real）：从插件页列表滤掉占位平台 MCP（endpoint host=mock 的 demo 种子，
    # 如「oModel 查询与恢复」）；mock/默认模式照常展示。与 seed 门控互补（seed 挡新库、这条隐藏老库既有行）。
    hide_placeholder = os.getenv("OPENOPS_MCPREGISTRY", "mock").lower() == "real"
    rows, total = await assets.list_mcps_page(
        user["user_id"], source_type=source_type, q=q,
        limit=page_size, offset=(page - 1) * page_size, hide_placeholder=hide_placeholder,
    )
    items: list[dict[str, Any]] = []
    for r in rows:
        d = row_json(r)
        # endpoint 脱敏（30.5：展示时必须脱敏）
        cfg = d.get("endpoint_config_json") or {}
        if isinstance(cfg, dict) and cfg.get("endpoint"):
            cfg = {**cfg, "endpoint": str(cfg["endpoint"])[:12] + "…"}
        d["endpoint_config_redacted"] = cfg
        d.pop("endpoint_config_json", None)
        # registry 的服务描述存在版本 manifest 里（reconcile ingest 落库）；抽到行顶层供插件页「说明」
        manifest = d.pop("manifest_json", None) or {}  # 内部 manifest 不透前端，只抽展示字段
        if isinstance(manifest, dict):
            d["description"] = manifest.get("description")
            d["category"] = manifest.get("category")
        items.append(d)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def register_mcp(user: dict[str, Any], req: Any) -> dict[str, Any]:
    if req.transport != "http":
        raise ApiError(Err.VALIDATION_FAILED, "V1 仅支持 HTTP MCP")  # MCP-007
    if not (req.endpoint or "").strip():
        raise ApiError(Err.VALIDATION_FAILED, "endpoint 不能为空")  # 空 endpoint 运行时不可达（resolve 会过滤）
    # SSRF：endpoint 是用户任填的 URL，且自定义 MCP 现已默认自动装配到 main、运行时真出站
    egress.check_mcp_egress(req.endpoint)
    return await assets.create_mcp(
        user["user_id"], "user", req.display_name, "http", {"endpoint": req.endpoint}, req.manifest_json
    )


async def delete_mcp(user: dict[str, Any], mcp_id: str) -> None:
    row = await assets.get_mcp(mcp_id)
    if row is None:
        raise ApiError(Err.NOT_FOUND, "MCP 不存在")
    if row["source_type"] == "user" and row["owner_user_id"] != user["user_id"]:
        raise ApiError(Err.FORBIDDEN, "无权删除该 MCP")
    if await agent_teams.asset_in_use("mcp", mcp_id):
        raise ApiError(Err.ASSET_IN_USE, "该资产仍被 active 配置引用，请先解绑")
    tool_names = await assets.tool_names_for_mcp(mcp_id)  # 审计留痕用（catalog 随 asset 保留）
    await assets.delete_mcp(mcp_id, user["user_id"])
    # 级联清理：删后对全部 draft 重跑归一化——被删 server 的引用（复合键失配/裸名解析不到）被摘，
    # 幸存 server 上仍 allowed 的同名裸名保留并升级为幸存家复合键（published 不可变，编辑时自愈）。
    # 解析基于 runtime_annotations，其 catalog 查询已排除已删资产的行，故删除即刻生效。
    scrubbed = await template_service.renormalize_drafts()
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="mcp.deleted", user_id=user["user_id"], action="delete",
        payload_redacted={"mcp_id": mcp_id, "tool_names": tool_names, "templates_scrubbed": scrubbed},
    )


async def bind(user: dict[str, Any], instance_id: str, req: Any) -> dict[str, Any]:
    # MCP 无「绑定」语义：自定义 MCP 按 owner 默认自动装配（解绑走 /mcp-mutes），平台 MCP 由模板
    # main.default_tools 装配。留着 active 的 mcp 绑定行=第二扇门（运行时不读、却会撞 ux_iab_mcp）。
    if req.asset_type == "mcp":
        raise ApiError(Err.VALIDATION_FAILED, "MCP 无需绑定：自定义 MCP 默认自动装配到本 Agent，平台 MCP 由模板装配")
    inst = await agent_teams.get_instance(instance_id)
    if inst is None or inst["owner_user_id"] != user["user_id"]:
        raise ApiError(Err.FORBIDDEN, "无权操作该 AgentTeam")
    # 绑定=派生新配置版本（不可变链：沿用 overlay + 结转已有绑定 + 加新绑定；绑定目标固定 main）
    out = await agent_team_service.derive_config_version(
        inst, user["user_id"], f"bind {req.asset_type}",
        add_binding={"asset_type": req.asset_type, "skill_id": req.skill_id, "skill_version_id": req.skill_version_id,
                     "mcp_id": req.mcp_id, "mcp_version_id": req.mcp_version_id},
    )
    return {"binding_id": out["binding"]["binding_id"],
            "config_version_id": str(out["config_version"]["config_version_id"])}


async def list_instance_bindings(user: dict[str, Any], instance_id: str) -> list[dict[str, Any]]:
    inst = await agent_teams.get_instance(instance_id)
    if inst is None or inst["owner_user_id"] != user["user_id"]:
        raise ApiError(Err.FORBIDDEN, "无权查看该 AgentTeam 绑定")
    rows = await agent_teams.list_binding_details(str(inst["active_config_version_id"]))
    out: list[dict[str, Any]] = []
    for r in rows:
        d = row_json(r)
        d["binding_status"] = d.get("status")  # 绑定行状态（active/muted）——供前端分辨个人 skill 是否被解绑
        if d["asset_type"] == "skill":
            d["display_name"] = d.get("skill_display_name") or "Skill"
            d["version_no"] = d.get("skill_version_no") or 1
            # join-miss（skill_id 有值但连不出资产行）= 资产已软删（缺席墓碑/手动删）——标 deleted
            # 而非 unknown，绑定列表能看出"资产没了"（绑定行是不可变历史，保留展示）
            ghost = d.get("skill_id") and d.get("skill_display_name") is None
            d["asset_status"] = d.get("skill_status") or ("deleted" if ghost else "unknown")
            d["source_type"] = d.get("skill_source_type") or "user"
        else:
            d["display_name"] = d.get("mcp_display_name") or "HTTP MCP"
            d["version_no"] = d.get("mcp_version_no") or 1
            d["asset_status"] = d.get("mcp_status") or "unknown"
            d["source_type"] = d.get("mcp_source_type") or "user"
        out.append(d)
    return out


async def unbind(user: dict[str, Any], binding_id: str) -> dict[str, Any]:
    """解绑=派生新版本（少这一条绑定）；历史版本的绑定行不原地改写（28.7 不可变链）。"""
    b = await agent_teams.get_binding(binding_id)
    if b is None:
        raise ApiError(Err.NOT_FOUND, "绑定不存在")
    inst = await agent_teams.get_instance(str(b["agent_team_instance_id"]))
    if inst is None or inst["owner_user_id"] != user["user_id"]:
        raise ApiError(Err.FORBIDDEN, "无权操作该绑定")
    if str(b["config_version_id"]) != str(inst["active_config_version_id"]):
        raise ApiError(Err.CONFIG_VERSION_INVALID, "绑定不在当前 active 配置上，请刷新后重试")
    out = await agent_team_service.derive_config_version(
        inst, user["user_id"], "unbind", drop_binding_id=binding_id,
    )
    return {"config_version_id": str(out["config_version"]["config_version_id"])}


async def mute_skill(user: dict[str, Any], instance_id: str, req: Any) -> dict[str, Any]:
    """个人 skill「解绑」——mute_asset 的薄包装（b14b35c 契约不变）。"""
    return await mute_asset(user, instance_id, req, kind="skill")


async def mute_asset(user: dict[str, Any], instance_id: str, req: Any, *, kind: str) -> dict[str, Any]:
    """个人资产「解绑」（skill / mcp 同一模型）：个人资产默认自动挂载（按 owner 纳入可执行集），
    解绑=在当前配置版本记一条 muted 绑定行（opt-out），派生结转保留（list_bindings_incl_muted）；
    resolve_available_skills / resolve_available_mcps 据此剔除。重新绑定=对该 mute 行走 unbind（drop 掉）。
    仅限本人 source_type='user' 的资产；平台资产自动装配、不可解绑（403）。
    """
    noun = "Skill" if kind == "skill" else "MCP"
    asset_id = req.skill_id if kind == "skill" else req.mcp_id
    inst = await agent_teams.get_instance(instance_id)
    if inst is None or inst["owner_user_id"] != user["user_id"]:
        raise ApiError(Err.FORBIDDEN, "无权操作该 AgentTeam")
    asset = None
    if asset_id:
        asset = await (assets.get_skill(asset_id) if kind == "skill" else assets.get_mcp(asset_id))
    if asset is None:
        raise ApiError(Err.NOT_FOUND, f"{noun} 不存在")
    if asset["source_type"] != "user" or asset["owner_user_id"] != user["user_id"]:
        raise ApiError(Err.FORBIDDEN, f"仅可解绑本人的自定义 {noun}（平台 {noun} 自动装配、不可解绑）")
    # 同 (config_version, asset_id) 唯一（ux_iab_skill / ux_iab_mcp，**索引不含 status**）⇒ active 与 muted
    # 不能并存：已 muted → 幂等拒绝；存量 active（老模型手动绑的）→ 结转时 drop 掉，否则插 muted 撞唯一索引 500。
    drop_binding_id = None
    for b in await agent_teams.list_binding_details(str(inst["active_config_version_id"])):
        if b.get("asset_type") != kind or str(b.get(f"{kind}_id")) != str(asset_id):
            continue
        if b.get("status") == "muted":
            raise ApiError(Err.VALIDATION_FAILED, f"该 {noun} 已从本 Agent 解绑")
        drop_binding_id = str(b["binding_id"])
    add_binding: dict[str, Any] = {"asset_type": kind, "status": "muted"}
    if kind == "skill":
        add_binding |= {"skill_id": asset_id, "skill_version_id": req.skill_version_id}
    else:
        add_binding |= {"mcp_id": asset_id, "mcp_version_id": req.mcp_version_id}
    out = await agent_team_service.derive_config_version(
        inst, user["user_id"], f"mute {kind}", drop_binding_id=drop_binding_id, add_binding=add_binding,
    )
    return {"binding_id": out["binding"]["binding_id"],
            "config_version_id": str(out["config_version"]["config_version_id"])}
