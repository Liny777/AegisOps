"""用户自定义 LLM egress policy（13 / 28.4 号 SSRF 防护）。

创建/更新 LLM 配置时（以及后续每次调用边界）校验 base_url，阻断指向：
- 环回（127.0.0.0/8、::1）、未指定（0.0.0.0）—— 打自身
- 链路本地（169.254.0.0/16、fe80::/10）—— **云 metadata（169.254.169.254）**
- Docker bridge（默认 172.17.0.0/16）与部署方额外配置的内网基础设施（PG/Redis/后端）

内网 LLM 网关（企业 RFC1918）默认放行——SRE 平台的 GLM 网关常在内网；如需全锁私网，设
`OPENOPS_LLM_EGRESS_BLOCK_PRIVATE=1`。额外 deny CIDR/host 经 `OPENOPS_LLM_EGRESS_DENY`（逗号分隔）。
DNS rebinding 防护：校验时解析 host→IP 并逐一比对（不信任 hostname 字面）。
"""
from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

from domain.errors import ApiError, Err

_DEFAULT_DENY = "172.17.0.0/16"  # Docker 默认 bridge


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
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https"):
        raise ApiError(Err.MODEL_PROBE_FAILED, f"base_url 协议不允许：{parsed.scheme or '(空)'}（仅 http/https）")
    host = parsed.hostname
    if not host:
        raise ApiError(Err.MODEL_PROBE_FAILED, "base_url 缺少主机名")
    if host.lower() in _deny_hosts():
        raise ApiError(Err.MODEL_PROBE_FAILED, f"base_url 主机被 egress 策略阻断：{host}")
    # 解析所有 IP（DNS rebinding 防护）；解析失败也拦（无法确认目标安全）
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise ApiError(Err.MODEL_PROBE_FAILED, f"base_url 主机无法解析：{host}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        reason = _blocked_ip(ip)
        if reason is not None:
            raise ApiError(Err.MODEL_PROBE_FAILED, f"base_url 指向受限地址（{reason}）：{host}→{ip}")
