"""sre_skill_asset(+version) / sre_mcp_asset(+version) 仓储。"""
from __future__ import annotations

import uuid
from typing import Any

from infra.db import exec1, jsonb, q_all, q_one

# 列表 SQL 片段：全量读（list_*，供 resolve_available_skills 等需要完整资产面的热路径）与分页读
# （list_*_page，供 UI）共用同一 FROM/WHERE，避免两处漂移。
# 分页读的两条硬约束：
#  1) source_type/关键字过滤必须在服务端——分页后再做客户端过滤，每页数量必然错乱；
#  2) 排序必须唯一——creation_date 不唯一（reconcile 紧循环批量插入同一时刻多行），只按它排会翻页重/漏，
#     故一律追加主键做 tiebreaker。
_SKILL_COLS = "s.*, v.skill_version_id, v.version_no, v.checksum_sha256, v.manifest_json"
_SKILL_FROM = """
        from sre_skill_asset s
        left join lateral (
          select * from sre_skill_asset_version sv
          where sv.skill_id = s.skill_id and sv.deleted_at is null
          order by sv.version_no desc limit 1
        ) v on true
        where s.deleted_at is null
          and ( (s.source_type='platform' and %(p)s)
             or (s.source_type='user' and s.owner_user_id=%(o)s) )
"""
_SKILL_FILTERS = (
    " and (%(st)s::text is null or s.source_type = %(st)s)"
    " and (%(qlike)s::text is null or s.display_name ilike %(qlike)s or s.skill_key ilike %(qlike)s)"
)


async def list_skills(owner: str | None, include_platform: bool = True) -> list[dict[str, Any]]:
    """全量读（不分页）：resolve_available_skills 等需要**完整**技能面的路径用——分页会掐掉 Agent 的技能。
    UI 列表走 list_skills_page。"""
    return await q_all(
        f"select {_SKILL_COLS}{_SKILL_FROM} order by s.creation_date",
        {"o": owner, "p": include_platform},
    )


