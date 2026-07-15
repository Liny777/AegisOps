"""sre_skill_asset(+version) / sre_mcp_asset(+version) 仓储。"""
from __future__ import annotations

import uuid
from typing import Any

from infra.db import exec1, jsonb, q_all, q_one


async def list_skills(owner: str | None, include_platform: bool = True) -> list[dict[str, Any]]:
    return await q_all(
        """
        select s.*, v.skill_version_id, v.version_no, v.checksum_sha256, v.manifest_json
        from sre_skill_asset s
        left join lateral (
          select * from sre_skill_asset_version sv
          where sv.skill_id = s.skill_id and sv.deleted_at is null
          order by sv.version_no desc limit 1
        ) v on true
        where s.deleted_at is null
          and ( (s.source_type='platform' and %(p)s)
             or (s.source_type='user' and s.owner_user_id=%(o)s) )
        order by s.creation_date
        """,
        {"o": owner, "p": include_platform},
    )


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


async def delete_skill(skill_id: str, by: str) -> int:
    return await exec1(
        "update sre_skill_asset set deleted_at=now(), status='deleted', last_updated_by=%(b)s where skill_id=%(s)s and deleted_at is null",
        {"s": skill_id, "b": by},
    )


async def list_mcps(owner: str | None, include_platform: bool = True) -> list[dict[str, Any]]:
    return await q_all(
        """
        select m.*, v.mcp_version_id, v.version_no
        from sre_mcp_asset m
        left join lateral (
          select * from sre_mcp_asset_version mv
          where mv.mcp_id = m.mcp_id and mv.deleted_at is null
          order by mv.version_no desc limit 1
        ) v on true
        where m.deleted_at is null
          and ( (m.source_type='platform' and %(p)s)
             or (m.source_type='user' and m.owner_user_id=%(o)s) )
        order by m.creation_date
        """,
        {"o": owner, "p": include_platform},
    )


async def create_mcp(
    owner: str | None, source_type: str, display_name: str, transport: str,
    endpoint_config: dict[str, Any], manifest_json: dict[str, Any],
) -> dict[str, Any]:
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
