"""内网契约映射纯函数（29.11 Kafka 体 / 29.10 历史行 → 内部形状）。

样本取自 Obsidian 契约文档原文（脱敏值照抄）——映射表变更时这里是第一道回归。
无 DB、无 IO。
"""
from __future__ import annotations

import json

from infra.external.alert_inet_contract import (
    CST,
    LEVEL_TO_SEVERITY,
    SEVERITY_TO_LEVEL,
    map_history_row,
    map_kafka_alarm,
    to_iso8601,
)

# ---- 29.11 Kafka 消息体样本（文档原文，链接标记等噪声已去，字段一个不少） ----
KAFKA_SAMPLE = {
    "alarmCode": "20260618172920712nzw40229679e4014272aa51321aca27ae27",
    "alarmDesc": "(Group)队列可消费消息数，(Group)队列可消费消息数 > 1000000，连续2次真实值:(1407119.00Count,1830519.00Count)；",
    "alarmId": "20260618172920712nzw40229679e4014272aa51321aca27ae27",
    "alarmLevel": "3",
    "alarmOwnerLname": "xxx",
    "alarmTime": "2026-06-18 17:29:20.000",
    "alarmTimeStamp": 1781774960000,
    "alarmTitle": "【Kafka】metrics-gaia:(Group)队列可消费消息数过高",
    "apmExtendMap": {"ci_sub_type": "KafkaInstance", "ci_type": "Kafka",
                     "apm_object_name": "xxx", "metric": "", "apm_object_id": "xxx"},
    "appIdList": ["com.huawei.so.monitoring.sentry"],
    "applications": "com.huawei.so.monitoring.sentry",
    "assign": "", "assignLName": "", "assignW3": "",
    "ciName": "metrics-gaia",
    "ciPolicyId": "2713",
    "dataCreateTimeStamp": 1781774960731,
    "dataModifyTimeStamp": 1786000396732,
    "dataSource": "中间件",
    "displayName": "metrics-gaia",
    "enterpriseId": "11111111111111111111111111111111",
    "eventTool": "慧眼-资源监控-中间件-Kafka",
    "extraInfo": [{"instanceName": "topic:GAIA_INFLUXDB_MONGODB;group:679345_3",
                   "regionName": "xxx", "env": "pro_live_01", "thresholdTimes": 0,
                   "productInfo": "xxx"}],
    "incidentTime": 1781774960000,
    "instance": "04c9042d-4135-4caa-83fb-7aa9ac16e35d",
    "isAdmin": "N", "isFilter": "Y", "is_assign": "N",
    "metric": "groups_topics_unconsume_num",
    "metricName": "(Group)队列可消费消息数",
    "moHrn": "xxx", "moSubtype": "KafkaInstance", "moType": "Kafka",
    "modifyTime": "2026-08-06 15:13:16.797",
    "modifyTimeStamp": 1786000396797,
    "monitorTool": "PROMETHEUS_KAFKA_KAFKAINSTANCE",
    "mtta": "", "mttr": "", "notify_type": "2,4",
    "policyId": "2713",
    "prodTreeId": 12237,
    "prodTreeList": [{"prod_tree_id_0": 37983, "prod_tree_id_1": 1001,
                      "prod_tree_id_2": 12237, "prod_tree_name_2": "xxx"}],
    "projectName": "基础资源监控",
    "responsePerson": "xxx",
    "sloId": "",
    "source": "Middleware",
    "sourceCategory": "中间件",
    "sourceCategoryCode": "center",
    "status": "1",
    "tag": "topic:GAIA_INFLUXDB_MONGODB;group:679345_3",
    "upsertTime": 1786000396732,
}

