"""内网告警契约 → 内部形状的**唯一翻译点**（纯函数零 IO）。

上游两份契约（Obsidian 29.11 / 29.10）：
- Kafka 消息体：大而平的 JSON（alarmId/alarmLevel/alarmTitle/moType/ciName/metricName/
  status/appIdList/alarmTimeStamp…）→ `map_kafka_alarm` 译成内部 AlertDTO，交 ingest。
- 历史告警查询（alarm_list）响应行：与 Kafka 体几乎同名但无 alarmId（用 alarmCode）、
  无 appIdList（单值 projectId）→ `map_history_row` 译成预览行（与 AlertEventWire 同键）。

为什么放 infra/external 而不是 alerts/ 切片下：`alert_platform_real.list_history` 要用
`map_history_row`，core→alerts 方向 import 被切片铁律禁止；放这里 alerts→infra 合法
（kafka_source 已同向依赖 alert_platform_client），real 客户端同包直用。

词表取舍（联调待确认项见 docs/ALERT-PLATFORM-CONTRACT.md v3 的 R1–R12）：
- 级别 "0"(SLO)/未知 → warning（原始值恒存 labels.alarm_level，可追溯零特判）；
- 状态只认 "5"=已关闭 → resolved，"1"–"4" 全归 firing——误把中间态当 firing 最多多接管
  一次（指纹去重兜底），误归 resolved 会直接丢诊断（不可逆），方向性安全；
- fingerprint ← alarmId：内网同一告警重复推送=同 alarmId 的 upsert，天然指纹；同一问题
  恢复后再触发是新 alarmId → 新事件行，风暴聚合仍由 group_key 承接；
- prodTreeList/extraInfo 大对象**有意丢弃**（防挤爆 labels 的 64 键/1000B 裁剪）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

# 内网无时区时间串的钦定口径（R1：联调用 alarmTimeStamp 对表核实；不符只改这一处）
CST = timezone(timedelta(hours=8))

# alarmLevel（字符串数字）→ 内部四档；"0"=SLO 与未知值一律降 warning
LEVEL_TO_SEVERITY = {"1": "fatal", "2": "critical", "3": "warning", "4": "info"}
# 历史查询入参 alarmLevels 的反查表（内部四档 → 内网数字）
SEVERITY_TO_LEVEL = {"fatal": 1, "critical": 2, "warning": 3, "info": 4}

# labels 白名单（低基数、可进 label_selectors 匹配；上游字段名 → 内部键）。
# 全部转 str 存放；缺失/空值不写键。
_KAFKA_LABEL_MAP = {
    "alarmLevel": "alarm_level",        # 原始级别数字（SLO"0" 由此可追溯）
    "status": "alarm_status",           # 原始状态（"2"/"3"/"4" 中间态语义联调后备查，R5）
    "moSubtype": "mo_subtype",
    "metric": "metric",
    "policyId": "policy_id",
    "ciPolicyId": "ci_policy_id",
    "dataSource": "data_source",
    "sourceCategory": "source_category",
    "monitorTool": "monitor_tool",
    "eventTool": "event_tool",
    "enterpriseId": "enterprise_id",
}


def to_iso8601(value: Any, *, assume: timezone = CST) -> str | None:
    """内网三种时间形态 → RFC3339；解析失败返回 None（调用方回退下一候选字段）。

    ① epoch 毫秒（int / 纯数字 str，如 alarmTimeStamp/dataCreateTimeStamp）→ +08:00 ISO；
    ② "yyyy-MM-dd HH:mm:ss[.fff]" 无时区（Kafka 体 alarmTime / 历史行 dataCreateTime）
      → 按 `assume`（默认北京时间）补时区；
    ③ ISO 带时区（历史行 alarmTime "…+00:00"）→ 原样保留偏移（R2：真伪联调核对）。
    """
    if value in (None, ""):
        return None
    s = str(value).strip()
    if s.isdigit() and len(s) >= 12:  # 13 位 epoch ms（12 位下限防误吞短数字串）
        try:
            return datetime.fromtimestamp(int(s) / 1000, tz=CST).isoformat()
        except (ValueError, OverflowError, OSError):
            return None
    try:
        dt = datetime.fromisoformat(s)  # py3.11 原生接受空格分隔与 ".000" 毫秒
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=assume)).isoformat()


def map_kafka_alarm(raw: dict[str, Any]) -> dict[str, Any]:
    """29.11 Kafka 消息体 → 内部 AlertDTO（`ingest._normalize` 的入参形状）。

    产出**不含 detail_url 键**（内网体没有详情外链；event_row_out 缺省空串，
    前端编号列已实现空串退纯文本）。app_id 取 appIdList 首元素参与本地匹配/可见性
    （全列表存 annotations.app_id_list——本期 UI 未暴露 appids 匹配维度，取舍见 R9）。
    """
    alarm_id = str(raw.get("alarmId") or raw.get("alarmCode") or "")
    app_ids = [str(a) for a in (raw.get("appIdList") or []) if a]
    labels = {v: str(raw[k]) for k, v in _KAFKA_LABEL_MAP.items()
              if raw.get(k) not in (None, "")}
    annotations: dict[str, str] = {}
    if app_ids:
        annotations["app_id_list"] = json.dumps(app_ids, ensure_ascii=False)
    if raw.get("displayName") and raw.get("displayName") != raw.get("ciName"):
        annotations["display_name"] = str(raw["displayName"])
    if raw.get("alarmCode") and str(raw["alarmCode"]) != alarm_id:
        annotations["alarm_code"] = str(raw["alarmCode"])
    return {
        "alert_id": alarm_id,
        "fingerprint": alarm_id,  # 空串时 ingest 走 sha256(source|title|labels) 兜底
        "status": "resolved" if str(raw.get("status") or "") == "5" else "firing",
        "severity": LEVEL_TO_SEVERITY.get(str(raw.get("alarmLevel") or ""), "warning"),
        "category": str(raw.get("moType") or ""),  # 开放枚举原样透传（MySQL/PostgreSQL/Docker/…）
        "title": str(raw.get("alarmTitle") or ""),
        "description": str(raw.get("alarmDesc") or ""),
        "strategy_name": str(raw.get("metricName") or ""),  # R10：与「监控策略名」的关系联调核对
        "alert_object": str(raw.get("ciName") or raw.get("displayName") or ""),
        "app_id": app_ids[0] if app_ids else "",
        "labels": labels,
        "annotations": annotations,
        "started_at": to_iso8601(raw.get("alarmTimeStamp")) or to_iso8601(raw.get("alarmTime")),
        "source": "inet",
    }


def map_history_row(row: dict[str, Any]) -> dict[str, Any]:
    """29.10 alarm_list 响应 datas[] 行 → 预览行（与 GET /alerts/events 行口径同键）。

    键集对齐前端 AlertEventWire，规则编辑器第二步七列零改渲染。历史行无 alarmId，
    编号用 alarmCode；"已分派"对应的 status 值文档未给（R5），暂只分 closed/unassigned。
    """
    closed = str(row.get("status") or "") == "5"
    duration = str(row.get("duration") or "")
    return {
        "alert_no": str(row.get("alarmCode") or ""),
        "category": str(row.get("moType") or ""),
        "alert_object": str(row.get("ciName") or row.get("displayName") or ""),
        "appid": str(row.get("projectId") or ""),  # 历史行是单值 projectId
        "title": str(row.get("alarmTitle") or ""),
        "description": str(row.get("alarmDesc") or ""),
        "alert_status": "closed" if closed else "unassigned",
        "severity": LEVEL_TO_SEVERITY.get(str(row.get("alarmLevel") or ""), "warning"),
        "takeover_status": "none",
        "agent_result": None,
        "user_feedback": None,
        "feedback_note": "",
        "detail_url": "",
        "incident_id": None,
        "run_id": None,
        "run_clickable": False,
        "started_at": to_iso8601(row.get("alarmTimeStamp")) or to_iso8601(row.get("alarmTime")),
        "ended_at": to_iso8601(row.get("incidentClosedTime")) if closed else None,
        "duration_s": int(duration) if duration.isdigit() else None,  # 单位联调确认（R11）
        "seen_count": 1,
    }
