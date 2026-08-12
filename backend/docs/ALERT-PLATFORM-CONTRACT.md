# 告警平台对接契约 v3（内网真实契约版，2026-08-09）

> v2（自拟契约）已废弃。v3 以内网两份真实文档为准：**29.11 告警 Kafka 消息体**、
> **29.10 历史告警查询接口**（Obsidian `学习/OpenOps/`）。本文件是映射的**单一事实源**，
> 代码实现在 `src/infra/external/alert_inet_contract.py`（纯函数，Kafka 消费与历史查询共用词表），
> 逐字段回归在 `tests/alerts/test_inet_contract.py`（样本取契约文档原文）。

## 0. 架构：映射层放哪

```
Kafka 消息(29.11 体) ─→ alerts/kafka_source._parse ─┐
                          （探测 alarmId/alarmCode）  ├─→ map_kafka_alarm ─→ 内部 AlertDTO ─→ ingest
内部形状消息（mock/测试注入）──────────────────────────┘        （原样透传）

规则预览 ─→ service.history_preview ─→ client.list_history ─→ real: POST alarm_list → map_history_row
                                              └────────────→ mock: 内部形状样本（不认识内网体）
```

- **内部 AlertDTO 契约不变**：mock、ingest、matcher、全部 `_inject` 测试零改动。
- 双格式缝：消息体含 `alarmId`/`alarmCode` 键 → 内网映射；否则视为内部 DTO 透传。

## 1. 环境与配置

| env | 说明 |
|---|---|
| `OPENOPS_ALERT=real` | 启用真实对接（Kafka 消费 + 历史查询）；默认 mock |
| `OPENOPS_ALERT_KAFKA_BOOTSTRAP/TOPIC/GROUP/USERNAME/PASSWORD/SECURITY_PROTOCOL/SASL` | Kafka 六变量（GROUP 默认 openops-alert-takeover） |
| `OPENOPS_ALERT_QUERY_URL` | **历史查询完整 URL**（含路径；sit/beta/pro 路径不同，不做拼装）：sit `http://wesee.console.hissit/observe/unifieduery/api/v1/{eid}/{sid}/alarm_list_for_sreagent`、pro `https://console.his-op/...alarm_list`（R7：三环境 URL 联调时抄部署手册） |
| `OPENOPS_ALERT_TOKEN` | Bearer token（Kafka 详情/历史查询共用；R4：对端若要 cookie/自定义头，改 `alert_platform_real.list_history` headers 一处） |
| `OPENOPS_ALERT_ENTERPRISE_ID` | 可选；历史查询 body.enterpriseId（对端文档 2026-08-09 已改非必填，缺省不传） |

## 2. Kafka 消息体（29.11）→ 内部 AlertDTO 映射表

| 内部字段 | 取自 | 规则 |
|---|---|---|
| alert_id | `alarmId` → `alarmCode` | 回退链；两者都缺 → 空串（ingest 兜底） |
| fingerprint | `alarmId` | 同 alarmId 重复推送 = upsert（seen_count++）；跨 alarmId 不去重，风暴由 group_key 聚合承接 |
| status | `status` | **"5"→resolved，其余（"1"–"4"）全归 firing**——误当 firing 可被去重兜底，误归 resolved 丢诊断不可逆（R5：中间态语义联调后细分） |
| severity | `alarmLevel` | "1"→fatal "2"→critical "3"→warning "4"→info；**"0"(SLO)/未知→warning**；原始值恒存 `labels.alarm_level` |
| category | `moType` | 原样透传（开放枚举：MySQL/PostgreSQL/OpenGauss/Docker/Kafka/…）；**UI 模板三类已对齐该词表** |
| title / description | `alarmTitle` / `alarmDesc` | 直取 |
| strategy_name | `metricName` | R10：与本地「监控策略名」不是同一词表——存量勾选 strategies 的规则将失配，见迁移 SQL 可选段 |
| alert_object | `ciName` → `displayName` | 回退链 |
| app_id | `appIdList[0]` | 元素=omodel projectId（包名风格与 32 位 hex 混合口径，纯字符串比较）；**全列表存 `annotations.app_id_list`**（R9：本地 appids 匹配/可见性仅首元素参与；本期 UI 未暴露该维度） |
| labels | 白名单 11 键 | alarm_level/alarm_status/mo_subtype/metric/policy_id/ci_policy_id/data_source/source_category/monitor_tool/event_tool/enterprise_id；**有意丢弃** prodTreeList/extraInfo（大对象防 64 键裁剪噪声）、isFilter/isAdmin、三个 modify 时间戳 |
| annotations | 派生三键 | app_id_list（JSON 数组串）、display_name（≠ciName 时）、alarm_code（≠alert_id 时）；**无 detail_url**（内网无详情外链，UI 编号列退纯文本） |
| started_at | `alarmTimeStamp` → `alarmTime` | epoch ms 优先；无时区串按 **+08:00**（R1，改 `CST` 常量一处） |
| source | 固定 `"inet"` | 参与 fingerprint fallback 的去重域 |