# ---- 29.10 历史查询响应行样本（文档原文） ----
HISTORY_ROW = {
    "alarmCode": "axxx",
    "alarmDesc": "异常检测消费告警数据kakfa出现消费积压，(Group)Topic的未消费数量 > 0，连续3次真实值:(46822.00Count,58190.00Count,247.00Count)；",
    "alarmLevel": "1",
    "alarmTime": "2026-08-06T21:59:29.000+00:00",
    "alarmTitle": "【Kafka】axxx:(Group)Topic的未消费数量过高",
    "dataSource": "中间件",
    "projectId": "00000000000000000000000000000144",
    "assign": "", "assignLName": "", "assignW3": "",
    "ciName": "observ-monit-rule-kwe-kafka-01",
    "enterpriseId": "88888888888888888888888888888888",
    "incidentClosedTime": "1786071740000",
    "metric": "groups_topics_unconsume_num",
    "metricName": "(Group)Topic的未消费数量",
    "moHrn": "axxx", "moSubtype": "KafkaInstance", "moType": "Kafka",
    "sloId": "", "status": "5",
    "responsePerson": "axxx", "alarmOwnerLname": "axxx",
    "isAi": "", "policyId": "318", "ciPolicyId": "318",
    "monitorTool": "PROMETHEUS_KAFKA_KAFKAINSTANCE",
    "itsmTicket": "", "projectName": "智能监控告警(多租)",
    "duration": 18171000,
    "tag1": "topic:alarm_ori_list;group:aiops_anomaly_detection_beta_kafka4",
    "dataCreateTime": "2026-08-07 05:59:29",
    "instance": "5a6439d3-4c8f-47dc-98d7-8a1c4e58c12c",
}


# ============================ to_iso8601 三形态 ============================


def test_to_iso8601_epoch_ms():
    got = to_iso8601(1781774960000)
    assert got == "2026-06-18T17:29:20+08:00"  # 与样本 alarmTime 互证（内网确为北京时间口径）
    assert to_iso8601("1781774960000") == got  # 字符串形态等价


def test_to_iso8601_naive_string_assumes_cst():
    assert to_iso8601("2026-06-18 17:29:20.000") == "2026-06-18T17:29:20+08:00"


def test_to_iso8601_keeps_explicit_offset():
    # 历史行 alarmTime 带 +00:00：原样保留偏移，不做二次换算（R2 联调核对真伪）
    assert to_iso8601("2026-08-06T21:59:29.000+00:00") == "2026-08-06T21:59:29+00:00"


def test_to_iso8601_garbage_returns_none():
    for bad in ("", None, "not-a-date", "123", {"x": 1}):
        assert to_iso8601(bad) is None


# ============================ map_kafka_alarm ============================


def test_kafka_sample_maps_all_key_fields():
    dto = map_kafka_alarm(KAFKA_SAMPLE)
    assert dto["alert_id"] == KAFKA_SAMPLE["alarmId"]
    assert dto["fingerprint"] == KAFKA_SAMPLE["alarmId"]  # 同 alarmId 重推 = upsert 天然指纹
    assert dto["status"] == "firing"                      # "1" 未关闭
    assert dto["severity"] == "warning"                   # "3" 普通
    assert dto["category"] == "Kafka"                     # moType 原样透传（开放枚举）
    assert dto["title"] == KAFKA_SAMPLE["alarmTitle"]
    assert dto["description"] == KAFKA_SAMPLE["alarmDesc"]
    assert dto["strategy_name"] == "(Group)队列可消费消息数"  # metricName
    assert dto["alert_object"] == "metrics-gaia"          # ciName
    assert dto["app_id"] == "com.huawei.so.monitoring.sentry"  # appIdList[0]
    assert dto["started_at"] == "2026-06-18T17:29:20+08:00"    # epoch 优先
    assert dto["source"] == "inet"
    assert "detail_url" not in dto  # 内网无详情外链：不产出该键，UI 编号列退纯文本


def test_kafka_labels_whitelist_exactly():
    dto = map_kafka_alarm(KAFKA_SAMPLE)
    assert dto["labels"] == {
        "alarm_level": "3", "alarm_status": "1", "mo_subtype": "KafkaInstance",
        "metric": "groups_topics_unconsume_num", "policy_id": "2713",
        "ci_policy_id": "2713", "data_source": "中间件", "source_category": "中间件",
        "monitor_tool": "PROMETHEUS_KAFKA_KAFKAINSTANCE",
        "event_tool": "慧眼-资源监控-中间件-Kafka",
        "enterprise_id": "11111111111111111111111111111111",
    }
    # 大对象有意丢弃：prodTreeList / extraInfo 绝不进 labels（防 64 键/1000B 裁剪噪声）
    flat = json.dumps(dto["labels"], ensure_ascii=False)
    assert "prod_tree" not in flat and "instanceName" not in flat


