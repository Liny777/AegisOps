"""Secret 加密占位：指纹 + 可逆混淆。

V1 骨架约束：明文绝不落库、绝不回显（SEC-001）。
TODO(B3 Secret/Model Gateway 块)：换 Fernet + OPENOPS_ENCRYPTION_KEY + key_version 轮换。
"""
from __future__ import annotations

import base64
import hashlib
import os

_KEY = os.environ.get("OPENOPS_SECRET_KEY", "change-me").encode()


def fingerprint(plain: str) -> str:
    """脱敏指纹：sha256 前 12 位，展示用。"""
    return "fp_" + hashlib.sha256(plain.encode()).hexdigest()[:12]


def encrypt(plain: str) -> str:
    data = plain.encode()
    x = bytes(b ^ _KEY[i % len(_KEY)] for i, b in enumerate(data))
    return "enc:" + base64.b64encode(x).decode()


def decrypt(cipher: str) -> str:
    raw = base64.b64decode(cipher.removeprefix("enc:"))
    return bytes(b ^ _KEY[i % len(_KEY)] for i, b in enumerate(raw)).decode()
