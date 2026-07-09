"""agent_team_instance / agent_team_config_version / instance_asset_binding 仓储。"""
from __future__ import annotations

import uuid
from typing import Any

from infra.db import exec1, jsonb, q_all, q_one


async def name_exists(owner: str, name: str) -> bool:
    row = await q_one(
        """
        select 1 ok from agent_team_instance
        where owner_user_id=%(o)s and instance_name=%(n)s and deleted_at is null
        """,
        {"o": owner, "n": name},
    )
    return row is not None


async def create_instance(
    owner: str,
    template_id: str,
    template_version_id: str,
    name: str,
    workspace_id: str,
    scope_revision: str,
    overlay_json: dict[str, Any],
) -> dict[str, Any]:
    iid, cvid = str(uuid.uuid4()), str(uuid.uuid4())
    await exec1(
        """
        insert into agent_team_instance
          (agent_team_instance_id, owner_user_id, template_id, template_version_id, instance_name,
           workspace_id, scope_revision, active_config_version_id, status, created_by, last_updated_by)
        values (%(i)s, %(o)s, %(t)s, %(tv)s, %(n)s, %(w)s, %(sr)s, %(cv)s, 'active', %(o)s, %(o)s)
        """,
        {"i": iid, "o": owner, "t": template_id, "tv": template_version_id, "n": name,
         "w": workspace_id, "sr": scope_revision, "cv": cvid},
    )
    await exec1(
        """
        insert into agent_team_config_version
          (config_version_id, agent_team_instance_id, template_version_id, version_no, schema_version,
           overlay_json, status, created_by, last_updated_by, change_reason)
        values (%(cv)s, %(i)s, %(tv)s, 1, 'v1', %(ov)s, 'active', %(o)s, %(o)s, 'initial')
        """,
        {"cv": cvid, "i": iid, "tv": template_version_id, "ov": jsonb(overlay_json), "o": owner},
    )
    return (await get_instance(iid))  # type: ignore[return-value]


async def get_instance(instance_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from agent_team_instance where agent_team_instance_id=%(i)s and deleted_at is null",
        {"i": instance_id},
    )


async def list_by_owner(owner: str) -> list[dict[str, Any]]:
    return await q_all(
        """
        select * from agent_team_instance
        where owner_user_id=%(o)s and deleted_at is null
        order by creation_date
        """,
        {"o": owner},
    )


async def set_status(instance_id: str, status: str, by: str) -> int:
    return await exec1(
        """
        update agent_team_instance set status=%(s)s, last_updated_by=%(b)s, last_update_date=now()
        where agent_team_instance_id=%(i)s and deleted_at is null
        """,
        {"i": instance_id, "s": status, "b": by},
    )


async def update_scope_revision(instance_id: str, scope_revision: str, by: str) -> int:
    """回写实例 scope_revision（oModel 返回新版本时；28.6 scope.updated）。"""
    return await exec1(
        """
        update agent_team_instance set scope_revision=%(sr)s, last_updated_by=%(b)s, last_update_date=now()
        where agent_team_instance_id=%(i)s and deleted_at is null
        """,
        {"i": instance_id, "sr": scope_revision, "b": by},
    )


async def soft_delete(instance_id: str, by: str) -> int:
    return await exec1(
        """
        update agent_team_instance set deleted_at=now(), last_updated_by=%(b)s, last_update_date=now()
        where agent_team_instance_id=%(i)s and deleted_at is null
        """,
        {"i": instance_id, "b": by},
    )


async def get_config_version(config_version_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from agent_team_config_version where config_version_id=%(c)s and deleted_at is null",
        {"c": config_version_id},
    )


async def list_config_versions(instance_id: str) -> list[dict[str, Any]]:
    return await q_all(
        """
        select * from agent_team_config_version
        where agent_team_instance_id=%(i)s and deleted_at is null
        order by version_no desc
        """,
        {"i": instance_id},
    )


