"""工具身份复合键（server+tool）：跨 MCP server 同名工具的唯一引用（2026-07-23 同名冲突根治）。

数据层唯一约束只到 (mcp_version_id, tool_name)（ux_mcp_tool_catalog_name），跨 server 同名合法；
但模板绑定/运行时标注曾把裸 tool_name 当全局唯一身份 → 同名互踩（标注「后者赢」、模板编辑勾选
跨 server 联动）。本模块是复合键的单点定义：`"{mcp_display_name}::{tool_name}"`。

选 display_name 而非 tool_catalog_id：display_name 是 reconcile create-if-missing 的资产身份锚
（删 server 重注册后引用不断），且人可读（content_json diff / 审计 / 前端残留 chip 直接可辨）；
UUID 两者皆无（删重建即断、seed 无法静态写死）。已知代价：管理员改 display_name 会断引用，
表现同「工具下线」（scrub 摘除 + fail-closed，不静默错绑）。

**复合键 ≠ toolkit 注册名**：注册名是 LLM 面（OpenAI 兼容 [A-Za-z0-9_-]{1,64}，容不下 "::" 与
中文），冲突时由 _build_toolkit 的分配器生成 `{slug(server)}__{tool}`；两者经标注元数据关联，
不靠解析注册名。前端镜像实现：frontend/src/admin/toolBinding.ts —— 两处规则必须保持一致。
"""
from __future__ import annotations

SEP = "::"


def make_key(server: str, tool: str) -> str:
    return f"{server}{SEP}{tool}"


def is_composite(ref: str) -> bool:
    return SEP in ref


def parse_key(ref: str) -> tuple[str | None, str]:
    """复合键 → (server, tool)；裸名 → (None, 原名)。

    从右切（rsplit）：tool_name 是 MCP 协议名（[A-Za-z0-9_-]，不含 "::"），
    display_name 可含单冒号/中文——右切保证 server 段完整。
    """
    if SEP not in ref:
        return None, ref
    srv, tool = ref.rsplit(SEP, 1)
    return srv, tool


def sanitize_server_name(name: str) -> str:
    """server 名防呆：display_name 含 "::" 会让复合键歧义（parse 右切仍稳，但与真键撞形），
    入库前归一为单冒号。资产创建的唯一入口（assets.create_mcp）调用。"""
    return name.replace(SEP, ":")
