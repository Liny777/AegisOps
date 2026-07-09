"""Secret 应用层加密（13 号 SEC-001）：Fernet（AES128-CBC + HMAC）真加密 + key 轮换。

密钥来源：
- 生产：`OPENOPS_ENCRYPTION_KEY`（Fernet key，base64 urlsafe 32 字节；`Fernet.generate_key()` 生成）。
- 轮换：`OPENOPS_ENCRYPTION_KEY_OLD`（逗号分隔的旧 key，仅解密）；用 MultiFernet 透明兼容旧密文。
- 开发回退：未配 `OPENOPS_ENCRYPTION_KEY` 时从 `OPENOPS_SECRET_KEY` 派生确定性 Fernet key（**仍是真
  Fernet 非 XOR**，仅供本地/测试；生产必须配 `OPENOPS_ENCRYPTION_KEY`，否则 key 丢即密文不可解）。

明文绝不落库/回显（SEC-001）；只在 Model/Tool Gateway 调用边界瞬时 decrypt。
⚠ 从旧「可逆混淆」占位升级：存量 `user_secret.ciphertext`（旧 `enc:` XOR 格式）无法 Fernet 解密，
需用户重新录入 Secret（预生产无存量；迁移脚本另见部署说明）。
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

log = logging.getLogger("openops.crypto")
_warned = False


def _derive_dev_key() -> bytes:
    seed = os.environ.get("OPENOPS_SECRET_KEY", "change-me").encode()
    return base64.urlsafe_b64encode(hashlib.sha256(seed).digest())


def _multifernet() -> MultiFernet:
    global _warned
    keys: list[Fernet] = []
    primary = os.environ.get("OPENOPS_ENCRYPTION_KEY")
    if primary:
        keys.append(Fernet(primary.encode()))
    else:
        if not _warned:
            log.warning("OPENOPS_ENCRYPTION_KEY 未配置：使用从 OPENOPS_SECRET_KEY 派生的开发 key（生产必须显式配置）。")
            _warned = True
        keys.append(Fernet(_derive_dev_key()))
    for old in os.environ.get("OPENOPS_ENCRYPTION_KEY_OLD", "").split(","):
        old = old.strip()
        if old:
            keys.append(Fernet(old.encode()))  # 旧 key 仅用于解密（轮换过渡）
    return MultiFernet(keys)


def current_key_version() -> str:
    """当前加密 key 版本标识（存 user_secret.key_version；轮换时可据此批量重加密）。"""
    return "cfg" if os.environ.get("OPENOPS_ENCRYPTION_KEY") else "dev"


def fingerprint(plain: str) -> str:
    """脱敏指纹：sha256 前 12 位，展示用（不可逆，非加密）。"""
    return "fp_" + hashlib.sha256(plain.encode()).hexdigest()[:12]


def encrypt(plain: str) -> str:
    return _multifernet().encrypt(plain.encode()).decode("ascii")


def decrypt(cipher: str) -> str:
    try:
        return _multifernet().decrypt(cipher.encode("ascii")).decode("utf-8")
    except InvalidToken as e:  # key 不匹配 / 旧 XOR 占位格式 / 篡改
        raise ValueError("Secret 解密失败（key 不匹配或密文非当前格式，需重新录入）") from e
