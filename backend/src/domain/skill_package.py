"""Skill 包 checksum（纯函数，sandbox 执行面与 Skill Hub 投递面共用）。

绑文件名（B8-OBS-001：不同布局同拼接字节不再碰撞）；与 Skill Hub `X-Checksum-SHA256`（29.3 真包）
同一算法——按文件名排序、`name\0content` 拼接后 sha256。
"""
from __future__ import annotations

import hashlib


def package_checksum(files: dict[str, bytes]) -> str:
    parts = b"".join(name.encode("utf-8") + b"\0" + files[name] for name in sorted(files))
    return hashlib.sha256(parts).hexdigest()
