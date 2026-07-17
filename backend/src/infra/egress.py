"""用户自定义出站 egress policy（13 / 28.4 号 SSRF 防护）：用户 LLM base_url + 用户自定义 MCP endpoint。

两条出站共用同一条梯子（`_check`）：`check_llm_egress` / `check_mcp_egress` 只是薄包装。
**环境变量沿用 `OPENOPS_LLM_EGRESS_*` 命名（历史包袱，不改名），但它们同时管辖 MCP。**
已知残留：`_PIN` 是模块级全局、按裸 hostname 键控且 LLM/MCP 共用——用户 MCP 若在 ALB/蓝绿后面
整组轮换 IP，会被判 rebinding 拦截（发现边界吞成 warning ⇒ 工具静默消失），逃生门 OPENOPS_LLM_EGRESS_PIN=0。

创建/更新 LLM 配置时（以及后续每次调用边界）校验 base_url，阻断指向：
- 环回（127.0.0.0/8、::1）、未指定（0.0.0.0）—— 打自身
- 链路本地（169.254.0.0/16、fe80::/10）—— **云 metadata（169.254.169.254）**
- Docker bridge（默认 172.17.0.0/16）与部署方额外配置的内网基础设施（PG/Redis/后端）

内网 LLM 网关（企业 RFC1918）默认放行——SRE 平台的 GLM 网关常在内网；如需全锁私网，设
`OPENOPS_LLM_EGRESS_BLOCK_PRIVATE=1`。额外 deny CIDR/host 经 `OPENOPS_LLM_EGRESS_DENY`（逗号分隔）。
DNS rebinding 防护：校验时解析 host→IP 并逐一比对（不信任 hostname 字面）；
S3 追加**解析钉扎**：同一 host 的解析结果跨调用比对，与钉扎集完全不相交=疑似 rebinding → 拦截
（OPENOPS_LLM_EGRESS_PIN=0 可关；TTL OPENOPS_LLM_EGRESS_PIN_TTL_S 默认 86400s）。
残余风险（C2-OBS-002 已知）：check→httpx 实连之间的单次窗口仍存在（真正消除需自定义 DNS transport，
V1 不做）——企业 LLM 端点解析稳定，跨调用钉扎已覆盖实际攻击面（TTL=0 快速翻转）。
"""
from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

from domain.errors import ApiError, Err

_DEFAULT_DENY = "172.17.0.0/16"  # Docker 默认 bridge

_PIN: dict[str, tuple[float, frozenset[str]]] = {}  # host → (expire_monotonic, 首见解析 IP 集)


def _pin_enabled() -> bool:
    return os.environ.get("OPENOPS_LLM_EGRESS_PIN", "1") != "0"


def _pin_ttl() -> float:
    try:
        return float(os.environ.get("OPENOPS_LLM_EGRESS_PIN_TTL_S", "86400"))
    except ValueError:
        return 86400.0


def reset_pins() -> None:
    """测试钩子：清解析钉扎表。"""
    _PIN.clear()


def _deny_networks() -> list:
    nets: list = []
    raw = os.environ.get("OPENOPS_LLM_EGRESS_DENY", _DEFAULT_DENY)
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue  # 非法项忽略（可能是 host 名，交 hostname deny）
    return nets


def _deny_hosts() -> set[str]:
    raw = os.environ.get("OPENOPS_LLM_EGRESS_DENY_HOSTS", "localhost,metadata.google.internal,metadata")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _blocked_ip(ip) -> str | None:  # noqa: ANN001 — ip: IPv4Address|IPv6Address
    if ip.is_loopback:
        return "环回地址"
    if ip.is_link_local:
        return "链路本地（含云 metadata）"
    if ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        return "保留/未指定地址"
    if os.environ.get("OPENOPS_LLM_EGRESS_BLOCK_PRIVATE") == "1" and ip.is_private:
        return "内网私有地址（已启用全锁私网）"
    for net in _deny_networks():
        if ip in net:
            return f"命中 egress deny 网段 {net}"
    return None


def check_llm_egress(base_url: str) -> None:
    """校验用户 LLM base_url；不合规抛 MODEL_PROBE_FAILED（含原因）。"""
    _check(base_url, code=Err.MODEL_PROBE_FAILED, label="base_url")


def check_mcp_egress(endpoint: str) -> None:
    """校验用户自定义 MCP endpoint（登记时 + 发现边界 + 调用边界三处）；不合规抛 VALIDATION_FAILED。

    与 LLM 同一条梯子（单一来源）：用户 MCP 的 endpoint 是用户任填的 URL，若不校，注册一个
    `http://169.254.169.254/…` 就能让后端代其读云 metadata。环境变量沿用 `OPENOPS_LLM_EGRESS_*`
    （不改名，历史包袱）——它们现在同时管辖 LLM 与 MCP 两条出站。
    """
    _check(endpoint, code=Err.VALIDATION_FAILED, label="MCP endpoint")


def _check(url: str, *, code: str, label: str) -> None:
    """SSRF 梯子（scheme / deny-host / 逐 IP / rebinding 钉扎）——LLM 与 MCP 共用，勿分叉。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ApiError(code, f"{label} 协议不允许：{parsed.scheme or '(空)'}（仅 http/https）")
    host = parsed.hostname
    if not host:
        raise ApiError(code, f"{label} 缺少主机名")
    if host.lower() in _deny_hosts():
        raise ApiError(code, f"{label} 主机被 egress 策略阻断：{host}")
    # 解析所有 IP（DNS rebinding 防护）；解析失败也拦（无法确认目标安全）
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise ApiError(code, f"{label} 主机无法解析：{host}") from e
    ips = frozenset(str(ipaddress.ip_address(info[4][0])) for info in infos)
    for raw in ips:
        ip = ipaddress.ip_address(raw)
        reason = _blocked_ip(ip)
        if reason is not None:
            raise ApiError(code, f"{label} 指向受限地址（{reason}）：{host}→{ip}")
    # S3 解析钉扎：与首见解析集完全不相交 → 疑似 DNS rebinding（合法 CDN 轮换通常有交集/走 deny 白名单面）
    if _pin_enabled():
        import time as _time

        now = _time.monotonic()
        pinned = _PIN.get(host.lower())
        if pinned and pinned[0] > now and ips.isdisjoint(pinned[1]):
            raise ApiError(code,
                           f"{label} 解析漂移（疑似 DNS rebinding，已拦截）：{host}；如为合法迁移请重启后端或置 OPENOPS_LLM_EGRESS_PIN=0")
        if pinned is None or pinned[0] <= now:
            _PIN[host.lower()] = (now + _pin_ttl(), ips)
