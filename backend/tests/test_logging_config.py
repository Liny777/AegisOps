"""集中日志配置（infra.logging_config）：access 降噪 + 根 handler + 通用窗口闸门。

背景：内网 access 日志被前端 SSE 的 401 重连刷满（退避封顶 8s，一个未登录标签页永久每
8 秒一行）。这里锁住降噪的**边界**——降噪必须只压重复，不得压掉新信息。
"""
from __future__ import annotations

import logging

import pytest

from infra.logging_config import (
    AccessNoiseFilter,
    WindowGate,
    build_log_config,
    normalize_path,
    suppressed_suffix,
)


def _record(path: str, status: int = 200, client: str = "172.19.0.3:1") -> logging.LogRecord:
    """构造一条与 uvicorn 完全同形的 access 记录（h11_impl 的 access_logger.info 调用）。"""
    return logging.LogRecord(
        name="uvicorn.access", level=logging.INFO, pathname=__file__, lineno=1,
        msg='%s - "%s %s HTTP/%s" %d', args=(client, "GET", path, "1.1", status), exc_info=None,
    )


# ============================ 路径归一化 ============================


@pytest.mark.parametrize("raw,expect", [
    # 不归一化 uuid 就无法按接口聚合——每个 run 的 SSE 路径都不同，抑制表会退化成不抑制
    ("/api/openops/v1/agent-runs/58045ee4-c693-4f5d-b3a1-af63409a3f3f/events/stream",
     "/api/openops/v1/agent-runs/{id}/events/stream"),
    ("/api/openops/v1/runs/12345/state", "/api/openops/v1/runs/{id}/state"),
    ("/api/openops/v1/tasks/crid_a1b2c3d4", "/api/openops/v1/tasks/{id}"),
    # query string 必须剥掉：分页参数会让基数爆炸，且与抑制判定无关
    ("/api/openops/v1/alerts?page=3&size=20", "/api/openops/v1/alerts"),
    ("/health", "/health"),
    ("/", "/"),
])
def test_normalize_path(raw, expect):
    assert normalize_path(raw) == expect


def test_normalize_path_keeps_real_segments():
    """别把正常路径段误判成 id——归一化过头会把不同接口聚成一个，压掉真信息。"""
    p = "/api/openops/v1/agent-teams/instances/config"
    assert normalize_path(p) == p


# ============================ access 降噪 ============================


def test_health_paths_always_dropped():
    f = AccessNoiseFilter(window_s=0)  # 窗口关掉也要丢健康检查
    for p in ("/health", "/healthz", "/api/health"):
        assert f.filter(_record(p)) is False


def test_same_path_status_suppressed_within_window():
    """同 (路径,状态码) 窗口内只放行首条——这正是 SSE 401 刷屏的形状。"""
    f = AccessNoiseFilter(window_s=60)
    path = "/api/openops/v1/agent-runs/58045ee4-c693-4f5d-b3a1-af63409a3f3f/events/stream"
    assert f.filter(_record(path, 401)) is True
    for _ in range(10):
        assert f.filter(_record(path, 401)) is False


def test_different_status_passes_immediately():
    """新状态码即时可见：压的是重复，不是错误。"""
    f = AccessNoiseFilter(window_s=60)
    path = "/api/openops/v1/x"
    assert f.filter(_record(path, 401)) is True
    assert f.filter(_record(path, 500)) is True
    assert f.filter(_record(path, 200)) is True


def test_different_run_ids_share_one_bucket():
    """归一化后不同 run 聚成同一桶——否则每个 run 各放行一条，等于没压。"""
    f = AccessNoiseFilter(window_s=60)
    a = "/api/openops/v1/agent-runs/58045ee4-c693-4f5d-b3a1-af63409a3f3f/events/stream"
    b = "/api/openops/v1/agent-runs/11111111-2222-3333-4444-555555555555/events/stream"
    assert f.filter(_record(a, 401)) is True
    assert f.filter(_record(b, 401)) is False


def test_window_zero_disables_suppression():
    """窗口 0 = 排查具体请求时全量放行（健康检查仍丢）。"""
    f = AccessNoiseFilter(window_s=0)
    for _ in range(5):
        assert f.filter(_record("/api/openops/v1/x", 401)) is True


def test_unknown_record_shape_passes():
    """形状不符一律放行：降噪出错的正确方向是多打，不是丢。"""
    f = AccessNoiseFilter(window_s=60)
    rec = logging.LogRecord(name="uvicorn.access", level=logging.INFO, pathname=__file__,
                            lineno=1, msg="自定义消息", args=None, exc_info=None)
    assert f.filter(rec) is True


def test_suppressed_count_reported_on_next_pass(monkeypatch, caplog):
    """被压的条数不能凭空消失——下次放行时由 openops.access 汇报。"""
    f = AccessNoiseFilter(window_s=60)
    path = "/api/openops/v1/x"
    assert f.filter(_record(path, 401)) is True
    for _ in range(3):
        f.filter(_record(path, 401))
    f._seen[(path, "401")] = (-1000.0, 3)  # 拨窗口到过去，等价于窗口已过
    with caplog.at_level(logging.INFO, logger="openops.access"):
        assert f.filter(_record(path, 401)) is True
    assert any("另有 3 条已抑制" in r.getMessage() for r in caplog.records)


def test_tracking_table_bounded():
    """归一化漏网导致基数失控时整体重置——宁可少抑制，不可泄内存。"""
    from infra.logging_config import _MAX_TRACKED

    f = AccessNoiseFilter(window_s=60)
    for i in range(_MAX_TRACKED + 50):
        f.filter(_record(f"/api/openops/v1/seg-{i}-x", 200))
    assert len(f._seen) <= _MAX_TRACKED


