"""OpenOps 后端主机自身的出站 IP（平台 MCP 出站头 `x-ec-ip`）。

与 request_context.client_ip() **严格区分**：那是登录用户浏览器的 IP（出站为 IAM-Client-Ip /
X-Forwarded-For，华为 IAM 会话绑它）；本模块是**后端主机自己**的 IP，属内网拓扑信息。两者语义
不同、并存、不可互相回退。

⚠只允许发给**平台自有** MCP。用户自注册 MCP 的 endpoint 由用户任填（含外网地址），带上等于把内网
主机 IP 送出去；且发现路径对每个已登记用户 MCP 每轮无条件出网，泄露发生在任何审批/标注之前。
注入点唯一：tool_gateway._platform_headers（调用面）+ 两个平台 discover_tools 调用点（发现面）。

**值只来自 `OPENOPS_EC_IP`，本模块不做任何自动探测**（2026-07-20 与对端确认的语义）：对端按该 IP
做**白名单准入**——只授权特定主机调它的 MCP。因此这个值必须与对端登记项**逐字节一致**，而探测出来
的是「内核去往某个目标时选的网卡地址」，多网卡 / NAT / SNAT 下与对端登记项对不上是常态。上报一个错
的 IP 比不上报更糟：不带头对端顶多按未授权拒绝，带错值会被判成伪造。运维按对端登记项显式配置。
"""
from __future__ import annotations

import ipaddress
import logging
import os

log = logging.getLogger("openops.hostip")

# 头名常量：HTTP 头名大小写不敏感、httpx 原样发出，但内网自研网关有过大小写敏感解析的先例，
# 故收敛到一处便于按对端口径改。
EC_IP_HEADER = "x-ec-ip"


def _sane(raw: str) -> str:
    """IP 值校验，返回规范化 IP 或 ""（= 无效）。

    含 CR/LF/空白即判无效——header 值直接来自 env，是 header 注入的入口。
    0.0.0.0/::、环回、链路本地（169.254）、组播、保留段一律判无效：这些都不可能是对端登记的授权地址，
    多半是配置写错，带出去只会被判成伪造。私网地址（10./172.16./192.168.）是**有效**的：内网部署
    本来就是私网 IP。
    """
    s = (raw or "").strip()
    if not s or len(s) > 45 or any(c in s for c in "\r\n\t "):
        return ""
    try:
        a = ipaddress.ip_address(s)
    except ValueError:
        return ""
    if a.is_unspecified or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved:
        return ""
    return str(a)


def ec_ip() -> str:
    """本机出站 IP：只读 `OPENOPS_EC_IP`（每次现读，同 mcp_route() 的 os.getenv 直读口径）。

    未配置或值非法 → ""，出站不带该头（fail-open：不在本端阻断调用，让对端的未授权响应作为
    权威信号——那比本端抛一个自造的错误更好定位）。
    """
    return _sane(os.getenv("OPENOPS_EC_IP", ""))


def ec_ip_headers() -> dict[str, str]:
    """平台 MCP 出站的 x-ec-ip 头片段；取不到返回**空 dict**。

    绝不发 `x-ec-ip: ""`——空值头会让对端把「本端未配置」误读成「本端声称自己 IP 为空」。
    """
    ip = ec_ip()
    return {EC_IP_HEADER: ip} if ip else {}


def log_startup() -> None:
    """启动期可观测：未配置时**必须显眼**——对端按 IP 白名单准入，不带该头的平台 MCP 调用会被拒，
    而本端只是静默少一个头（无异常、无 SSE、无审计），这行日志是运维唯一的抓手。

    该配哪个 IP 由对端的授权登记项决定，本端不猜；部署机多网卡时可用
    `ip route get <MCP网关IP>` 的 src 字段自查实际出站地址。
    """
    ip = ec_ip()
    if ip:
        log.warning("[OpenOps][hostip] %s=%s（source=OPENOPS_EC_IP）", EC_IP_HEADER, ip)
        return
    raw = os.getenv("OPENOPS_EC_IP", "").strip()
    why = "值非法（非 IP 字面量，或为环回/未指定地址）" if raw else "未配置"
    log.warning("[OpenOps][hostip] OPENOPS_EC_IP %s ⇒ 平台 MCP 出站不带 %s。"
                "对端按该 IP 做白名单准入，缺失将被判为未授权调用方——请按对端登记的地址配置。",
                why, EC_IP_HEADER)