async def list_skills_page(
    owner: str | None, *, include_platform: bool = True, source_type: str | None = None,
    q: str | None = None, limit: int = 20, offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """UI 分页读（29.3 §2.2 口径 page/page_size → limit/offset）→ (rows, total)。
    total 单独 count：`count(*) over ()` 在越界空页会退化成 0（拿不到真实总数）。"""
    p: dict[str, Any] = {"o": owner, "p": include_platform, "st": source_type,
                         "qlike": f"%{q}%" if q else None}
    total = int(((await q_one(f"select count(*) as n{_SKILL_FROM}{_SKILL_FILTERS}", p)) or {}).get("n") or 0)
    rows = await q_all(
        f"select {_SKILL_COLS}{_SKILL_FROM}{_SKILL_FILTERS}"
        " order by s.creation_date, s.skill_id limit %(lim)s offset %(off)s",
        {**p, "lim": limit, "off": offset},
    )
    return rows, total


async def create_skill(
    owner: str | None, source_type: str, display_name: str, skill_key: str,
    manifest_json: dict[str, Any], checksum: str,
) -> dict[str, Any]:
    sid, vid = str(uuid.uuid4()), str(uuid.uuid4())
    by = owner or "system"
    await exec1(
        """
        insert into sre_skill_asset
          (skill_id, source, source_type, owner_user_id, display_name, skill_key, status, created_by, last_updated_by)
        values (%(s)s, 'openops', %(st)s, %(o)s, %(d)s, %(k)s, 'active', %(b)s, %(b)s)
        """,
        {"s": sid, "st": source_type, "o": owner, "d": display_name, "k": skill_key, "b": by},
    )
    await exec1(
        """
        insert into sre_skill_asset_version
          (skill_version_id, skill_id, version_no, manifest_json, checksum_sha256, status, created_by, last_updated_by)
        values (%(v)s, %(s)s, 1, %(m)s, %(c)s, 'active', %(b)s, %(b)s)
        """,
        {"v": vid, "s": sid, "m": jsonb(manifest_json), "c": checksum, "b": by},
    )
    return {"skill_id": sid, "skill_version_id": vid}


async def get_skill(skill_id: str) -> dict[str, Any] | None:
    return await q_one("select * from sre_skill_asset where skill_id=%(s)s and deleted_at is null", {"s": skill_id})


async def get_skill_by_key(source_type: str, skill_key: str) -> dict[str, Any] | None:
    return await q_one(
        """
        select * from sre_skill_asset
        where source_type=%(st)s and skill_key=%(k)s and deleted_at is null
        order by creation_date desc limit 1
        """,
        {"st": source_type, "k": skill_key},
    )


async def get_user_skill_by_key(owner: str, skill_key: str) -> dict[str, Any] | None:
    """个人 skill 按 (owner, skill_key) 查重：user skill 无 owner 作用域会串号（两个用户同名 skill
    落到同一行）——upload / 按用户同步都用它，确保同一 (owner, skill_key) 才认作同一 skill。"""
    return await q_one(
        """
        select * from sre_skill_asset
        where source_type='user' and owner_user_id=%(o)s and skill_key=%(k)s and deleted_at is null
        order by creation_date desc limit 1
        """,
        {"o": owner, "k": skill_key},
    )


async def latest_skill_version(skill_id: str) -> dict[str, Any] | None:
    return await q_one(
        """
        select * from sre_skill_asset_version
        where skill_id=%(s)s and deleted_at is null order by version_no desc limit 1
        """,
        {"s": skill_id},
    )


async def add_skill_version(
    skill_id: str, version_no: int, manifest_json: dict[str, Any], checksum: str, by: str
) -> str:
    vid = str(uuid.uuid4())
    await exec1(
        """
        insert into sre_skill_asset_version
          (skill_version_id, skill_id, version_no, manifest_json, checksum_sha256, status, created_by, last_updated_by)
        values (%(v)s, %(s)s, %(n)s, %(m)s, %(c)s, 'active', %(b)s, %(b)s)
        """,
        {"v": vid, "s": skill_id, "n": version_no, "m": jsonb(manifest_json), "c": checksum, "b": by},
    )
    return vid


async def update_skill_version_manifest(skill_version_id: str, manifest_json: dict[str, Any]) -> None:
    """原地刷新缓存版本的 manifest（reconcile 回填 SkillHub 展示元数据 latest_version/category）。
    不改 version_no/checksum，非新版本——skill_asset(+version) 本就是「外部 SkillHub 为事实源」的缓存表。"""
    await exec1(
        "update sre_skill_asset_version set manifest_json=%(m)s, last_update_date=now() where skill_version_id=%(v)s",
        {"m": jsonb(manifest_json), "v": skill_version_id},
    )


async def list_platform_mcps() -> list[dict[str, Any]]:
    """平台 HTTP MCP（含最新版本 id）：对账 tools/list 用。"""
    return await q_all(
        """
        select m.*, v.mcp_version_id
        from sre_mcp_asset m
        join lateral (
          select mcp_version_id from sre_mcp_asset_version mv
          where mv.mcp_id = m.mcp_id and mv.deleted_at is null
          order by mv.version_no desc limit 1
        ) v on true
        where m.source_type='platform' and m.deleted_at is null
        """
    )


async def list_platform_skills() -> list[dict[str, Any]]:
    """全部平台 skill（含最新版本 manifest）：reconcile 缺席墓碑判定用（synced_from 在版本 manifest 里）。

    left join lateral 与 _SKILL_FROM 同口径：无版本行的 skill 也返回（manifest 为 NULL →
    synced_from 判定自然豁免，只跳过不误删）。"""
    return await q_all(
        """
        select s.*, v.manifest_json
        from sre_skill_asset s
        left join lateral (
          select manifest_json from sre_skill_asset_version sv
          where sv.skill_id = s.skill_id and sv.deleted_at is null
          order by sv.version_no desc limit 1
        ) v on true
        where s.source_type='platform' and s.deleted_at is null
        """
    )


async def delete_skill(skill_id: str, by: str) -> int:
    return await exec1(
        "update sre_skill_asset set deleted_at=now(), status='deleted', last_updated_by=%(b)s where skill_id=%(s)s and deleted_at is null",
        {"s": skill_id, "b": by},
    )


# v.manifest_json 必须投影：MCP 的 description 由 reconcile 存在版本表 manifest 里（create_mcp），
# 此前 select 没带它 → MCP 描述从 list 面根本取不到（skill 侧一直有，属两边不对称的缺口）
_MCP_COLS = "m.*, v.mcp_version_id, v.version_no, v.manifest_json"
_MCP_FROM = """
        from sre_mcp_asset m
        left join lateral (
          select * from sre_mcp_asset_version mv
          where mv.mcp_id = m.mcp_id and mv.deleted_at is null
          order by mv.version_no desc limit 1
        ) v on true
        where m.deleted_at is null
          and ( (m.source_type='platform' and %(p)s)
             or (m.source_type='user' and m.owner_user_id=%(o)s) )
"""
_MCP_FILTERS = (
    " and (%(st)s::text is null or m.source_type = %(st)s)"
    " and (%(qlike)s::text is null or m.display_name ilike %(qlike)s)"
    # 真机（OPENOPS_MCPREGISTRY=real）隐藏占位平台 MCP：endpoint host=mock 的 demo 种子资产
    # （如「oModel 查询与恢复」）。seed 门控只挡新库，老库已种的靠这条从插件页列表滤掉——
    # 数据不删、工具装配不变，仅不展示。占位判定对齐 mcp_registry_client.is_placeholder_endpoint（空 / host=mock）。
    " and not (%(hide_ph)s and m.source_type='platform' and ("
    "     coalesce(m.endpoint_config_json->>'endpoint','') = ''"
    "     or m.endpoint_config_json->>'endpoint' ~ '^[a-zA-Z][a-zA-Z0-9+.-]*://mock([:/]|$)'"
    " ))"
)


async def list_mcps(owner: str | None, include_platform: bool = True) -> list[dict[str, Any]]:
    """全量读（不分页）。UI 列表走 list_mcps_page。"""
    return await q_all(
        f"select {_MCP_COLS}{_MCP_FROM} order by m.creation_date",
        {"o": owner, "p": include_platform},
    )


async def list_mcps_page(
    owner: str | None, *, include_platform: bool = True, source_type: str | None = None,
    q: str | None = None, limit: int = 20, offset: int = 0, hide_placeholder: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """UI 分页读 → (rows, total)。口径同 list_skills_page。
    hide_placeholder=True（真机）时滤掉 endpoint host=mock 的占位平台 MCP（见 _MCP_FILTERS）；count 同步生效。"""
    p: dict[str, Any] = {"o": owner, "p": include_platform, "st": source_type,
                         "qlike": f"%{q}%" if q else None, "hide_ph": hide_placeholder}
    total = int(((await q_one(f"select count(*) as n{_MCP_FROM}{_MCP_FILTERS}", p)) or {}).get("n") or 0)
    rows = await q_all(
        f"select {_MCP_COLS}{_MCP_FROM}{_MCP_FILTERS}"
        " order by m.creation_date, m.mcp_id limit %(lim)s offset %(off)s",
        {**p, "lim": limit, "off": offset},
    )
    return rows, total


async def create_mcp(
    owner: str | None, source_type: str, display_name: str, transport: str,
    endpoint_config: dict[str, Any], manifest_json: dict[str, Any],
) -> dict[str, Any]:
    from domain import tool_key

    display_name = tool_key.sanitize_server_name(display_name)  # "::" 会让复合键歧义，入库即归一
    mid, vid = str(uuid.uuid4()), str(uuid.uuid4())
    by = owner or "system"
    await exec1(
        """
        insert into sre_mcp_asset
          (mcp_id, source, source_type, owner_user_id, display_name, transport, endpoint_config_json,
           status, created_by, last_updated_by)
        values (%(m)s, 'openops', %(st)s, %(o)s, %(d)s, %(t)s, %(e)s, 'active', %(b)s, %(b)s)
        """,
        {"m": mid, "st": source_type, "o": owner, "d": display_name, "t": transport,
         "e": jsonb(endpoint_config), "b": by},
    )
    await exec1(
        """
        insert into sre_mcp_asset_version
          (mcp_version_id, mcp_id, version_no, manifest_json, status, created_by, last_updated_by)
        values (%(v)s, %(m)s, 1, %(mf)s, 'active', %(b)s, %(b)s)
        """,
        {"v": vid, "m": mid, "mf": jsonb(manifest_json), "b": by},
    )
    return {"mcp_id": mid, "mcp_version_id": vid}


async def list_platform_mcps_with_manifest() -> list[dict[str, Any]]:
    """全部平台 MCP（含最新版本 id 与 manifest_json）：管理台注册/删除的同名收敛判定用
    （上游 server_id 存在 manifest 里）。

    与 list_platform_mcps 的两点区别，都是刻意的：
    - **left** join lateral：无版本行的资产也返回（manifest 为 NULL → synced_from 判定自然豁免，
      只跳过不误删），口径同 list_platform_skills；
    - 投影 manifest_json。
    不改 list_platform_mcps：reconcile 的 tools 循环依赖它的 inner join 语义（无版本行不该进 catalog 同步）。"""
    return await q_all(
        """
        select m.*, v.mcp_version_id, v.manifest_json
        from sre_mcp_asset m
        left join lateral (
          select mcp_version_id, manifest_json from sre_mcp_asset_version mv
          where mv.mcp_id = m.mcp_id and mv.deleted_at is null
          order by mv.version_no desc limit 1
        ) v on true
        where m.source_type='platform' and m.deleted_at is null
        """
    )


async def update_mcp_endpoint(mcp_id: str, endpoint_config: dict[str, Any], by: str) -> None:
    """原地更新 MCP 的连接配置（管理台重注册改 URL 用）。不改 display_name——它是
    `{display_name}::{tool_name}` 复合键的左半，改了等于让全部模板绑定失配（见 domain/tool_key）。"""
    await exec1(
        "update sre_mcp_asset set endpoint_config_json=%(e)s, last_updated_by=%(b)s,"
        " last_update_date=now() where mcp_id=%(m)s and deleted_at is null",
        {"m": mcp_id, "e": jsonb(endpoint_config), "b": by},
    )


async def update_mcp_version_manifest(mcp_version_id: str, manifest_json: dict[str, Any]) -> None:
    """原地刷新 MCP 版本 manifest（对称 update_skill_version_manifest）。**不新增版本**——
    sre_mcp_tool_catalog 挂在 mcp_version_id 上，保住它就保住了整套工具标注；重注册若派生新版本，
    全部标注失联、工具掉回 fail-closed 直到管理员重标注。"""
    await exec1(
        "update sre_mcp_asset_version set manifest_json=%(m)s, last_update_date=now()"
        " where mcp_version_id=%(v)s",
        {"m": jsonb(manifest_json), "v": mcp_version_id},
    )


async def latest_mcp_version(mcp_id: str) -> dict[str, Any] | None:
    """最新 MCP 版本行（manifest_json 里存着 registry 的 server_id / description）。对齐 latest_skill_version。"""
    return await q_one(
        """
        select * from sre_mcp_asset_version
        where mcp_id=%(m)s and deleted_at is null order by version_no desc limit 1
        """,
        {"m": mcp_id},
    )


async def get_mcp(mcp_id: str) -> dict[str, Any] | None:
    return await q_one("select * from sre_mcp_asset where mcp_id=%(m)s and deleted_at is null", {"m": mcp_id})


async def delete_mcp(mcp_id: str, by: str) -> int:
    return await exec1(
        "update sre_mcp_asset set deleted_at=now(), status='deleted', last_updated_by=%(b)s where mcp_id=%(m)s and deleted_at is null",
        {"m": mcp_id, "b": by},
    )


async def tool_names_for_mcp(mcp_id: str) -> list[str]:
    """该 MCP server 所有版本的 catalog 工具名（删 server 级联清模板绑定用；含软删 catalog 行，清干净）。"""
    rows = await q_all(
        "select distinct c.tool_name from sre_mcp_tool_catalog c "
        "join sre_mcp_asset_version v on v.mcp_version_id = c.mcp_version_id "
        "where v.mcp_id=%(m)s",
        {"m": mcp_id},
    )
    return [str(r["tool_name"]) for r in rows]
