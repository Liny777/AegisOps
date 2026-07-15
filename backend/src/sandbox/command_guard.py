"""容器内 Bash 四层裁决（B8-3，09 号「容器内 Bash 管控」）。

纯决策：给一条命令与平台规则，返回 allow / ask / deny + 命中层。执行与审计在装配层。

四层（先命中先裁决）：
  1. 平台 deny 规则（管理员前缀黑名单，最高优先，不可被放行覆盖）
  2. agentscope 原生 Bash.check_permissions 内置安全分析（注入检测 / 只读自动放行 /
     危险命令与关键路径拦截 rm→/、~、/usr，chmod 777、mkfs 等）——ALLOW / ASK[bypass_immune]
  3. PASSTHROUGH（内置未定性）→ 只读放行、非只读 ask（走 HITL 审批）
  4. 容器隔离兜底（不在本决策内——即便放行，破坏面也限于该用户容器；执行层保证）

agentscope 不可用时（fake/CI 无 agentscope）回退内置静态分类器，保证决策可测、fail-closed
（未知非只读命令一律 ask）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


def parse_deny_prefixes(raw: Any) -> list[str]:
    """把 runtime_config 的 bash_deny_prefixes 值归一为前缀列表。

    管理台编辑框按字符串回传（逗号分隔），API 直 PUT 可能是 JSON 数组——两种都容忍。
    关键：字符串值绝不能 `list(str)` 拆成单字符（sandbox_bash 旧隐患，如 "docker" → d/o/c/k/e/r）。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        return [p.strip() for p in raw.split(",") if p.strip()]
    return [str(p).strip() for p in raw if str(p).strip()]

# 只读命令前缀（自动放行）——guard 自己的白名单，比 agentscope 硬编码窄清单更全，覆盖 SRE 诊断类。
# 只收「确无写/删/改」的观察类命令；多词项（`kubectl get`）只放行读子命令，写子命令（`kubectl delete`）
# 不在列 → 落 ask。刻意不含 dual-use（curl/wget/nc/ssh）与可变更的 ip/route/ifconfig/iptables/awk/sed/tee。
_READONLY_PREFIXES = (
    # 文件/文本（只读）
    "ls", "pwd", "cat", "head", "tail", "grep", "egrep", "fgrep", "find", "echo", "wc", "stat",
    "file", "less", "more", "tree", "which", "whereis", "type", "readlink", "realpath",
    "sort", "uniq", "cut", "tr", "diff", "cmp", "column", "md5sum", "sha256sum", "cksum", "printf",
    # 系统/进程（只读观察）
    "df", "du", "ps", "pgrep", "pstree", "top", "free", "vmstat", "iostat", "uptime",
    "lscpu", "lsblk", "lsof", "lsmod", "dmesg", "env", "printenv", "whoami", "id", "date",
    "uname", "hostname",
    # 网络诊断（只读/无变更）
    "netstat", "ss", "dig", "host", "nslookup", "ping", "traceroute",
    "journalctl",
    # git 只读子命令
    "git status", "git log", "git diff", "git show", "git branch", "git remote", "git rev-parse",
    # kubectl 只读子命令
    "kubectl get", "kubectl describe", "kubectl logs", "kubectl top", "kubectl explain",
    "kubectl version", "kubectl cluster-info", "kubectl api-resources",
    # systemctl 只读子命令
    "systemctl status", "systemctl list-units", "systemctl is-active", "systemctl is-enabled",
    "systemctl show", "systemctl cat",
    # docker 只读子命令
    "docker ps", "docker images", "docker inspect", "docker logs", "docker stats",
    "docker top", "docker version", "docker info",
)
# 回退分类器用：危险命令模式（拦截为 ask[dangerous]）
_DANGEROUS = re.compile(
    r"(^|\s|;|&&|\|\|)\s*(rm\s+-[rf]|rmdir|mkfs|dd\s|chmod\s+777|chmod\s+-R\s+777|"
    r":\(\)\s*\{|shutdown|reboot|mv\s+/\S+\s+/dev/null)",
    re.IGNORECASE,
)
# 回退分类器用：命令首 token（判非只读写类）
_NONREADONLY_HINT = re.compile(r"^\s*(rm|mv|cp|mkdir|touch|sed|tee|chmod|chown|kill|pip|npm|apt|curl|wget|python|bash|sh)\b")


@dataclass
class GuardDecision:
    action: str  # allow / ask / deny
    reason: str
    layer: int


# 命令串联/替换分隔符：拆成子段后逐段比对 deny（B8-SEC-001：deny 不可被 `&&`/`;`/`$()` 绕过）
_CHAIN_SPLIT = re.compile(r"[;\n]|&&|\|\||\||\$\(|`|<\(|>\(")


