"""Skill Hub client（mock）：source=openops 资产拉取（29.3 契约面，ASSET-001）。"""
from __future__ import annotations

from typing import Any


async def list_skills(user_id: str) -> list[dict[str, Any]]:
    return [
        {
            "skill_key": "inspection",
            "display_name": "巡检 inspection",
            "source": "openops",
            "source_type": "platform",
            "version_no": 2,
            "checksum_sha256": "c0ffee" * 10 + "beef",
            "status": "active",
        }
    ]
