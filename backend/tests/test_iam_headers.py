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
    """限频 flag 与 token 缓存都是 module 级——逐测重置，否则前一个用例缓存的 token
    会让后一个用例根本不调 j2c，断言全部失真。"""
    iam_headers._warned_missing = False
    iam_headers._warned_failed = False
    iam_headers._reset_cache()
    yield
    iam_headers._warned_missing = False
    iam_headers._warned_failed = False
    iam_headers._reset_cache()


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


# ============================ TTL 缓存（2026-08-15） ============================
# 内网 j2c 每次出站都真打 IAM（requests，同步阻塞事件循环），日志里每条出站都伴随一条
# InsecureRequestWarning——这组用例锁住「同一 TTL 内只取一次」。


def _counting_j2c(monkeypatch, auth: str = "Bearer IAM-TOKEN") -> dict[str, int]:
    calls = {"n": 0}

    def _get():
        calls["n"] += 1
        return {"Authorization": auth}

    monkeypatch.setitem(sys.modules, "infra.j2c_utils", SimpleNamespace(_get_iam_headers=_get))
    return calls


def test_token_cached_within_ttl(monkeypatch):
    """TTL 内重复出站只取一次 token（这就是刷屏与阻塞的根治点）。"""
    calls = _counting_j2c(monkeypatch)
    monkeypatch.setenv("OPENOPS_IAM_TOKEN_TTL_S", "300")
    for _ in range(5):
        assert iam_headers.iam_auth_headers() == {"Authorization": "Bearer IAM-TOKEN"}
    assert calls["n"] == 1


def test_cache_expiry_refetches(monkeypatch):
    """TTL 过期后必须重取——token 有寿命，缓存不能变成永久钉死。"""
    calls = _counting_j2c(monkeypatch)
    monkeypatch.setenv("OPENOPS_IAM_TOKEN_TTL_S", "300")
    iam_headers.iam_auth_headers()
    assert calls["n"] == 1
    # 直接把过期时刻拨到过去，避免 sleep 拖慢 suite
    iam_headers._cache = (0.0, {"Authorization": "Bearer IAM-TOKEN"})
    iam_headers.iam_auth_headers()
    assert calls["n"] == 2


def test_ttl_zero_disables_cache(monkeypatch):
    """TTL=0 = 每次现取（排查缝）。"""
    calls = _counting_j2c(monkeypatch)
    monkeypatch.setenv("OPENOPS_IAM_TOKEN_TTL_S", "0")
    for _ in range(3):
        iam_headers.iam_auth_headers()
    assert calls["n"] == 3


def test_failure_not_cached(monkeypatch):
    """失败**不写**缓存：否则一次 IAM 抖动会被放大成整个 TTL 窗口的无鉴权出站，
    与本模块「取不到就降级回 Cookie 通路」的语义相冲。"""
    def _boom():
        raise RuntimeError("IAM down")

    monkeypatch.setitem(sys.modules, "infra.j2c_utils", SimpleNamespace(_get_iam_headers=_boom))
    monkeypatch.setenv("OPENOPS_IAM_TOKEN_TTL_S", "300")
    assert iam_headers.iam_auth_headers() == {}
    assert iam_headers._cache is None

    # 恢复后立即拿到新 token，不被负缓存挡住
    calls = _counting_j2c(monkeypatch)
    assert iam_headers.iam_auth_headers() == {"Authorization": "Bearer IAM-TOKEN"}
    assert calls["n"] == 1


def test_cached_headers_are_copies(monkeypatch):
    """返回副本：调用方（_client_kwargs）会把它 update 进自己的 headers 再改。"""
    _counting_j2c(monkeypatch)
    monkeypatch.setenv("OPENOPS_IAM_TOKEN_TTL_S", "300")
    first = iam_headers.iam_auth_headers()
    first["Authorization"] = "TAMPERED"
    assert iam_headers.iam_auth_headers()["Authorization"] == "Bearer IAM-TOKEN"
