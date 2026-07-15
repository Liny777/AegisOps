"""sre_agent_team_instance / sre_agent_team_config_version / sre_instance_asset_binding 仓储。"""
from __future__ import annotations

import uuid
from typing import Any

from infra.db import exec1, jsonb, q_all, q_one


async def name_exists(owner: str, name: str, exclude_instance_id: str | None = None) -> bool:
    """同 owner 存活实例同名判定；编辑改名传 exclude_instance_id 排除自身（DDL 有部分唯一索引兜底）。"""
    sql = """
        select 1 ok from sre_agent_team_instance
        where owner_user_id=%(o)s and instance_name=%(n)s and deleted_at is null
    """
    params: dict[str, Any] = {"o": owner, "n": name}
    if exclude_instance_id:
        sql += " and agent_team_instance_id <> %(x)s"
        params["x"] = exclude_instance_id
    return (await q_one(sql, params)) is not None


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
        insert into sre_agent_team_instance
          (agent_team_instance_id, owner_user_id, template_id, template_version_id, instance_name,
           workspace_id, scope_revision, active_config_version_id, status, created_by, last_updated_by)
        values (%(i)s, %(o)s, %(t)s, %(tv)s, %(n)s, %(w)s, %(sr)s, %(cv)s, 'active', %(o)s, %(o)s)
        """,
        {"i": iid, "o": owner, "t": template_id, "tv": template_version_id, "n": name,
         "w": workspace_id, "sr": scope_revision, "cv": cvid},
    )
    await exec1(
        """
        insert into sre_agent_team_config_version
          (config_version_id, agent_team_instance_id, template_version_id, version_no, schema_version,
           overlay_json, status, created_by, last_updated_by, change_reason)
        values (%(cv)s, %(i)s, %(tv)s, 1, 'v1', %(ov)s, 'active', %(o)s, %(o)s, 'initial')
        """,
        {"cv": cvid, "i": iid, "tv": template_version_id, "ov": jsonb(overlay_json), "o": owner},
    )
    return (await get_instance(iid))  # type: ignore[return-value]


async def get_instance(instance_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from sre_agent_team_instance where agent_team_instance_id=%(i)s and deleted_at is null",
        {"i": instance_id},
    )


async def list_by_owner(owner: str) -> list[dict[str, Any]]:
    return await q_all(
        """
        select * from sre_agent_team_instance
        where owner_user_id=%(o)s and deleted_at is null
        order by creation_date
        """,
        {"o": owner},
    )


async def set_status(instance_id: str, status: str, by: str) -> int:
    return await exec1(
        """
        update sre_agent_team_instance set status=%(s)s, last_updated_by=%(b)s, last_update_date=now()
        where agent_team_instance_id=%(i)s and deleted_at is null
        """,
        {"i": instance_id, "s": status, "b": by},
    )


async def update_template_version(instance_id: str, template_version_id: str, by: str) -> int:
    """模板升级派生后回写实例的模板版本指针（28.7）。"""
    return await exec1(
        """
        update sre_agent_team_instance set template_version_id=%(tv)s, last_updated_by=%(b)s, last_update_date=now()
        where agent_team_instance_id=%(i)s and deleted_at is null
        """,
        {"i": instance_id, "tv": template_version_id, "b": by},
    )


async def set_name(instance_id: str, name: str, by: str) -> int:
    """编辑改名（唯一性由 service 预检 + DDL 部分唯一索引兜底）。"""
    return await exec1(
        """
        update sre_agent_team_instance set instance_name=%(n)s, last_updated_by=%(b)s, last_update_date=now()
        where agent_team_instance_id=%(i)s and deleted_at is null
        """,
        {"i": instance_id, "n": name, "b": by},
    )


async def update_workspace(instance_id: str, workspace_id: str, scope_revision: str, by: str) -> int:
    """编辑换系统范围：workspace_id 与 scope_revision 快照一条 UPDATE 同步落库。"""
    return await exec1(
        """
        update sre_agent_team_instance set workspace_id=%(w)s, scope_revision=%(sr)s,
               last_updated_by=%(b)s, last_update_date=now()
        where agent_team_instance_id=%(i)s and deleted_at is null
        """,
        {"i": instance_id, "w": workspace_id, "sr": scope_revision, "b": by},
    )


async def update_scope_revision(instance_id: str, scope_revision: str, by: str) -> int:
    """回写实例 scope_revision（oModel 返回新版本时；28.6 scope.updated）。"""
    return await exec1(
        """
        update sre_agent_team_instance set scope_revision=%(sr)s, last_updated_by=%(b)s, last_update_date=now()
        where agent_team_instance_id=%(i)s and deleted_at is null
        """,
        {"i": instance_id, "sr": scope_revision, "b": by},
    )


async def soft_delete(instance_id: str, by: str) -> int:
    return await exec1(
        """
        update sre_agent_team_instance set deleted_at=now(), last_updated_by=%(b)s, last_update_date=now()
        where agent_team_instance_id=%(i)s and deleted_at is null
        """,
        {"i": instance_id, "b": by},
    )


async def get_config_version(config_version_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from sre_agent_team_config_version where config_version_id=%(c)s and deleted_at is null",
        {"c": config_version_id},
    )


async def list_config_versions(instance_id: str) -> list[dict[str, Any]]:
    return await q_all(
        """
        select * from sre_agent_team_config_version
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
        select coalesce(max(version_no),0) v from sre_agent_team_config_version
        where agent_team_instance_id=%(i)s
        """,
        {"i": instance_id},
    )
    cvid = str(uuid.uuid4())
    await exec1(
        """
        update sre_agent_team_config_version set status='archived', last_update_date=now(), last_updated_by=%(b)s
        where agent_team_instance_id=%(i)s and status='active'
        """,
        {"i": instance_id, "b": by},
    )
    await exec1(
        """
        insert into sre_agent_team_config_version
          (config_version_id, agent_team_instance_id, template_version_id, version_no, schema_version,
           overlay_json, status, created_by, last_updated_by, change_reason)
        values (%(cv)s, %(i)s, %(tv)s, %(no)s, 'v1', %(ov)s, 'active', %(b)s, %(b)s, %(r)s)
        """,
        {"cv": cvid, "i": instance_id, "tv": template_version_id, "no": (last["v"] if last else 0) + 1,
         "ov": jsonb(overlay_json), "b": by, "r": reason},
    )
    await exec1(
        """
        update sre_agent_team_instance set active_config_version_id=%(cv)s, last_update_date=now(), last_updated_by=%(b)s
        where agent_team_instance_id=%(i)s
        """,
        {"cv": cvid, "i": instance_id, "b": by},
    )
    return (await get_config_version(cvid))  # type: ignore[return-value]


async def create_binding(
    instance_id: str, config_version_id: str, owner: str, asset_type: str,
    skill_id: str | None, skill_version_id: str | None, mcp_id: str | None, mcp_version_id: str | None,
    status: str = "active",
) -> dict[str, Any]:
    bid = str(uuid.uuid4())
    await exec1(
        """
        insert into sre_instance_asset_binding
          (binding_id, agent_team_instance_id, config_version_id, owner_user_id, agent_key, asset_type,
           skill_id, skill_version_id, mcp_id, mcp_version_id, status, created_by, last_updated_by)
        values (%(b)s, %(i)s, %(cv)s, %(o)s, 'main', %(t)s, %(s)s, %(sv)s, %(m)s, %(mv)s, %(st)s, %(o)s, %(o)s)
        """,
        {"b": bid, "i": instance_id, "cv": config_version_id, "o": owner, "t": asset_type,
         "s": skill_id, "sv": skill_version_id, "m": mcp_id, "mv": mcp_version_id, "st": status},
    )
    return {"binding_id": bid}


async def get_binding(binding_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from sre_instance_asset_binding where binding_id=%(b)s and deleted_at is null", {"b": binding_id}
    )


async def list_bindings(config_version_id: str) -> list[dict[str, Any]]:
    return await q_all(
        """
        select * from sre_instance_asset_binding
        where config_version_id=%(cv)s and deleted_at is null and status='active'
        """,
        {"cv": config_version_id},
    )


async def list_bindings_incl_muted(config_version_id: str) -> list[dict[str, Any]]:
    """结转用：含 muted（个人 skill 的「解绑」标记）——派生新配置版本必须保留 mute，
    否则解绑会被下一次派生（save_config / 模板升级 / 再绑定）冲掉、个人 skill 静默复现。"""
    return await q_all(
        """
        select * from sre_instance_asset_binding
        where config_version_id=%(cv)s and deleted_at is null and status in ('active', 'muted')
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
          s.skill_key as skill_key,
          sv.version_no as skill_version_no,
          s.status as skill_status,
          s.source_type as skill_source_type,
          m.display_name as mcp_display_name,
          mv.version_no as mcp_version_no,
          m.status as mcp_status,
          m.source_type as mcp_source_type,
          m.transport as mcp_transport
        from sre_instance_asset_binding b
        left join sre_skill_asset s on s.skill_id = b.skill_id and s.deleted_at is null
        left join sre_skill_asset_version sv on sv.skill_version_id = b.skill_version_id and sv.deleted_at is null
        left join sre_mcp_asset m on m.mcp_id = b.mcp_id and m.deleted_at is null
        left join sre_mcp_asset_version mv on mv.mcp_version_id = b.mcp_version_id and mv.deleted_at is null
        where b.config_version_id=%(cv)s and b.deleted_at is null and b.status in ('active', 'muted')
        order by b.creation_date
        """,
        {"cv": config_version_id},
    )


async def delete_binding(binding_id: str, by: str) -> int:
    return await exec1(
        """
        update sre_instance_asset_binding set deleted_at=now(), status='deleted',
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
        from sre_instance_asset_binding b
        join sre_agent_team_config_version cv on cv.config_version_id = b.config_version_id
        where b.{col}=%(a)s and b.deleted_at is null and b.status='active' and cv.status='active'
        limit 1
        """,
        {"a": asset_id},
    )
    return row is not None
