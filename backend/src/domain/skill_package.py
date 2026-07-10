"""Skill 包内容 checksum（纯函数，OpenOps 内部执行面完整性校验用）。

按文件名排序、`name\0content` 拼接后 sha256，**绑文件名**（B8-OBS-001：不同布局同拼接字节不碰撞）。
用途：`download_skill_package`→`executor.run_skill` 之间的整包一致性校验（防投递到容器途中被篡改）。
⚠这是 OpenOps 内部算法，**不是** Skill Hub 的 `X-Checksum-SHA256`——后者是下载 ZIP **原始字节**的
sha256（29.3 §2.5，传输完整性）。两者算法不同、值不同、用途不同，勿混用。
"""
from __future__ import annotations

import hashlib


def package_checksum(files: dict[str, bytes]) -> str:
    parts = b"".join(name.encode("utf-8") + b"\0" + files[name] for name in sorted(files))
    return hashlib.sha256(parts).hexdigest()
