"""Skill Hub client：source=openops 资产拉取 + Skill 包投递（29.3 契约面，ASSET-001 / SKILL-*）。

- `list_skills`：对账用资产清单。real 经 29.3 `POST /obsv/agent/management/skills/list/query`
  取分页 `{code,message,data:{items}}`，解包 + 字段映射为 OpenOps 词汇（29.4）。
- `download_skill_package`：真 ZIP 投递（C1）。`OPENOPS_SKILLHUB=mock(默认)|real` 切换；
  mock 合成**可执行**的真包（SKILL.md frontmatter + entrypoint 脚本），供 run_skill 端到端；
  real 经 29.3 `GET /obsv/agent/management/skills/download?skill_id=` 取 ZIP，按响应头 `X-Checksum-SHA256`
  （ZIP 原始字节 sha256，C1-CHK-001）校验传输完整性（未联真环境时 raise，由调用方收口）。
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import zipfile
from typing import Any

from domain.skill_package import package_checksum
from infra.external.mcp_registry_client import (  # 同 console 口径（TLS 三档/代理/文根/共享 cookie/带体报错）
    console_api_prefix,
    console_cookie,
    console_tls_verify,
    http_trust_env,
    raise_with_body,
)


def skillhub_base() -> str:
    """Skill Hub host 根：`OPENOPS_SKILLHUB_BASE_URL`，未配回退 `OPENOPS_MCPREGISTRY_BASE_URL`——
    skills 与 mcps 是同一 console 网关（29.3 同文根），联调少配一个变量（与共享 cookie 同思路）。"""
    return (os.getenv("OPENOPS_SKILLHUB_BASE_URL") or os.getenv("OPENOPS_MCPREGISTRY_BASE_URL") or "").rstrip("/")


def _headers() -> dict[str, str]:
    """console 鉴权 cookie：OPENOPS_SKILLHUB_COOKIE 专属 > 共享 OPENOPS_CONSOLE_COOKIE（mcps 面内网实测必须带）。"""
    cookie = console_cookie("OPENOPS_SKILLHUB_COOKIE")
    return {"Cookie": cookie} if cookie else {}

# mock 平台 Skill「inspection」的可执行包（run.py 写 output.json，run_skill 真跑得通）
_MOCK_RUN_PY = (
    b"import json\n"
    b"print('inspection skill running in sandbox container')\n"
    b"open('output.json', 'w').write(json.dumps({'status': 'success', 'findings': "
    b"['redis conn pool near limit', 'p99 elevated on svc-a']}))\n"
)
_MOCK_SKILL_MD = (
    b"---\nname: inspection\nversion: 2.0.0\nentrypoint: python3 run.py\n"
    b"description: \xe5\xb7\xa1\xe6\xa3\x80 Skill\n---\n# inspection\n"
)
_MOCK_FILES: dict[str, bytes] = {"SKILL.md": _MOCK_SKILL_MD, "run.py": _MOCK_RUN_PY}
_MOCK_ENTRYPOINT = "python3 run.py"
# 资产记录 checksum 与投递包一致（对账时 Skill Hub 提供的 X-Checksum-SHA256 即此值）
MOCK_INSPECTION_CHECKSUM = package_checksum(_MOCK_FILES)


_MOCK_LIST = [
    {
        "skill_key": "inspection",
        "display_name": "巡检 inspection",
        "source": "openops",
        "source_type": "platform",
        "version_no": 2,
        "checksum_sha256": MOCK_INSPECTION_CHECKSUM,
        "status": "active",
    }
]


def _unwrap_data(body: dict[str, Any]) -> dict[str, Any]:
    """业务信封 `{code, message, data}` 解包（code 是业务状态，与 HTTP 状态分离）。

    成功码收 0 和 200 两种：29.3 文档写 `code:0`，但内网实测（2026-07-11 check-net ⑤）skills 面
    成功返回 `code:200`——2026-07-13 起 console 网关 skills/mcps 两面已统一 200（此前 mcps 面是 0）；两收兼容旧版。"""
    if int(body.get("code", -1)) not in (0, 200):
        raise RuntimeError(f"Skill Hub 返回业务错误：code={body.get('code')} {body.get('message', '')}")
    return body.get("data") or {}


def _semver_to_int(semver: str | None) -> int:
    """latest_version(semver) → version_no(int) 排序值（29.4）。V1 仅供形状一致；精确 pin 待 repo 穿透 semver。"""
    if not semver:
        return 1
    parts = (str(semver).split(".") + ["0", "0", "0"])[:3]
    try:
        nums = [int("".join(ch for ch in p if ch.isdigit()) or "0") for p in parts]
        return nums[0] * 10000 + nums[1] * 100 + nums[2]
    except Exception:
        return 1


def _map_skill(it: dict[str, Any]) -> dict[str, Any]:
    """29.3 skill item → OpenOps 词汇（29.4：skill_id→skill_key、name→display_name、is_system→source_type、
    created_by→owner_user_id、latest_version→version_no）。返回键与 mock/_MOCK_LIST 一致，供 reconcile 消费。"""
    return {
        "skill_key": it.get("skill_id"),
        "display_name": it.get("name"),
        "source": it.get("source"),
        "source_type": "platform" if bool(it.get("is_system")) else "user",
        "owner_user_id": it.get("created_by"),
        "version_no": _semver_to_int(it.get("latest_version")),
        "checksum_sha256": it.get("checksum_sha256"),
        "status": it.get("status", "active"),
    }


async def list_skills(user_id: str) -> list[dict[str, Any]]:
    """对账用资产清单（source=openops）。real 经 29.3 `POST /skills/list/query` 拉取 + 信封解包 + 字段映射（未联环境 raise）。"""
    if os.getenv("OPENOPS_SKILLHUB", "mock").lower() == "real":
        base = skillhub_base()
        if not base:
            raise RuntimeError("OPENOPS_SKILLHUB=real 需配 OPENOPS_SKILLHUB_BASE_URL（或 OPENOPS_MCPREGISTRY_BASE_URL，同 console 网关）")
        import httpx

        url = f"{base}{console_api_prefix()}/skills/list/query"
        async with httpx.AsyncClient(timeout=15, verify=console_tls_verify(), trust_env=http_trust_env()) as cli:
            r = await cli.post(url, json={"page": 1, "page_size": 200, "source": "openops"}, headers=_headers())
            raise_with_body(r)  # 非 2xx 带响应体前 300 字（401=cookie 失效）
            items = _unwrap_data(r.json()).get("items", [])  # 分页对象 data.items，非裸 list
        return [_map_skill(it) for it in items]
    return list(_MOCK_LIST)


def _unzip(data: bytes) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            if not name.endswith("/"):
                files[name] = z.read(name)
    return files


def _entrypoint_from(files: dict[str, bytes]) -> str:
    """从 SKILL.md frontmatter 取 entrypoint（29.3 契约）；缺省回退 python3 run.py。"""
    md = files.get("SKILL.md", b"").decode("utf-8", "replace")
    for line in md.splitlines():
        if line.strip().startswith("entrypoint:"):
            return line.split(":", 1)[1].strip()
    return "python3 run.py"


async def download_skill_package(skill_key: str, version_no: int) -> dict[str, Any]:
    """取 Skill 包（真 ZIP 投递）：返回 {files, entrypoint, checksum}。checksum 供 run_skill 完整性校验。

    mock：合成可执行包；real：HTTP GET ZIP + 校验 `X-Checksum-SHA256`（未联环境 raise）。
    """
    if os.getenv("OPENOPS_SKILLHUB", "mock").lower() == "real":
        base = skillhub_base()
        if not base:
            raise RuntimeError("OPENOPS_SKILLHUB=real 需配 OPENOPS_SKILLHUB_BASE_URL（或 OPENOPS_MCPREGISTRY_BASE_URL，同 console 网关）")
        import httpx

        async with httpx.AsyncClient(timeout=30, verify=console_tls_verify(), trust_env=http_trust_env()) as cli:
            # 29.3 §2.5：flat `GET /skills/download?skill_id=&version=`。V1 省略 version → 下载 latest
            # （OpenOps 存 version_no(int) 无 semver，精确 pin 待 repo 穿透；latest + ZIP 字节 checksum 校验漂移即 fail-closed）。
            r = await cli.get(f"{base}{console_api_prefix()}/skills/download",
                              params={"skill_id": skill_key}, headers=_headers())
            raise_with_body(r)  # 非 2xx 带响应体前 300 字（401=cookie 失效）
            raw = r.content
            header_checksum = r.headers.get("X-Checksum-SHA256", "")
        if raw[:1] == b"<":  # 登录页/门户 HTML 而非 ZIP：cookie 失效——直接说清，别让 BadZipFile 迷惑定位
            raise RuntimeError(f"Skill 下载返回 HTML 而非 ZIP（cookie 失效或地址错）：{raw[:120]!r}")
        if raw[:1] in (b"{", b"["):  # JSON 信封而非裸 ZIP 字节：29.3 §2.5 约定直接回 ZIP，同网关 list 面
            # 却是 {code,message,data} 信封——对端若 download 也包信封，这里显式说清（带 code/message），
            # 拿到真实形状后再适配，不瞎猜 base64/download_url 分支
            raise RuntimeError(f"Skill 下载返回 JSON 信封而非 ZIP 字节（对端契约与 29.3 §2.5 不符）：{raw[:200]!r}")
        if not header_checksum:
            logging.getLogger("openops.skillhub").warning(
                "[skillhub] download %s 无 X-Checksum-SHA256 头，跳过传输校验（对端未实现该头）", skill_key)
        # 传输完整性（29.3 §2.5）：X-Checksum-SHA256 = 下载 ZIP **原始字节**的 sha256，非解包内容。
        if header_checksum and hashlib.sha256(raw).hexdigest() != header_checksum:
            raise RuntimeError("Skill 包传输校验失败：X-Checksum-SHA256 与 ZIP 字节不符")
        files = _unzip(raw)
        # 返回给执行面的 checksum 用 package_checksum（绑文件名，executor.run_skill 内部再校验一致性）。
        # 它与 Skill Hub 的 ZIP checksum 是两套算法、两个用途（传输 vs 执行面防篡改），勿混用。
        return {"files": files, "entrypoint": _entrypoint_from(files), "checksum": package_checksum(files)}

    return {"files": dict(_MOCK_FILES), "entrypoint": _MOCK_ENTRYPOINT, "checksum": MOCK_INSPECTION_CHECKSUM}
