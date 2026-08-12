"""出站 IAM 服务态鉴权头（infra.iam_headers）：j2c_utils 仅内网存在，本仓库以
sys.modules 塞 fake 模块模拟「内网态」。三态语义 + 两个消费点（omodel/console）注入验证。
"""
from __future__ import annotations

import logging
import sys
from types import SimpleNamespace

import pytest

from infra import iam_headers


@pytest.fixture(autouse=True)
def _reset_flags():
    """限频 flag 是 module 级——逐测重置，保证一次性日志断言确定。"""
    iam_headers._warned_missing = False
    iam_headers._warned_failed = False
    yield
    iam_headers._warned_missing = False
    iam_headers._warned_failed = False


def _fake_j2c(monkeypatch, auth: str = "Bearer IAM-TOKEN") -> None:
    monkeypatch.setitem(sys.modules, "infra.j2c_utils", SimpleNamespace(
        _get_iam_headers=lambda: {"Content-Type": "application/json; charset=UTF-8",
                                  "Authorization": auth}))


def test_missing_module_returns_empty():
    """本仓库天然态：j2c_utils 不存在 → 空 dict（绝不发空值头），行为零变化。"""
    assert "infra.j2c_utils" not in sys.modules
    assert iam_headers.iam_auth_headers() == {}


def test_fake_module_passes_only_authorization(monkeypatch):
    """内网态：只透传 Authorization——j2c 的 Content-Type 丢弃（httpx 自管，防污染 GET/握手）。"""
    _fake_j2c(monkeypatch)
    assert iam_headers.iam_auth_headers() == {"Authorization": "Bearer IAM-TOKEN"}


def test_token_failure_degrades_with_single_warning(monkeypatch, caplog):
    """取 token 抛异常 → 空 dict 不炸（降级回 Cookie 通路）+ warning 只提示一次。"""
    def _boom():
        raise RuntimeError("IAM down")

    monkeypatch.setitem(sys.modules, "infra.j2c_utils",
                        SimpleNamespace(_get_iam_headers=_boom))
    with caplog.at_level(logging.WARNING, logger="openops.iam_headers"):
        assert iam_headers.iam_auth_headers() == {}
        assert iam_headers.iam_auth_headers() == {}
    warns = [r for r in caplog.records if "取 IAM token 失败" in r.getMessage()]
    assert len(warns) == 1  # 风暴期每条告警都会走到这——限频一次

    # 空 Authorization 同样降级为空 dict
    _fake_j2c(monkeypatch, auth="")
    assert iam_headers.iam_auth_headers() == {}


def test_omodel_client_kwargs_carries_iam(monkeypatch):
    """omodel 出站唯一头构造点：内网态含 Authorization，本仓库态不含（7 方法共用此缝）。"""
    from infra.external import omodel_real

    base = "https://omodel.example.internal"
    assert "Authorization" not in omodel_real._client_kwargs(base)["headers"]
    _fake_j2c(monkeypatch)
    headers = omodel_real._client_kwargs(base)["headers"]
    assert headers["Authorization"] == "Bearer IAM-TOKEN"
    assert headers["User-Agent"]  # 既有头原样保留


def test_console_client_kwargs_carries_iam(monkeypatch):
    """console 系（mcps/skills 目录）出站统一装配点：内网态含 Authorization。"""
    from infra.external.mcp_registry_client import console_client_kwargs

    base = "https://console.example.internal"
    assert "Authorization" not in console_client_kwargs(base, "OPENOPS_MCPREGISTRY_COOKIE")["headers"]
    _fake_j2c(monkeypatch)
    headers = console_client_kwargs(base, "OPENOPS_MCPREGISTRY_COOKIE")["headers"]
    assert headers["Authorization"] == "Bearer IAM-TOKEN"
