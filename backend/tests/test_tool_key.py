"""domain.tool_key：复合键单点规则（前端 toolBinding.ts 镜像同规则，两处必须一致）。"""
from __future__ import annotations

from domain import tool_key


def test_make_and_parse_roundtrip():
    key = tool_key.make_key("omodel-mcp-server", "query_resource")
    assert key == "omodel-mcp-server::query_resource"
    assert tool_key.is_composite(key)
    assert tool_key.parse_key(key) == ("omodel-mcp-server", "query_resource")


def test_bare_name_passthrough():
    assert not tool_key.is_composite("query_resource")
    assert tool_key.parse_key("query_resource") == (None, "query_resource")


def test_parse_rsplit_keeps_server_segment_whole():
    # display_name 可含单冒号/中文；tool_name 是 MCP 协议名不含 "::" → 从右切保 server 段完整
    assert tool_key.parse_key("oModel 查询与恢复::recover_execute") == ("oModel 查询与恢复", "recover_execute")
    assert tool_key.parse_key("gw:8080::foo") == ("gw:8080", "foo")
    # 病理形（server 名里混进 "::"，入库防呆本应拦下）：右切仍稳定，tool 段不碎
    assert tool_key.parse_key("a::b::foo") == ("a::b", "foo")


def test_sanitize_server_name():
    assert tool_key.sanitize_server_name("normal-server") == "normal-server"
    assert tool_key.sanitize_server_name("bad::server") == "bad:server"
