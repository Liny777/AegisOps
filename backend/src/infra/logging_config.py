"""集中日志配置（uvicorn dictConfig 扩展）——本仓库此前**没有任何**日志配置。

在此之前：`run.py` 不传 `log_config`，uvicorn 只装自己的三个 logger（`uvicorn` /
`uvicorn.error` / `uvicorn.access`），**根 logger 没有 handler**。于是全仓 ~35 个
`openops.*` logger 落到 `logging.lastResort`——只输出 WARNING 及以上、无格式、去 stderr，
所有 `log.info()` 被静默丢弃。这就是代码里到处把启动信息写成 `log.warning` + `print()`
的原因（main.py:113-114、studio/__init__.py:95、alerts/__init__.py:79）。

本模块补两件事：
① 给根 logger 装 handler，级别由 `OPENOPS_LOG_LEVEL` 控（默认 INFO）——info 从此可见，
   降噪改造才有「降级到 info」这个档位可用；
② 给 `uvicorn.access` 挂降噪过滤器，见 AccessNoiseFilter。

不复制 uvicorn 的 LOGGING_CONFIG 字面量，而是运行时 deepcopy——uvicorn 版本间它改过
（formatter 类名、handler 流向），复制一份就是埋一个跨版本失配。
"""
from __future__ import annotations

import copy
import logging
import os
import re
import time
from typing import Any

#: 健康探活：k8s/compose healthcheck + 运维 curl，每几秒一条且永远 200，记了也没人看。
QUIET_PATHS = frozenset({"/health", "/healthz", "/api/health", "/api/healthz"})

#: 第三方库里「INFO 即刷屏」的 logger，一律钉到 WARNING。
#:
#: 装根 handler 之前这些库的 INFO 本来就被 lastResort 丢掉，装上之后会**全部冒出来**——
#: 治日志反而先加一把火。`httpx` 尤其危险：它每个**成功**响应打一行且**带完整 URL**
#: （`HTTP Request: GET https://…/workspaces/ws-x/projects "HTTP/1.1 200 OK"`），
#: 而本仓库出站调试日志的既定纪律是绝不记 URL/头/体（见 omodel_real._log_outbound_response
#: 与 mcp_registry_client 的掩码逻辑）——放任它等于绕过那条纪律。
#: 排查第三方库自身问题时用 OPENOPS_THIRD_PARTY_LOG_LEVEL 临时调回 INFO/DEBUG。
THIRD_PARTY_LOGGERS = (
    "httpx", "httpcore",      # 出站 HTTP：httpx 每次成功响应一行且带完整 URL
    "urllib3",                # 内网 j2c_utils 走 requests → urllib3
    "aiokafka", "kafka",      # 告警消费循环：心跳/rebalance 在 INFO
    "openai",                 # GLM/自带模型经 OpenAI 兼容客户端
    "asyncio", "multipart",
)

#: 抑制汇总打到独立 logger，避免在 uvicorn.access 的 filter 里递归回自己。
_summary_log = logging.getLogger("openops.access")

#: 路径归一化：uuid（run_id 等）/ 纯数字 / `前缀_十六进制串`（crid_、run_ 这类）→ {id}。
#: 不归一化就无法按「同一个接口」聚合——每个 run 的 SSE 路径都不同，抑制表会退化成不抑制。
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_PREFIXED_ID = re.compile(r"^[a-zA-Z]{1,12}_[0-9a-zA-Z]{6,}$")

#: 抑制表容量上限。归一化 + 剥 query 后基数应等于「路由数 × 状态码数」（几百），
#: 超了说明归一化没覆盖住某种 id，此时整体清空而不是无限涨——宁可少抑制，不可泄内存。
_MAX_TRACKED = 512


def normalize_path(raw: str) -> str:
    """access 日志里的 path（含 query）→ 可聚合的路由形状。

    先剥 query string：`?page=3` 这类会让基数爆炸，且抑制判定与它无关。
    """
    path = raw.split("?", 1)[0]
    if len(path) > 512:  # 异常长路径不参与聚合，原样返回（也不会污染抑制表：见 _MAX_TRACKED）
        return path[:512]
    parts = path.split("/")
    for i, seg in enumerate(parts):
        if not seg:
            continue
        if seg.isdigit() or _UUID.match(seg) or _PREFIXED_ID.match(seg):
            parts[i] = "{id}"
    return "/".join(parts)


class AccessNoiseFilter(logging.Filter):
    """uvicorn.access 降噪：健康检查全丢；同 `(归一化路径, 状态码)` 在窗口内只放行首条。

    针对的实景（2026-08-15 内网）：前端 SSE 客户端把 401 当网络抖动无限重连（退避封顶
    8s），一个未登录标签页就能永久每 8 秒写一行 access 日志。抑制的是**重复**，不是错误——
    每个 (路径, 状态码) 组合每个窗口都保证有一条能出去，新出现的状态码即时可见。

    被抑制的条数不丢：下一次同组合放行时，先由 `openops.access` 打一条汇总。代价是
    风暴停止后最后一批计数要等下次同组合请求才汇报——可接受，因为风暴停止本身就是
    「问题没了」，而持续风暴一定会有下一条。

    窗口 0 = 关闭抑制（排查具体请求时用），健康检查过滤仍生效。
    """

    def __init__(self, window_s: float = 60.0, quiet_paths: frozenset[str] = QUIET_PATHS) -> None:
        super().__init__()
        self._window = max(0.0, float(window_s))
        self._quiet = quiet_paths
        # (path, status) -> (窗口起点 monotonic, 窗口内被抑制的条数)
        self._seen: dict[tuple[str, str], tuple[float, int]] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        # uvicorn 的 access 记录形如 ('%s - "%s %s HTTP/%s" %d', client, method, path, ver, status)。
        # 形状不符（uvicorn 换了实现 / 别人往这个 logger 里打了东西）→ 一律放行：
        # 降噪出错的正确方向是多打，不是丢。
        if not (isinstance(args, tuple) and len(args) == 5):
            return True
        path = normalize_path(str(args[2]))
        if path in self._quiet:
            return False
        if self._window <= 0:
            return True

        key = (path, str(args[4]))
        now = time.monotonic()
        started, suppressed = self._seen.get(key, (0.0, 0))
        if now - started < self._window:
            self._seen[key] = (started, suppressed + 1)
            return False
        if suppressed:
            # 秒数取整但不小于 1：窗口设成亚秒（测试/排查）时「近 0s」读起来像没统计。
            _summary_log.info("[access] %s %s 另有 %d 条已抑制（近 %ds）",
                              key[1], path, suppressed, max(1, round(now - started)))
        if len(self._seen) >= _MAX_TRACKED:
            self._seen.clear()  # 归一化漏网导致基数失控：整体重置，下个窗口重新统计
        self._seen[key] = (now, 0)
        return True