async def create_config_version(
    instance_id: str, template_version_id: str, overlay_json: dict[str, Any], by: str, reason: str
) -> dict[str, Any]:
    """归档旧 active → 新 active（不可变版本链）。"""
    last = await q_one(
        """
        select coalesce(max(version_no),0) v from agent_team_config_version
        where agent_team_instance_id=%(i)s
        """,
        {"i": instance_id},
    )
    cvid = str(uuid.uuid4())
    await exec1(
        """
        update agent_team_config_version set status='archived', last_update_date=now(), last_updated_by=%(b)s
        where agent_team_instance_id=%(i)s and status='active'
        """,
        {"i": instance_id, "b": by},
    )
    await exec1(
        """
        insert into agent_team_config_version
          (config_version_id, agent_team_instance_id, template_version_id, version_no, schema_version,
           overlay_json, status, created_by, last_updated_by, change_reason)
        values (%(cv)s, %(i)s, %(tv)s, %(no)s, 'v1', %(ov)s, 'active', %(b)s, %(b)s, %(r)s)
        """,
        {"cv": cvid, "i": instance_id, "tv": template_version_id, "no": (last["v"] if last else 0) + 1,
         "ov": jsonb(overlay_json), "b": by, "r": reason},
    )
    await exec1(
        """
        update agent_team_instance set active_config_version_id=%(cv)s, last_update_date=now(), last_updated_by=%(b)s
        where agent_team_instance_id=%(i)s
        """,
        {"cv": cvid, "i": instance_id, "b": by},
    )
    return (await get_config_version(cvid))  # type: ignore[return-value]


async def create_binding(
    instance_id: str, config_version_id: str, owner: str, asset_type: str,
    skill_id: str | None, skill_version_id: str | None, mcp_id: str | None, mcp_version_id: str | None,
) -> dict[str, Any]:
    bid = str(uuid.uuid4())
    await exec1(
        """
        insert into instance_asset_binding
          (binding_id, agent_team_instance_id, config_version_id, owner_user_id, agent_key, asset_type,
           skill_id, skill_version_id, mcp_id, mcp_version_id, status, created_by, last_updated_by)
        values (%(b)s, %(i)s, %(cv)s, %(o)s, 'main', %(t)s, %(s)s, %(sv)s, %(m)s, %(mv)s, 'active', %(o)s, %(o)s)
        """,
        {"b": bid, "i": instance_id, "cv": config_version_id, "o": owner, "t": asset_type,
         "s": skill_id, "sv": skill_version_id, "m": mcp_id, "mv": mcp_version_id},
    )
    return {"binding_id": bid}


async def get_binding(binding_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from instance_asset_binding where binding_id=%(b)s and deleted_at is null", {"b": binding_id}
    )


async def list_bindings(config_version_id: str) -> list[dict[str, Any]]:
    return await q_all(
        """
        select * from instance_asset_binding
        where config_version_id=%(cv)s and deleted_at is null and status='active'
        """,
        {"cv": config_version_id},
    )


async def list_binding_details(config_version_id: str) -> list[dict[str, Any]]:
    """当前配置版本绑定详情，给实例设置页展示使用。"""
    return await q_all(
        """
        select
          b.*,
          s.display_name as skill_display_name,
          sv.version_no as skill_version_no,
          s.status as skill_status,
          s.source_type as skill_source_type,
          m.display_name as mcp_display_name,
          mv.version_no as mcp_version_no,
          m.status as mcp_status,
          m.source_type as mcp_source_type,
          m.transport as mcp_transport
        from instance_asset_binding b
        left join skill_asset s on s.skill_id = b.skill_id and s.deleted_at is null
        left join skill_asset_version sv on sv.skill_version_id = b.skill_version_id and sv.deleted_at is null
        left join mcp_asset m on m.mcp_id = b.mcp_id and m.deleted_at is null
        left join mcp_asset_version mv on mv.mcp_version_id = b.mcp_version_id and mv.deleted_at is null
        where b.config_version_id=%(cv)s and b.deleted_at is null and b.status='active'
        order by b.creation_date
        """,
        {"cv": config_version_id},
    )


async def delete_binding(binding_id: str, by: str) -> int:
    return await exec1(
        """
        update instance_asset_binding set deleted_at=now(), status='deleted',
               last_updated_by=%(b)s, last_update_date=now()
        where binding_id=%(id)s and deleted_at is null
        """,
        {"id": binding_id, "b": by},
    )


async def asset_in_use(asset_type: str, asset_id: str) -> bool:
    """资产是否仍被某 active 配置引用（ASSET_IN_USE 判定）。"""
    col = "skill_id" if asset_type == "skill" else "mcp_id"
    row = await q_one(
        f"""
        select 1 ok
        from instance_asset_binding b
        join agent_team_config_version cv on cv.config_version_id = b.config_version_id
        where b.{col}=%(a)s and b.deleted_at is null and b.status='active' and cv.status='active'
        limit 1
        """,
        {"a": asset_id},
    )
    return row is not None
