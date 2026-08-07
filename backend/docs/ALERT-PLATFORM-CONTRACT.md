# 告警平台对接契约（OpenOps 7x24 告警接管 · v2 Kafka 版）

> 角色：告警平台为数据源（已存在），本契约由 OpenOps 定义、告警平台实现。
> **主链路 = Kafka topic 消费**（v2 起替代 HTTP 增量拉取；无带外对账通道，回补依赖
> retention 内重放——见 §2.4）。Phase 1 需就绪 **§2 topic + §3 详情接口**；§4 回写
> 先定契约、Phase 2 实现。
> OpenOps 侧对接代码：`backend/src/alerts/kafka_source.py`（消费）与
> `backend/src/infra/external/alert_platform_real.py`（详情）；联调自检 `check-net.py` ⑦。
> 所有时间 RFC3339 带时区；消息与响应 UTF-8 JSON。

## 1. 通用约定

- **鉴权**：
  - Kafka：为 OpenOps 签发消费账号（SASL，机制以对方集群为准，默认 PLAIN/SCRAM），
    授权 `Read` 目标 topic + 消费组 `openops-alert-takeover`（组名可配）。凭证轮换需
    新旧重叠窗口 ≥7 天。
  - 详情/回写 HTTP：`Authorization: Bearer <service-token>`，同样支持轮换重叠 ≥7 天。
    凭证仅代表 OpenOps 平台身份，不承载终端用户身份。
- **错误格式（HTTP 面）**：`{"error": {"code": "...", "message": "..."}}` + 标准状态码；
  `429` 必带 `Retry-After` 秒数。

## 2. Kafka topic（Phase 1 主链路）

### 2.1 Topic 与消息

| 项 | 约定 |
|---|---|
| topic | 建议 `ops-alert-changes`（最终名以对方命名规范为准，写入联调纪要） |
| 分区数 | 建议 ≥3（OpenOps 当前单消费者，分区数为未来扩展预留） |
| **key** | **`alert_id`**（必须）——保证同一告警的变更（firing→resolved）落同分区**有序**，update-log 语义依赖此项 |
| value | AlertDTO JSON（UTF-8，字段见 §2.2）；单条消息 ≤1MB |
| **retention** | **≥7 天（硬性条款）**——OpenOps 放弃带外对账通道后，漏消费/offset 事故的**唯一**回补手段是 retention 窗口内按时间戳 seek 重放 |
| 压缩 | 建议 producer 侧 lz4/zstd，不做 log compaction（需要完整变更史，非最终态） |
| 投递语义 | at-least-once（重复投递安全：OpenOps 按 fingerprint 幂等合并）；**禁止**有损的 at-most-once |

**变更流语义**：update-log——同一 alert 的每次状态/字段变化（含 firing→resolved）都发一条
新消息（`updated_at` 前进）。OpenOps 按 `(alert_id, updated_at)` 与 fingerprint 幂等消费，
乱序跨 alert 无所谓、同 alert 内靠 key 分区保序。

### 2.2 AlertDTO 字段（消息 value；必填 ✱）

| 字段 | 类型 | 说明 |
|---|---|---|
| alert_id ✱ | string | 全局唯一且稳定（分区 key；回写契约用它定位）；建议 `ALM-` 前缀编号 |
| fingerprint ✱ | string | 同类事件指纹（OpenOps 据此去重与风暴聚合；缺省时 OpenOps 按 source+title+labels 自算） |
| status ✱ | enum | `firing` \| `resolved` |
| severity ✱ | enum | `fatal`(致命) \| `critical`(严重) \| `warning`(普通) \| `info`(提示)——内部等级映射表附实现文档；**本期 OpenOps 仅消费前三档**（info 保留字段） |
| category ✱ | string | 告警类型（策略类型）：**本期消费 `MySQL` / `PGSQL` / `ADS Docker`**；枚举开放，其余值照收暂不参与规则匹配 |
| **strategy_name ✱** | string | **触发本告警的监控策略名**（如「MySQL 主从延迟监控」）——OpenOps 接管规则的核心匹配维度，须与告警平台策略管理的展示名一致 |
| **alert_object ✱** | string | **告警对象**：实例/主机/集群标识（如 `mysql-prod-03`）——清单展示列与搜索维度 |
| **detail_url ✱** | string | **告警平台详情页链接**——OpenOps 清单「告警编号」一律外跳此地址（缺失时前端退化为纯文本，视为对端缺陷） |
| title ✱ | string | ≤512B |
| description | string | ≤8KB |
| app_id ✱ | string | 与内网 APPID 对齐（oModel/工作空间同一口径）；无法归属填 `"unknown"` |
| labels ✱ | object | ≤64 键；key ≤128B、value ≤1KB（service/idc/env 等） |
| annotations | object | 大文本类（runbook_url、dashboard_url） |
| started_at ✱ | string | 首次触发时间 |
| resolved_at | string\|null | resolved 时非空 |
| updated_at ✱ | string | 本次变更时间 |
| source ✱ | string | 监控源标识（zabbix/prom/自研名） |