def test_kafka_annotations_app_id_list_roundtrip():
    dto = map_kafka_alarm({**KAFKA_SAMPLE,
                           "appIdList": ["com.huawei.a", "00000000000000000000000000000425"]})
    assert dto["app_id"] == "com.huawei.a"  # 首元素参与本地匹配（R9 取舍）
    assert json.loads(dto["annotations"]["app_id_list"]) == [
        "com.huawei.a", "00000000000000000000000000000425"]


def test_kafka_status_and_level_word_tables():
    assert map_kafka_alarm({**KAFKA_SAMPLE, "status": "5"})["status"] == "resolved"
    for mid in ("2", "3", "4"):  # 中间态全归 firing（误判方向安全，R5）
        assert map_kafka_alarm({**KAFKA_SAMPLE, "status": mid})["status"] == "firing"
    assert map_kafka_alarm({**KAFKA_SAMPLE, "alarmLevel": "1"})["severity"] == "fatal"
    assert map_kafka_alarm({**KAFKA_SAMPLE, "alarmLevel": "0"})["severity"] == "warning"  # SLO 降档
    assert map_kafka_alarm({**KAFKA_SAMPLE, "alarmLevel": "9"})["severity"] == "warning"
    # 原始级别恒在 labels 可追溯（零特判方案）
    assert map_kafka_alarm({**KAFKA_SAMPLE, "alarmLevel": "0"})["labels"]["alarm_level"] == "0"


def test_kafka_fallbacks():
    no_id = {k: v for k, v in KAFKA_SAMPLE.items() if k != "alarmId"}
    assert map_kafka_alarm(no_id)["alert_id"] == KAFKA_SAMPLE["alarmCode"]  # alarmCode 兜底
    no_ci = {k: v for k, v in KAFKA_SAMPLE.items() if k != "ciName"}
    assert map_kafka_alarm(no_ci)["alert_object"] == "metrics-gaia"  # displayName 兜底
    no_ts = {k: v for k, v in KAFKA_SAMPLE.items() if k != "alarmTimeStamp"}
    assert map_kafka_alarm(no_ts)["started_at"] == "2026-06-18T17:29:20+08:00"  # alarmTime 兜底
    assert map_kafka_alarm({**KAFKA_SAMPLE, "appIdList": []})["app_id"] == ""


# ============================ map_history_row ============================


def test_history_row_closed_state():
    row = map_history_row(HISTORY_ROW)
    assert row["alert_no"] == "axxx"                    # 历史行无 alarmId，用 alarmCode
    assert row["appid"] == "00000000000000000000000000000144"  # 单值 projectId
    assert row["alert_status"] == "closed"              # status "5"
    assert row["severity"] == "fatal"                   # "1"
    assert row["category"] == "Kafka"
    assert row["started_at"] == "2026-08-06T21:59:29+00:00"    # 保留原偏移
    assert row["ended_at"] == to_iso8601(1786071740000)        # incidentClosedTime（epoch ms 串）
    assert row["duration_s"] == 18171000                # 数字原样；单位联调确认（R11）
    assert row["takeover_status"] == "none" and row["run_id"] is None
    assert row["detail_url"] == "" and row["incident_id"] is None


def test_history_row_open_state():
    row = map_history_row({**HISTORY_ROW, "status": "1", "duration": "abc"})
    assert row["alert_status"] == "unassigned"
    assert row["ended_at"] is None                      # 未关闭不取 incidentClosedTime
    assert row["duration_s"] is None                    # 非纯数字置 None


# ============================ 词表一致性 ============================


def test_level_tables_are_inverse():
    for level, sev in LEVEL_TO_SEVERITY.items():
        assert SEVERITY_TO_LEVEL[sev] == int(level)
    assert CST.utcoffset(None).total_seconds() == 8 * 3600