## 3. 历史告警查询（29.10 alarm_list）

**请求**（`list_history` 内部词表 → wire 翻译）：POST `OPENOPS_ALERT_QUERY_URL`，body：
`startTime/endTime`（`"yyyy-MM-dd HH:mm:ss"` 北京时间，R3）、`moTypeList`←categories、
`alarmLevels`←severities 数字反查（R8：SLO=0 是否收待确认）、`projectIds`←**omodel 实时探询**（`scope_service.peek_effective_appids`：override 缝→30s 缓存→workspace projects 解析；**必传**——拿不到时不打请求直接本地降级）、`pageNo/pageSize`、`enterpriseId`（env 有才带）。

**响应**：`{status:"OK", data:{datas:[...]}, message}`；`status!="OK"` → AlertPlatformError("http")。
行 → 预览行（`map_history_row`，与 `GET /alerts/events` 行口径同键）：

| 预览行 | 取自 | 规则 |
|---|---|---|
| alert_no | `alarmCode` | 历史行**无 alarmId** |
| enterprise_id | `enterpriseId` | 展示列 + 跳转分发键（32×8=OP、32×1=KWE） |
| detail_url | 拼接 | `alarm_detail_url`：按企业分发 `ALARM_OP_URL`/`ALARM_KWE_URL`（裸名 env）+ `?alarmCode=`；未命中/未配→空串退纯文本。本地降级行同规则（labels.enterprise_id + external_alert_id） |
| appid | `projectId` | 历史行是单值 |
| alert_status | `status` | "5"→closed，其余→unassigned（"已分派"值待 R5） |
| ended_at | `incidentClosedTime` | 仅 closed 时取（epoch ms 串） |
| duration_s | `duration` | 非纯数字→None（R11：单位待确认） |
| 其余 | 同 §2 词表 | takeover/run 等本地字段置空占位 |

**消费方**：`GET /alerts/history-preview`（规则编辑器第二步）——平台主路径失败自动降级本地
`sre_alert_event`（响应 `source: platform|local_fallback`，前端提示数据来源）。

## 4. 回写接口

内网暂无对应端点（v2 自拟的 ack/comment 回写作废）。诊断结论回填告警平台待对方提供接口后另立契约。

## 5. 联调待确认清单（R1–R12）

| # | 事项 | 现方案 | 改动点 |
|---|---|---|---|
| R1 | Kafka `alarmTime` 无时区 | 按 +08:00 | `alert_inet_contract.CST` |
| R2 | 历史行 alarmTime "+00:00" 真伪 | 保留原偏移 | 同上 |
| R3 | startTime 服务端时区解释 | 按北京时间发 | `list_history` astimezone |
| R4 | 鉴权 header | Bearer OPENOPS_ALERT_TOKEN | real.list_history headers |
| R5 | status 2/3/4 语义、"已分派"值 | 全归 firing / unassigned | 词表 + map_history_row |
| R6 | 响应有无 total | 缺省=本页行数 | real.list_history 取数 |
| R7 | 三环境完整 URL | env 化 | 部署手册 |
| R8 | alarmLevels 是否收 0（SLO） | UI 三档只传 [1,2,3] | SEVERITY_TO_LEVEL |
| R9 | appIdList 仅 [0] 参与本地匹配 | 接受（UI 未暴露 appids 维度） | 全列表在 annotations 可升级 |
| R10 | metricName vs 监控策略名 | 存量 strategies 失配 | 迁移 SQL 可选段（清空 strategies） |
| R11 | duration 单位 | 非纯数字置 None | map_history_row |
| R12 | Kafka 是否推 status=5；大消息内存量级 | 风暴演练观测 | alert_pull_batch_limit 可调 |
| R13 | ~~projectIds 等四选一必填~~（2026-08-10 内网实证 `[Required]:必须输入应用或产品或子产品或模块或Hrn列表`） | **已解决**：peek 实时取 projectIds，空则本地降级不打请求 | scope_service.peek_effective_appids |

**联调日自检**：`OPENOPS_ALERT=real` + 全量 env → `python check-net.py` ⑦ 两段 ✅
→ 界面规则编辑器第二步看 `source:"platform"` → 临时改错 QUERY_URL 验证 `local_fallback` 降级提示。
