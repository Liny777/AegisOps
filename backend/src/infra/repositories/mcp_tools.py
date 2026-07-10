"""sre_mcp_tool_catalog / sre_mcp_tool_annotation 仓储。"""
from __future__ import annotations

import uuid
from typing import Any

from infra.db import exec1, jsonb, q_all, q_one


async def upsert_catalog_tool(
    mcp_version_id: str, tool_name: str, description: str, input_schema: dict[str, Any], schema_hash: str
) -> str:
    row = await q_one(
        """
        select tool_catalog_id from sre_mcp_tool_catalog
        where mcp_version_id=%(v)s and tool_name=%(n)s and deleted_at is null
        """,
        {"v": mcp_version_id, "n": tool_name},
    )
    if row:
        return str(row["tool_catalog_id"])
    tcid = str(uuid.uuid4())
    await exec1(
        """
        insert into sre_mcp_tool_catalog
          (tool_catalog_id, mcp_version_id, tool_name, description, input_schema_json, schema_hash,
           discovered_at, status, created_by, last_updated_by)
        values (%(t)s, %(v)s, %(n)s, %(d)s, %(s)s, %(h)s, now(), 'active', 'system', 'system')
        """,
        {"t": tcid, "v": mcp_version_id, "n": tool_name, "d": description, "s": jsonb(input_schema), "h": schema_hash},
    )
    return tcid


async def list_catalog_with_annotation() -> list[dict[str, Any]]:
    return await q_all(
        """
        select c.tool_catalog_id, c.tool_name, c.description, c.schema_hash, c.mcp_version_id,
               m.display_name mcp_display_name,
               a.annotation_id, a.is_approval_required, a.is_secret_required,
               a.scope_mode, a.appid_arg_path, a.status annotation_status, a.blocked_reason
        from sre_mcp_tool_catalog c
        join sre_mcp_asset_version v on v.mcp_version_id = c.mcp_version_id
        join sre_mcp_asset m on m.mcp_id = v.mcp_id
        left join sre_mcp_tool_annotation a
          on a.tool_catalog_id = c.tool_catalog_id and a.deleted_at is null
        where c.deleted_at is null
        order by m.display_name, c.tool_name
        """
    )


async def get_annotation_by_tool_name(tool_name: str) -> dict[str, Any] | None:
    return await q_one(
        """
        select a.*, c.tool_name from sre_mcp_tool_annotation a
        join sre_mcp_tool_catalog c on c.tool_catalog_id = a.tool_catalog_id
        where c.tool_name=%(n)s and a.deleted_at is null and c.deleted_at is null
        limit 1
        """,
        {"n": tool_name},
    )


async def get_runtime_annotation(tool_name: str) -> dict[str, Any] | None:
    """运行时事实（28.7 热更新）：**最新**未删 catalog 行 + 其标注（可能无标注）。

    schema 变化后新行未标注 → annotation_id 为 NULL → Gateway 按 TOOL_NOT_ANNOTATED fail-closed；
    旧行（已 superseded 软删）的历史标注不再生效（标注不继承）。
    """
    return await q_one(
        """
        select c.tool_catalog_id, c.tool_name, c.schema_hash,
               a.annotation_id, a.is_approval_required, a.is_secret_required,
               a.scope_mode, a.appid_arg_path, a.status annotation_status, a.blocked_reason
        from sre_mcp_tool_catalog c
        left join sre_mcp_tool_annotation a
          on a.tool_catalog_id = c.tool_catalog_id and a.deleted_at is null
        where c.tool_name=%(n)s and c.deleted_at is null
        order by c.discovered_at desc limit 1
        """,
        {"n": tool_name},
    )


