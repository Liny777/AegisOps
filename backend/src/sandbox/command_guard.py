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

# 回退分类器用（无 agentscope 时）：已知只读命令前缀（自动放行）
_READONLY_PREFIXES = (
    "ls", "pwd", "cat", "head", "tail", "grep", "find", "echo", "wc", "stat", "file",
    "df", "du", "ps", "top", "env", "printenv", "whoami", "id", "date", "uname",
    "git status", "git log", "git diff", "git show", "git branch",
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
    if any(cmd == p or cmd.startswith(p + " ") for p in _READONLY_PREFIXES):
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
    # PASSTHROUGH（层 3）：只读放行、非只读 ask
    if await bash.check_read_only({"command": command}):
        return GuardDecision("allow", "只读命令放行", 3)
    return GuardDecision("ask", "非只读命令，需人工确认", 3)