# ============================ 通用窗口闸门 ============================


def test_window_gate_suppresses_and_counts(monkeypatch):
    monkeypatch.setenv("OPENOPS_TEST_GATE_S", "60")
    g = WindowGate("OPENOPS_TEST_GATE_S")
    assert g.allow("status:403") == (True, 0)
    for _ in range(4):
        assert g.allow("status:403") == (False, 0)
    g._seen["status:403"] = (-1000.0, 4)
    assert g.allow("status:403") == (True, 4)


def test_window_gate_zero_passes_all(monkeypatch):
    monkeypatch.setenv("OPENOPS_TEST_GATE_S", "0")
    g = WindowGate("OPENOPS_TEST_GATE_S")
    for _ in range(3):
        assert g.allow("k")[0] is True


def test_window_gate_bad_value_falls_back(monkeypatch):
    """配错不该让日志行为变成未定义。"""
    monkeypatch.setenv("OPENOPS_TEST_GATE_S", "不是数字")
    g = WindowGate("OPENOPS_TEST_GATE_S", default_s=60.0)
    assert g._window() == 60.0


def test_suppressed_suffix():
    assert suppressed_suffix(0) == ""
    assert "3" in suppressed_suffix(3)


# ============================ dictConfig 装配 ============================


def test_build_log_config_adds_root_handler(monkeypatch):
    """根 handler 是全仓 openops.* 的唯一出口——没有它，所有 log.info 被静默丢弃
    （改造前的实际状态，也是 Part 3 敢把出站调试日志降到 info 的前提）。"""
    monkeypatch.delenv("OPENOPS_LOG_LEVEL", raising=False)
    cfg = build_log_config()
    assert cfg["root"]["handlers"] == ["openops"]
    assert cfg["root"]["level"] == "INFO"
    assert cfg["handlers"]["openops"]["class"] == "logging.StreamHandler"


def test_build_log_config_level_env(monkeypatch):
    monkeypatch.setenv("OPENOPS_LOG_LEVEL", "warning")
    cfg = build_log_config()
    assert cfg["root"]["level"] == "WARNING"
    assert cfg["loggers"]["uvicorn.access"]["level"] == "WARNING"


def test_build_log_config_bad_level_falls_back(monkeypatch):
    monkeypatch.setenv("OPENOPS_LOG_LEVEL", "VERBOSE")
    assert build_log_config()["root"]["level"] == "INFO"


def test_third_party_loggers_pinned_to_warning(monkeypatch):
    """装了根 handler 之后，第三方库的 INFO 会**全部**冒出来——治日志反倒先加一把火。

    httpx 尤其危险：每个成功响应一行且**带完整 URL**，直接绕过本仓库
    「出站日志绝不记 URL/头/体」的纪律（omodel_real._log_outbound_response 的注释）。
    """
    monkeypatch.delenv("OPENOPS_THIRD_PARTY_LOG_LEVEL", raising=False)
    cfg = build_log_config()
    for name in ("httpx", "httpcore", "urllib3", "aiokafka", "openai"):
        assert cfg["loggers"][name]["level"] == "WARNING"
    # 不给 handler：照常向上冒到根，只卡级别
    assert "handlers" not in cfg["loggers"]["httpx"]


def test_third_party_level_env_override(monkeypatch):
    """排查第三方库自身问题时要能调回来。"""
    monkeypatch.setenv("OPENOPS_THIRD_PARTY_LOG_LEVEL", "debug")
    assert build_log_config()["loggers"]["httpx"]["level"] == "DEBUG"
    monkeypatch.setenv("OPENOPS_THIRD_PARTY_LOG_LEVEL", "乱填")
    assert build_log_config()["loggers"]["httpx"]["level"] == "WARNING"


def test_third_party_pin_does_not_touch_openops_loggers(monkeypatch):
    """钉的只能是第三方——把 openops.* 一起钉了就等于白装根 handler。"""
    monkeypatch.setenv("OPENOPS_LOG_LEVEL", "INFO")
    cfg = build_log_config()
    assert not any(n.startswith("openops") for n in cfg["loggers"])
    assert cfg["root"]["level"] == "INFO"


def test_build_log_config_attaches_access_filter():
    cfg = build_log_config()
    assert "openops_access_noise" in cfg["loggers"]["uvicorn.access"]["filters"]
    assert cfg["filters"]["openops_access_noise"]["()"] == "infra.logging_config.AccessNoiseFilter"


def test_build_log_config_is_dictconfig_loadable(monkeypatch):
    """真装一遍：`()` 工厂路径写错、filter 构造签名不符都只在 dictConfig 时才炸，
    而那时进程已经在起 uvicorn 了——必须在测试里先炸。

    dictConfig 会**换掉根 handler**（pytest 的 caplog 就挂在那），不还原会让后面用
    caplog 的用例莫名其妙拿不到日志。故快照/还原根 handler 与被改的 logger。
    """
    import logging.config

    root = logging.getLogger()
    access = logging.getLogger("uvicorn.access")
    saved = (list(root.handlers), root.level, list(access.filters), list(access.handlers))
    try:
        monkeypatch.setenv("OPENOPS_ACCESS_LOG_DEDUP_S", "60")
        logging.config.dictConfig(build_log_config())
        assert access.filters
        assert isinstance(access.filters[0], AccessNoiseFilter)
    finally:
        root.handlers[:], root.level = saved[0], saved[1]
        access.filters[:], access.handlers[:] = saved[2], saved[3]