**消息示例**：

```json
// key: "ALM-20260730-000117"
{
  "alert_id": "ALM-20260730-000117",
  "fingerprint": "fp_mysql_prod03_replica_lag",
  "status": "firing",
  "severity": "fatal",
  "category": "MySQL",
  "strategy_name": "MySQL 主从延迟监控",
  "alert_object": "mysql-prod-03",
  "detail_url": "https://alert.example.internal/alarm/ALM-20260730-000117",
  "title": "MySQL 主库延迟>5s",
  "description": "pay-core 主从延迟 6.8s > 5s (5m)",
  "app_id": "APP-A",
  "labels": { "service": "pay-core", "idc": "sz-3", "env": "prod" },
  "annotations": { "runbook_url": "https://runbook.example.com/mysql-lag" },
  "started_at": "2026-07-30T10:00:30+08:00",
  "resolved_at": null,
  "updated_at": "2026-07-30T10:00:31+08:00",
  "source": "prom-internal"
}
```

### 2.3 Schema 演进

只允许**新增可选字段**（向后兼容）；改名/删字段/改枚举语义须走版本化 topic（`-v2` 后缀）
并保留旧 topic 双写 ≥30 天。OpenOps 消费端对未知字段忽略、对缺失必填字段记日志跳过
（不阻塞分区）。

### 2.4 消费与回补（OpenOps 侧承诺，供对方容量评估）

- 单消费组 `openops-alert-takeover`，当前单实例消费；批量 `max_records` 默认 200。
- **手动 commit、落库后提交**：at-least-once + fingerprint 幂等 = 恰好一次效果。
- 回补：事故后在 retention 窗口内按时间戳 `seek` 重放（重放无副作用）；因此 §2.1 的
  retention ≥7 天为硬性条款，缩短需提前 30 天知会。

## 3. 告警详情接口（HTTP，Phase 1）

`GET {BASE}/openapi/alerts/v1/alerts/{alert_id}`
`Authorization: Bearer <token>`

→ §2.2 全字段，另附可选 `events: [{at, type, note}]`（该告警升级/恢复轨迹）。
404 = 超出保留期或不存在。限流：对 OpenOps token 保障 ≥10 rps。

## 4. 结论回写（Phase 2，OpenOps → 告警平台；契约先行、暂不实现）

三个端点，鉴权同 §1 HTTP；全部以 `client_request_id` 幂等（24h 去重；对已处于目标态的
告警返回 200 幂等成功而非 409）：

```json
// 1) 认领：OpenOps 接管该告警时
POST /openapi/alerts/v1/alerts/{alert_id}:ack
{ "actor": "openops:agt_pay_fast_recovery",
  "comment": "已由感知快恢 Agent 接管，自动诊断中", "client_request_id": "crid_x1" }
→ { "alert_id": "ALM-20260730-000117", "acked": true }

// 2) 备注（诊断结论回流）
POST /openapi/alerts/v1/alerts/{alert_id}:annotate
{ "comment": "根因：CHG-88121 连接池 max 64→8 回退，建议回滚。",
  "links": [ { "type": "diagnosis", "title": "OpenOps 诊断会话",
               "url": "https://{openops-host}/agent-runs/run_xxx" } ],
  "client_request_id": "crid_x2" }
→ { "alert_id": "ALM-20260730-000117", "annotated": true }

// 3) 关闭
POST /openapi/alerts/v1/alerts/{alert_id}:close
{ "resolution": "fixed",   // fixed | auto_recovered | false_positive
  "comment": "已回滚 CHG-88121，指标恢复", "client_request_id": "crid_x3" }
→ { "alert_id": "ALM-20260730-000117", "status": "resolved" }
```

## 5. 联调核对单（给双方）

1. Kafka：topic 建立、分区/retention 确认（≥7d）、SASL 消费账号 + 消费组授权下发；
   OpenOps 配 `OPENOPS_ALERT=real` + `OPENOPS_ALERT_KAFKA_*` 六变量。
2. `check-net.py` ⑦ 通过（metadata/分区/末位 offset + 详情接口探测）。
3. **key=alert_id 抽检**：同一告警 firing→resolved 两条消息落同分区且有序。
4. severity 四档映射表评审；category 本期三类确认；strategy_name 与策略管理展示名
   一致性抽检；app_id 与内网 APPID 抽样比对；detail_url 可达性抽检。
5. 风暴演练：同 fingerprint 高频重复、同 app 多指纹并发，确认 OpenOps 去重/聚合/
   消费 lag 表现；演练中途重启 OpenOps 验证 offset 续传与幂等。
6. 回补演练：人为回拨消费组 offset 2 小时，确认重放零副作用。
