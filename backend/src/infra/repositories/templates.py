"""agent_team_template / _version 仓储。"""
from __future__ import annotations

import uuid
from typing import Any

from infra.db import exec1, jsonb, q_all, q_one


async def list_templates(status: str | None = None) -> list[dict[str, Any]]:
    where = "deleted_at is null" + (" and status=%(s)s" if status else "")
    return await q_all(f"select * from agent_team_template where {where} order by creation_date", {"s": status})


async def get_version(template_version_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from agent_team_template_version where template_version_id=%(v)s and deleted_at is null",
        {"v": template_version_id},
    )


async def get_template(template_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from agent_team_template where template_id=%(t)s and deleted_at is null", {"t": template_id}
    )


async def create_template_with_version(
    template_key: str, display_name: str, description: str, content_json: dict[str, Any], by: str
) -> tuple[str, str]:
    tid, vid = str(uuid.uuid4()), str(uuid.uuid4())
    await exec1(
        """
        insert into agent_team_template
          (template_id, template_key, display_name, description, status, active_template_version_id, created_by, last_updated_by)
        values (%(t)s, %(k)s, %(d)s, %(desc)s, 'active', %(v)s, %(b)s, %(b)s)
        """,
        {"t": tid, "k": template_key, "d": display_name, "desc": description, "v": vid, "b": by},
    )
    await exec1(
        """
        insert into agent_team_template_version
          (template_version_id, template_id, version_no, schema_version, content_json, status,
           published_by, published_at, created_by, last_updated_by)
        values (%(v)s, %(t)s, 1, 'v1', %(c)s, 'active', %(b)s, now(), %(b)s, %(b)s)
        """,
        {"v": vid, "t": tid, "c": jsonb(content_json), "b": by},
    )
    return tid, vid
