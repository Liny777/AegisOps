"""infra.host_ip：平台 MCP 出站头 x-ec-ip 的取值口径。

承重点：值**只**来自 OPENOPS_EC_IP（对端按 IP 白名单准入，值必须与登记项一致，绝不能靠猜）；
脏值一律判无效（含 header 注入形状）；取不到时返回空 dict 而非空值头。
fake_sock 在每个用例里替换 socket.socket，用来断言**本模块从不建 socket**。
"""
from __future__ import annotations

import socket

import pytest

from infra import host_ip


class _FakeSock:
    """替身 socket：记录 connect 收到的目标，getsockname 返回预置源 IP。"""

    connected: list[tuple] = []
    sockname = ("10.9.9.9", 0)
    raise_on_connect: OSError | None = None
    made: list[int] = []

    def __init__(self, fam, typ):
        _FakeSock.made.append(fam)

    def settimeout(self, _t):
        pass

    def connect(self, addr):
        _FakeSock.connected.append(addr)
        if _FakeSock.raise_on_connect:
            raise _FakeSock.raise_on_connect

    def getsockname(self):
        return _FakeSock.sockname

    def close(self):
        pass


@pytest.fixture()
def fake_sock(monkeypatch):
    _FakeSock.connected = []
    _FakeSock.made = []
    _FakeSock.sockname = ("10.9.9.9", 0)
    _FakeSock.raise_on_connect = None
    monkeypatch.setattr(socket, "socket", _FakeSock)
    monkeypatch.delenv("OPENOPS_EC_IP", raising=False)
    return _FakeSock


# --- 取值：只认 OPENOPS_EC_IP ----------------------------------------------------

def test_env_is_the_only_source(monkeypatch, fake_sock):
    """承重：值只来自 env，且**完全不建 socket**——对端按白名单准入，探测出来的网卡地址与登记项
    对不上是常态，上报错值会被判成伪造。"""
    monkeypatch.setenv("OPENOPS_EC_IP", "10.1.2.3")
    assert host_ip.ec_ip() == "10.1.2.3"
    assert host_ip.ec_ip_headers() == {"x-ec-ip": "10.1.2.3"}
    assert fake_sock.made == []


def test_unset_env_yields_no_header_and_never_probes(monkeypatch, fake_sock):
    """未配置 → 不带头（fail-open），且**不回退探测**。"""
    assert host_ip.ec_ip() == ""
    assert host_ip.ec_ip_headers() == {}
    assert fake_sock.made == []


def test_illegal_env_yields_no_header_not_a_guess(monkeypatch, fake_sock):
    """env 非法 → 不带头，而不是退而求其次猜一个。"""
    monkeypatch.setenv("OPENOPS_EC_IP", "0.0.0.0")
    assert host_ip.ec_ip_headers() == {}
    assert fake_sock.made == []


def test_env_is_read_fresh_not_cached(monkeypatch, fake_sock):
    """每次现读（同 mcp_route() 口径），无缓存——改 env 立即生效，测试也不需要 reset 钩子。"""
    monkeypatch.setenv("OPENOPS_EC_IP", "10.1.1.1")
    assert host_ip.ec_ip() == "10.1.1.1"
    monkeypatch.setenv("OPENOPS_EC_IP", "10.2.2.2")
    assert host_ip.ec_ip() == "10.2.2.2"


@pytest.mark.parametrize("bad", [
    "not-an-ip",
    "mcp.example.com",           # 域名不是 IP
    "1.2.3.4\r\nX-Injected: y",  # header 注入形状
    "0.0.0.0",                   # 未绑定到具体接口
    "127.0.0.1",                 # 只有 lo 的容器/沙箱
    "169.254.1.1",               # DHCP 未就绪
    "224.0.0.1",                 # 组播
    "",
])
def test_sane_rejects_bad_values(bad):
    assert host_ip._sane(bad) == ""


def test_sane_accepts_private_addresses():
    """私网地址是**有效**的——内网部署本来就是私网 IP，不可与环回/链路本地混为一谈。"""
    for ok in ("10.1.2.3", "172.16.0.9", "192.168.1.5"):
        assert host_ip._sane(ok) == ok
    assert host_ip._sane("  10.1.2.3  ") == "10.1.2.3"  # 仅首尾空白：strip 后合法


# --- 启动日志 --------------------------------------------------------------------

def test_log_startup_warns_loudly_when_unconfigured(monkeypatch, fake_sock, caplog):
    """未配置时日志必须显眼：对端按白名单准入，缺该头的调用会被拒，而本端只是静默少一个头
    （无异常/无 SSE/无审计）。"""
    with caplog.at_level("WARNING"):
        host_ip.log_startup()
    assert "未配置" in caplog.text and "白名单准入" in caplog.text
    assert fake_sock.made == []  # 本模块不做任何探测


def test_log_startup_reports_configured_value(monkeypatch, fake_sock, caplog):
    monkeypatch.setenv("OPENOPS_EC_IP", "10.1.2.3")
    with caplog.at_level("WARNING"):
        host_ip.log_startup()
    assert "10.1.2.3" in caplog.text
    assert fake_sock.made == []
