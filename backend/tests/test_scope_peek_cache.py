"""peek 专用缓存（scope_service._peek_cache，2026-08-15）。

背景：peek_effective_appids 此前只读 _cache、**不写任何缓存**，于是告警摄入的每个
Kafka 批次（getmany timeout 1s）都真打一次 omodel——内网实测约 1 秒一次出站，上游 403
时把 journald 刷满，每次还搭一个同步 IAM 取 token 阻塞事件循环。ingest.py 的注释
「跨批由 peek 的 30s 缓存兜住频率」当时是错的。

这里锁住四条边界：
① 成功结果被缓存（频率下来了）；
② **失败也被缓存**（短负 TTL）——403 场景每批都失败，只缓存成功等于没修；
③ peek 仍不污染 _cache（resolve_for_task 的 audit 要读 scope_snapshot_id）；
④ fresh=True 能绕过负缓存（用户点按钮的路径不该被后台的失败判死）。
"""
from __future__ import annotations

import pytest

from app import scope_service

WS = "ws_pay_abc"
INSTANCE = {"workspace_id": WS, "scope_revision": "rev-1",
            "agent_team_instance_id": "inst-1"}
UID = "u_peek"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    scope_service._reset_cache()
    monkeypatch.delenv("OPENOPS_SCOPE_OVERRIDE_APPIDS", raising=False)
    yield
    scope_service._reset_cache()


def _stub_resolve(monkeypatch, result: dict, counter: dict[str, int]) -> None:
    async def _resolve(ws_id, rev, user_id):  # noqa: ANN001, ARG001
        counter["n"] += 1
        return result

    monkeypatch.setattr(scope_service.omodel_client, "resolve_scope", _resolve)


OK = {"status": "ok", "effective_appids": ["APP-B", "APP-A"]}
FAILED = {"status": "failed", "reason": "HTTP 403"}


async def test_success_cached_within_ttl(monkeypatch):
    calls = {"n": 0}
    _stub_resolve(monkeypatch, OK, calls)
    for _ in range(5):
        assert await scope_service.peek_effective_appids(UID, INSTANCE) == ["APP-A", "APP-B"]
    assert calls["n"] == 1, "TTL 内不得重复打 omodel"


async def test_failure_cached_too(monkeypatch):
    """负缓存是本次修复的**关键**：403 场景每批都失败，只缓存成功等于没修。"""
    calls = {"n": 0}
    _stub_resolve(monkeypatch, FAILED, calls)
    for _ in range(5):
        assert await scope_service.peek_effective_appids(UID, INSTANCE) == []
    assert calls["n"] == 1


async def test_exception_cached_too(monkeypatch):
    """网络异常（omodel 完全不可达）与 status!=ok 走同一条负缓存。"""
    calls = {"n": 0}

    async def _boom(ws_id, rev, user_id):  # noqa: ANN001, ARG001
        calls["n"] += 1
        raise RuntimeError("connect error")

    monkeypatch.setattr(scope_service.omodel_client, "resolve_scope", _boom)
    for _ in range(3):
        assert await scope_service.peek_effective_appids(UID, INSTANCE) == []
    assert calls["n"] == 1


async def test_failure_ttl_is_shorter_than_success(monkeypatch):
    """负 TTL 必须短：oModel 恢复后不该还被自己压着不重试。"""
    assert scope_service._PEEK_FAIL_TTL_S < scope_service._PEEK_TTL_S


async def test_fresh_bypasses_negative_cache_only(monkeypatch):
    """fresh=True 跳过负缓存（用户点了预览按钮），但成功缓存照读（那不是故障）。"""
    calls = {"n": 0}
    _stub_resolve(monkeypatch, FAILED, calls)
    assert await scope_service.peek_effective_appids(UID, INSTANCE) == []
    assert calls["n"] == 1
    assert await scope_service.peek_effective_appids(UID, INSTANCE, fresh=True) == []
    assert calls["n"] == 2, "fresh 必须绕过负缓存重打"

    scope_service._reset_cache()
    calls["n"] = 0
    _stub_resolve(monkeypatch, OK, calls)
    await scope_service.peek_effective_appids(UID, INSTANCE)
    await scope_service.peek_effective_appids(UID, INSTANCE, fresh=True)
    assert calls["n"] == 1, "成功缓存不因 fresh 失效"


async def test_empty_scope_is_success_not_failure(monkeypatch):
    """上游明确答复「范围为空」≠「问不到」：前者按正常 TTL 缓存、fresh 也不绕。"""
    calls = {"n": 0}
    _stub_resolve(monkeypatch, {"status": "ok", "effective_appids": []}, calls)
    assert await scope_service.peek_effective_appids(UID, INSTANCE) == []
    assert await scope_service.peek_effective_appids(UID, INSTANCE, fresh=True) == []
    assert calls["n"] == 1


async def test_peek_never_writes_main_cache(monkeypatch):
    """_cache 的值是含 scope_snapshot_id 的完整 ScopeContext；peek 写半截 ctx 会污染
    resolve_for_task 的命中消费面（audit 要读 snapshot_id）。"""
    calls = {"n": 0}
    _stub_resolve(monkeypatch, OK, calls)
    await scope_service.peek_effective_appids(UID, INSTANCE)
    assert scope_service._cache == {}


async def test_main_cache_wins_over_peek_cache(monkeypatch):
    """resolve_for_task 写的 _cache 更权威（含快照身份），命中它就不该再看 peek 缓存。"""
    calls = {"n": 0}
    _stub_resolve(monkeypatch, OK, calls)
    await scope_service.peek_effective_appids(UID, INSTANCE)  # 先写 peek 缓存

    import time as _t
    key = (WS, INSTANCE["scope_revision"], UID)
    scope_service._cache[key] = (_t.monotonic() + 300,
                                 {"effective_appids": ["APP-Z"], "scope_snapshot_id": "snap-1"})
    assert await scope_service.peek_effective_appids(UID, INSTANCE) == ["APP-Z"]
    assert calls["n"] == 1


async def test_different_revision_is_a_different_key(monkeypatch):
    """scope_revision 变了就是新范围——缓存不得跨版本命中。"""
    calls = {"n": 0}
    _stub_resolve(monkeypatch, OK, calls)
    await scope_service.peek_effective_appids(UID, INSTANCE)
    await scope_service.peek_effective_appids(UID, {**INSTANCE, "scope_revision": "rev-2"})
    assert calls["n"] == 2


async def test_override_seam_still_short_circuits(monkeypatch):
    """联调覆盖缝优先级最高：不打 omodel、也不进缓存判定。"""
    calls = {"n": 0}
    _stub_resolve(monkeypatch, FAILED, calls)
    monkeypatch.setenv("OPENOPS_SCOPE_OVERRIDE_APPIDS", "APP-X, APP-Y")
    assert await scope_service.peek_effective_appids(UID, INSTANCE) == ["APP-X", "APP-Y"]
    assert calls["n"] == 0


async def test_reset_cache_clears_both(monkeypatch):
    """_reset_cache 是全 suite 的隔离缝（conftest / alerts 各 fixture 都在用），
    漏清 _peek_cache 会让别的文件的 fail-closed 用例偶发拿到缓存范围。"""
    calls = {"n": 0}
    _stub_resolve(monkeypatch, OK, calls)
    await scope_service.peek_effective_appids(UID, INSTANCE)
    assert scope_service._peek_cache
    scope_service._reset_cache()
    assert scope_service._peek_cache == {}
    assert scope_service._cache == {}
