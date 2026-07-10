"""Skill Hub client：source=openops 资产拉取 + Skill 包投递（29.3 契约面，ASSET-001 / SKILL-*）。

- `list_skills`：对账用资产清单（mock 硬编码；真 Skill Hub 见 29.3 `GET /skills`）。
- `download_skill_package`：真 ZIP 投递（C1）。`OPENOPS_SKILLHUB=mock(默认)|real` 切换；
  mock 合成**可执行**的真包（SKILL.md frontmatter + entrypoint 脚本），供 run_skill 端到端；
  real 变体经 29.3 `GET /skills/{id}/versions/{v}/download` 取 ZIP，按响应头 `X-Checksum-SHA256`
  校验（未联真环境时 raise，由调用方收口）。
"""
from __future__ import annotations

import hashlib
import io
import os
import zipfile
from typing import Any

from domain.skill_package import package_checksum

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


async def list_skills(user_id: str) -> list[dict[str, Any]]:
    """对账用资产清单。OPENOPS_SKILLHUB=real 时经 29.3 GET /skills?source=openops 拉取（未联环境 raise）。"""
    if os.getenv("OPENOPS_SKILLHUB", "mock").lower() == "real":
        base = os.getenv("OPENOPS_SKILLHUB_BASE_URL")
        if not base:
            raise RuntimeError("OPENOPS_SKILLHUB=real 需配 OPENOPS_SKILLHUB_BASE_URL（29.3 Skill Hub 未联）")
        import httpx

        async with httpx.AsyncClient(timeout=15) as cli:
            r = await cli.get(f"{base}/skills", params={"source": "openops", "user_id": user_id})
            r.raise_for_status()
            return r.json().get("data", r.json())
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
        base = os.getenv("OPENOPS_SKILLHUB_BASE_URL")
        if not base:
            raise RuntimeError("OPENOPS_SKILLHUB=real 需配 OPENOPS_SKILLHUB_BASE_URL（29.3 Skill Hub 未联）")
        import httpx

        async with httpx.AsyncClient(timeout=30) as cli:
            r = await cli.get(f"{base}/skills/{skill_key}/versions/{version_no}/download")
            r.raise_for_status()
            raw = r.content
            header_checksum = r.headers.get("X-Checksum-SHA256", "")
        # 传输完整性（29.3 §2.5）：X-Checksum-SHA256 = 下载 ZIP **原始字节**的 sha256，非解包内容。
        if header_checksum and hashlib.sha256(raw).hexdigest() != header_checksum:
            raise RuntimeError("Skill 包传输校验失败：X-Checksum-SHA256 与 ZIP 字节不符")
        files = _unzip(raw)
        # 返回给执行面的 checksum 用 package_checksum（绑文件名，executor.run_skill 内部再校验一致性）。
        # 它与 Skill Hub 的 ZIP checksum 是两套算法、两个用途（传输 vs 执行面防篡改），勿混用。
        return {"files": files, "entrypoint": _entrypoint_from(files), "checksum": package_checksum(files)}

    return {"files": dict(_MOCK_FILES), "entrypoint": _MOCK_ENTRYPOINT, "checksum": MOCK_INSPECTION_CHECKSUM}