def _segments(command: str) -> list[str]:
    """把一条命令按 shell 串联/替换算符拆成子命令段（含子 shell 里的命令），逐段做 deny 匹配。"""
    return [s.strip() for s in _CHAIN_SPLIT.split(command) if s.strip()]


# 写到真实文件的重定向（`> f` / `>> f`），排除 `2>/dev/null`/`>/dev/null`/`>&2` 这类无害丢弃
_WRITE_REDIRECT = re.compile(r">>?\s*(?!/dev/null|&)")


def _guard_read_only(command: str) -> bool:
    """guard 自己的只读识别（比 agentscope 窄清单更全，覆盖 SRE 诊断）：每个子命令段的首 token 都在
    只读白名单，且无写到真实文件的重定向。命令替换 `$(...)`/反引号会被 _segments 拆出无法匹配的残段
    （如 `ls)`）→ 判非只读（这类由 agentscope 层 2 注入检查另行 ask，此处再兜一层）。"""
    if _WRITE_REDIRECT.search(command):
        return False
    segs = _segments(command)
    if not segs:
        return False
    for seg in segs:
        if not any(seg == p or seg.startswith(p + " ") for p in _READONLY_PREFIXES):
            return False
    return True


def _matches_deny(command: str, deny_prefixes: list[str]) -> str | None:
    """token 级 deny：对每个子命令段的首 token 词边界匹配（B8-SEC-001 串联绕过 + B8-OBS-002 前缀误伤）。

    - 词边界：`rm` 命中 `rm -rf x` 但不误伤 `rmdir`（旧 startswith 会误命中）。
    - 串联：`echo hi && curl x` 的每段都比对，`curl` deny 不再被降级为 ask。
    """
    for seg in _segments(command):
        first = seg.split(None, 1)[0] if seg.split() else seg  # 段内首 token（可执行名）
        for p in deny_prefixes:
            p = p.strip()
            if not p:
                continue
            base = p[:-2].strip() if p.endswith(":*") else p  # 前缀通配 `docker:*`
            # 词边界匹配：段等于 base、或 base 后接空白（`rm ...`），不 startswith 裸拼接
            if first == base or seg == base or seg.startswith(base + " ") or seg.startswith(base + "\t"):
                return p
    return None


def _fallback_decide(command: str) -> GuardDecision:
    """无 agentscope 的内置分类器（层 2/3 合并）：只读放行、危险/非只读 ask，fail-closed。"""
    cmd = command.strip()
    if _DANGEROUS.search(cmd):
        return GuardDecision("ask", "危险命令模式（删除/权限/磁盘），需人工确认", 2)
    if _guard_read_only(cmd):
        return GuardDecision("allow", "只读命令自动放行", 2)
    if _NONREADONLY_HINT.match(cmd):
        return GuardDecision("ask", "非只读命令，需人工确认", 3)
    return GuardDecision("ask", "未知命令，保守 fail-closed 走确认", 3)


async def decide_async(command: str, *, deny_prefixes: list[str] | None = None) -> GuardDecision:
    """异步决策（装配层在 async 上下文调用）：优先 agentscope 内置分析，回退内置分类器。"""
    deny_prefixes = deny_prefixes or []
    # 层 1：平台 deny 前缀黑名单（最高优先）
    hit = _matches_deny(command, deny_prefixes)
    if hit is not None:
        return GuardDecision("deny", f"命中平台 deny 规则 `{hit}`", 1)
    # 层 2：agentscope 原生内置安全分析
    try:
        from agentscope.permission import PermissionBehavior, PermissionContext
        from agentscope.tool import Bash
    except Exception:  # noqa: BLE001 — 无 agentscope
        return _fallback_decide(command)
    bash = Bash()
    d = await bash.check_permissions({"command": command}, PermissionContext())
    if d.behavior == PermissionBehavior.ALLOW:
        return GuardDecision("allow", "内置分析：只读/安全命令自动放行", 2)
    if d.behavior == PermissionBehavior.ASK:
        return GuardDecision("ask", "内置分析：危险命令，需人工确认", 2)
    if d.behavior == PermissionBehavior.DENY:
        return GuardDecision("deny", "内置分析：拒绝执行", 2)
    # PASSTHROUGH（层 3）：agentscope 未定性（既非 ALLOW/ASK/DENY）。用 guard 自己的更宽只读白名单放行
    # ——agentscope 的 check_read_only 复用其硬编码窄清单（ps/df/netstat/systemctl status… 均不识别、
    # 且不可外部扩展），直接用它这里恒为 False、只读永远被 ask（此前的死代码）。改用 _guard_read_only。
    if _guard_read_only(command):
        return GuardDecision("allow", "只读命令放行（guard 白名单）", 3)
    return GuardDecision("ask", "非只读命令，需人工确认", 3)