async def sync_catalog_tool(
    mcp_version_id: str, tool_name: str, description: str, input_schema: dict[str, Any], schema_hash: str
) -> str:
    """对账同步（28.7）：schema_hash 不变→noop；变化→原行更新 schema + **软删其标注**（标注不继承）。

    DDL 对 (mcp_version_id, tool_name) 有绝对唯一索引 → 目录行保持一行、tool_catalog_id 稳定，
    管理员在同一行上重新标注；历史标注行软删保留（审计可回放）。返回 unchanged / created / schema_changed。
    """
    row = await q_one(
        """
        select tool_catalog_id, schema_hash from sre_mcp_tool_catalog
        where mcp_version_id=%(v)s and tool_name=%(n)s and deleted_at is null
        """,
        {"v": mcp_version_id, "n": tool_name},
    )
    if row is None:
        await exec1(
            """
            insert into sre_mcp_tool_catalog
              (tool_catalog_id, mcp_version_id, tool_name, description, input_schema_json, schema_hash,
               discovered_at, status, created_by, last_updated_by)
            values (%(t)s, %(v)s, %(n)s, %(d)s, %(s)s, %(h)s, now(), 'active', 'system', 'system')
            """,
            {"t": str(uuid.uuid4()), "v": mcp_version_id, "n": tool_name, "d": description,
             "s": jsonb(input_schema), "h": schema_hash},
        )
        return "created"
    if row["schema_hash"] == schema_hash:
        return "unchanged"
    # schema 变化：更新目录行 + 软删该行标注（不继承 → 未标注 fail-closed，待管理员重标）
    await exec1(
        """
        update sre_mcp_tool_catalog set description=%(d)s, input_schema_json=%(s)s, schema_hash=%(h)s,
               discovered_at=now(), last_updated_by='system', last_update_date=now()
        where tool_catalog_id=%(t)s
        """,
        {"t": row["tool_catalog_id"], "d": description, "s": jsonb(input_schema), "h": schema_hash},
    )
    await exec1(
        """
        update sre_mcp_tool_annotation set deleted_at=now(), last_updated_by='system', last_update_date=now()
        where tool_catalog_id=%(t)s and deleted_at is null
        """,
        {"t": row["tool_catalog_id"]},
    )
    return "schema_changed"


async def get_tool_by_name(tool_name: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from sre_mcp_tool_catalog where tool_name=%(n)s and deleted_at is null limit 1", {"n": tool_name}
    )


async def save_annotation(
    tool_catalog_id: str, is_approval_required: bool, is_secret_required: bool,
    scope_mode: str, appid_arg_path: str | None, status: str, blocked_reason: str | None, by: str,
) -> None:
    row = await q_one(
        "select annotation_id from sre_mcp_tool_annotation where tool_catalog_id=%(t)s and deleted_at is null",
        {"t": tool_catalog_id},
    )
    if row:
        await exec1(
            """
            update sre_mcp_tool_annotation
            set is_approval_required=%(a)s, is_secret_required=%(s)s, scope_mode=%(m)s,
                appid_arg_path=%(p)s, status=%(st)s, blocked_reason=%(r)s,
                annotated_by=%(b)s, annotated_at=now(), last_updated_by=%(b)s, last_update_date=now()
            where annotation_id=%(id)s
            """,
            {"id": row["annotation_id"], "a": is_approval_required, "s": is_secret_required,
             "m": scope_mode, "p": appid_arg_path, "st": status, "r": blocked_reason, "b": by},
        )
    else:
        await exec1(
            """
            insert into sre_mcp_tool_annotation
              (annotation_id, tool_catalog_id, is_approval_required, is_secret_required, scope_mode,
               appid_arg_path, status, blocked_reason, annotated_by, annotated_at, created_by, last_updated_by)
            values (%(id)s, %(t)s, %(a)s, %(s)s, %(m)s, %(p)s, %(st)s, %(r)s, %(b)s, now(), %(b)s, %(b)s)
            """,
            {"id": str(uuid.uuid4()), "t": tool_catalog_id, "a": is_approval_required, "s": is_secret_required,
             "m": scope_mode, "p": appid_arg_path, "st": status, "r": blocked_reason, "b": by},
        )
