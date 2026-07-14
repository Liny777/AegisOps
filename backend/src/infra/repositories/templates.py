"""sre_agent_team_template / _version 仓储。"""
from __future__ import annotations

import uuid
from typing import Any

from infra.db import exec1, jsonb, q_all, q_one


async def list_templates(status: str | None = None) -> list[dict[str, Any]]:
    where = "deleted_at is null" + (" and status=%(s)s" if status else "")
    return await q_all(f"select * from sre_agent_team_template where {where} order by creation_date", {"s": status})


async def get_version(template_version_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from sre_agent_team_tpl_version where template_version_id=%(v)s and deleted_at is null",
        {"v": template_version_id},
    )


async def get_template(template_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from sre_agent_team_template where template_id=%(t)s and deleted_at is null", {"t": template_id}
    )


async def get_draft(template_id: str) -> dict[str, Any] | None:
    """当前草稿版本（每模板同时至多一个 draft，save 为 upsert 语义）。"""
    return await q_one(
        """
        select * from sre_agent_team_tpl_version
        where template_id=%(t)s and status='draft' and deleted_at is null
        order by version_no desc limit 1
        """,
        {"t": template_id},
    )


async def save_draft(template_id: str, content_json: dict[str, Any], by: str) -> dict[str, Any]:
    """保存草稿：已有 draft 则更新内容（草稿可变），否则以 max(version_no)+1 新建。"""
    draft = await get_draft(template_id)
    if draft is not None:
        await exec1(
            """
            update sre_agent_team_tpl_version
            set content_json=%(c)s, last_updated_by=%(b)s, last_update_date=now()
            where template_version_id=%(v)s
            """,
            {"v": str(draft["template_version_id"]), "c": jsonb(content_json), "b": by},
        )
        return (await get_version(str(draft["template_version_id"])))  # type: ignore[return-value]
    last = await q_one(
        "select coalesce(max(version_no),0) v from sre_agent_team_tpl_version where template_id=%(t)s",
        {"t": template_id},
    )
    vid = str(uuid.uuid4())
    await exec1(
        """
        insert into sre_agent_team_tpl_version
          (template_version_id, template_id, version_no, schema_version, content_json, status, created_by, last_updated_by)
        values (%(v)s, %(t)s, %(no)s, 'v1', %(c)s, 'draft', %(b)s, %(b)s)
        """,
        {"v": vid, "t": template_id, "no": (last["v"] if last else 0) + 1, "c": jsonb(content_json), "b": by},
    )
    return (await get_version(vid))  # type: ignore[return-value]


async def publish_version(template_version_id: str, by: str) -> dict[str, Any]:
    """发布：draft→active（不可变），旧 active→archived，回写 template.active_template_version_id。"""
    ver = await get_version(template_version_id)
    assert ver is not None
    tid = str(ver["template_id"])
    await exec1(
        """
        update sre_agent_team_tpl_version set status='archived', last_updated_by=%(b)s, last_update_date=now()
        where template_id=%(t)s and status='active'
        """,
        {"t": tid, "b": by},
    )
    await exec1(
        """
        update sre_agent_team_tpl_version
        set status='active', published_by=%(b)s, published_at=now(), last_updated_by=%(b)s, last_update_date=now()
        where template_version_id=%(v)s
        """,
        {"v": template_version_id, "b": by},
    )
    await exec1(
        """
        update sre_agent_team_template set active_template_version_id=%(v)s, last_updated_by=%(b)s, last_update_date=now()
        where template_id=%(t)s
        """,
        {"v": template_version_id, "t": tid, "b": by},
    )
    return (await get_version(template_version_id))  # type: ignore[return-value]


async def disable_version(template_version_id: str, by: str) -> None:
    """禁用版本；若禁的是 active，模板暂无可实例化版本（active_template_version_id 置空）。"""
    ver = await get_version(template_version_id)
    assert ver is not None
    await exec1(
        """
        update sre_agent_team_tpl_version set status='disabled', last_updated_by=%(b)s, last_update_date=now()
        where template_version_id=%(v)s
        """,
        {"v": template_version_id, "b": by},
    )
    await exec1(
        """
        update sre_agent_team_template set active_template_version_id=null, last_updated_by=%(b)s, last_update_date=now()
        where template_id=%(t)s and active_template_version_id=%(v)s
        """,
        {"t": str(ver["template_id"]), "v": template_version_id, "b": by},
    )


async def list_versions(template_id: str) -> list[dict[str, Any]]:
    return await q_all(
        """
        select * from sre_agent_team_tpl_version
        where template_id=%(t)s and deleted_at is null order by version_no desc
        """,
        {"t": template_id},
    )


async def create_template_with_version(
    template_key: str, display_name: str, description: str, content_json: dict[str, Any], by: str
) -> tuple[str, str]:
    tid, vid = str(uuid.uuid4()), str(uuid.uuid4())
    await exec1(
        """
        insert into sre_agent_team_template
          (template_id, template_key, display_name, description, status, active_template_version_id, created_by, last_updated_by)
        values (%(t)s, %(k)s, %(d)s, %(desc)s, 'active', %(v)s, %(b)s, %(b)s)
        """,
        {"t": tid, "k": template_key, "d": display_name, "desc": description, "v": vid, "b": by},
    )
    await exec1(
        """
        insert into sre_agent_team_tpl_version
          (template_version_id, template_id, version_no, schema_version, content_json, status,
           published_by, published_at, created_by, last_updated_by)
        values (%(v)s, %(t)s, 1, 'v1', %(c)s, 'active', %(b)s, now(), %(b)s, %(b)s)
        """,
        {"v": vid, "t": tid, "c": jsonb(content_json), "b": by},
    )
    return tid, vid