class WindowGate:
    """按 key 的时间窗日志闸门：窗口内同 key 只放行首条，被压条数由下次放行时一并汇报。

    与 AccessNoiseFilter 是同一套语义，但作用在**调用点**而非 logging filter 上——
    出站调试日志的 key 是业务语义（状态码 / cookie 来源），不是 LogRecord 里现成的字段。

    窗口秒数每次现读 env（同 mcp_route()/ec2_ip() 的直读口径）：联调时改 env 重启即生效，
    不必为一个调试旋钮做热加载。窗口 ≤0 = 不抑制。
    """

    def __init__(self, window_env: str, default_s: float = 60.0) -> None:
        self._env = window_env
        self._default = default_s
        self._seen: dict[str, tuple[float, int]] = {}

    def _window(self) -> float:
        raw = os.getenv(self._env, "").strip()
        if not raw:
            return self._default
        try:
            return max(0.0, float(raw))
        except ValueError:
            return self._default

    def allow(self, key: str) -> tuple[bool, int]:
        """→ (是否放行, 上一窗口被抑制的条数)。抑制条数只在放行那次非 0。"""
        window = self._window()
        if window <= 0:
            return True, 0
        now = time.monotonic()
        started, suppressed = self._seen.get(key, (0.0, 0))
        if now - started < window:
            self._seen[key] = (started, suppressed + 1)
            return False, 0
        if len(self._seen) >= _MAX_TRACKED:
            self._seen.clear()
        self._seen[key] = (now, 0)
        return True, suppressed

    def _reset(self) -> None:  # 测试隔离
        self._seen.clear()


def suppressed_suffix(count: int) -> str:
    """给放行的那条日志加「同类还压了 N 条」的尾巴；0 条时返回空串（不改变原文）。"""
    return f"（同类已抑制 {count} 条）" if count else ""


def _level() -> str:
    """`OPENOPS_LOG_LEVEL` → 合法级别名；非法值回退 INFO（配错不该让进程起不来）。"""
    raw = os.getenv("OPENOPS_LOG_LEVEL", "").strip().upper()
    return raw if raw in ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG") else "INFO"


def _third_party_level() -> str:
    """`OPENOPS_THIRD_PARTY_LOG_LEVEL`；默认 WARNING（见 THIRD_PARTY_LOGGERS 的理由）。"""
    raw = os.getenv("OPENOPS_THIRD_PARTY_LOG_LEVEL", "").strip().upper()
    return raw if raw in ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG") else "WARNING"


def _access_dedup_window() -> float:
    try:
        return max(0.0, float(os.getenv("OPENOPS_ACCESS_LOG_DEDUP_S", "60") or "60"))
    except ValueError:
        return 60.0


def build_log_config() -> dict[str, Any]:
    """给 `uvicorn.Config(log_config=...)` 用的 dictConfig。"""
    import uvicorn.config

    cfg: dict[str, Any] = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    level = _level()

    # 根 handler：全仓 openops.* 的唯一出口。走 stdout（uvicorn 的 default 走 stderr），
    # 两者在 journald / docker json-file 里都会被收集，分流只是为了本地肉眼区分。
    cfg.setdefault("formatters", {})["openops"] = {
        "format": "%(asctime)s %(levelname)s %(name)s: %(message)s",
        "datefmt": "%Y-%m-%d %H:%M:%S",
    }
    cfg.setdefault("handlers", {})["openops"] = {
        "formatter": "openops",
        "class": "logging.StreamHandler",
        "stream": "ext://sys.stdout",
    }
    cfg["root"] = {"handlers": ["openops"], "level": level}

    cfg.setdefault("filters", {})["openops_access_noise"] = {
        "()": "infra.logging_config.AccessNoiseFilter",
        "window_s": _access_dedup_window(),
    }
    access = cfg.setdefault("loggers", {}).setdefault("uvicorn.access", {})
    access["filters"] = [*access.get("filters", []), "openops_access_noise"]

    # uvicorn 自身三个 logger 的级别跟随 OPENOPS_LOG_LEVEL（默认 INFO，与原行为一致）；
    # 设成 WARNING 即可整体静掉 access 行，是日志量应急旋钮。
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        if name in cfg["loggers"]:
            cfg["loggers"][name]["level"] = level

    # 第三方库钉级别：必须在装根 handler 的同一份配置里做，否则它们的 INFO 会顺着
    # 新根 handler 全部冒出来（httpx 还会带完整 URL）——治日志反倒先加一把火。
    tp = _third_party_level()
    for name in THIRD_PARTY_LOGGERS:
        cfg["loggers"][name] = {"level": tp}  # 不给 handler：照常向上冒到根，只卡级别
    return cfg
