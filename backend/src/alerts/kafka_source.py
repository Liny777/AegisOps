"""告警平台 Kafka 消费主链路（`OPENOPS_ALERT=real` 档；契约 §2）。

- aiokafka 惰性导入（pyproject 可选 extra `[kafka]`）：未安装/缺配 → AlertPlatformError("config")
  fail-closed，由 alerts.start() 捕获打启动警告，绝不静默降级。
- **先落库后 commit**（手动位点）：批级异常不 commit → 消息重投；at-least-once + 指纹幂等
  = 恰好一次效果。坏 JSON 消息记日志跳过（不阻塞分区）。
- `consume_loop(consumer=...)` 的 consumer 参数是**测试注入缝**：单测传假对象
  （async getmany/commit/start/stop），不碰真 Kafka。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import Counter
from typing import Any

from infra.external.alert_platform_client import AlertPlatformError

log = logging.getLogger("openops.alerts.kafka")

GROUP_DEFAULT = "openops-alert-takeover"


def kafka_config() -> dict[str, Any]:
    """env 装配 + fail-closed 校验（start_background 先同步调它，缺配在启动期就可见）。"""
    bootstrap = os.environ.get("OPENOPS_ALERT_KAFKA_BOOTSTRAP", "").strip()
    topic = os.environ.get("OPENOPS_ALERT_KAFKA_TOPIC", "").strip()
    if not bootstrap or not topic:
        raise AlertPlatformError(
            "config",
            "OPENOPS_ALERT=real 需配 OPENOPS_ALERT_KAFKA_BOOTSTRAP 与 _KAFKA_TOPIC（契约 §2）")
    cfg: dict[str, Any] = {
        "bootstrap_servers": bootstrap,
        "topic": topic,
        "group_id": os.environ.get("OPENOPS_ALERT_KAFKA_GROUP", GROUP_DEFAULT).strip() or GROUP_DEFAULT,
    }
    username = os.environ.get("OPENOPS_ALERT_KAFKA_USERNAME", "").strip()
    if username:
        cfg.update({
            "security_protocol": os.environ.get("OPENOPS_ALERT_KAFKA_SECURITY_PROTOCOL",
                                                "SASL_PLAINTEXT").strip(),
            "sasl_mechanism": os.environ.get("OPENOPS_ALERT_KAFKA_SASL", "PLAIN").strip(),
            "sasl_plain_username": username,
            "sasl_plain_password": os.environ.get("OPENOPS_ALERT_KAFKA_PASSWORD", ""),
        })
    return cfg


def build_consumer() -> Any:
    try:
        from aiokafka import AIOKafkaConsumer  # 惰性：仅 real 档需要
    except ModuleNotFoundError as e:
        raise AlertPlatformError(
            "config", "aiokafka 未安装——`pip install -e '.[kafka]'`（可选 extra）") from e
    cfg = kafka_config()
    topic = cfg.pop("topic")
    return AIOKafkaConsumer(
        topic,
        enable_auto_commit=False,      # 手动位点：先落库后 commit
        auto_offset_reset="latest",    # 首启不回灌历史（对齐原 HTTP 游标空=从现在开始的语义）
        **cfg,
    )


def _parse(value: bytes | str) -> dict[str, Any] | None:
    """消息体 → 内部 AlertDTO。双格式缝：内网 29.11 体（探测 alarmId/alarmCode 键）走
    `alert_inet_contract.map_kafka_alarm`；否则视为内部 DTO 原样透传（mock/测试注入缝，
    也给未来第二上游留位）。坏 JSON / 映射炸均返回 None，由调用方计数跳过。"""
    try:
        body = json.loads(value if isinstance(value, str) else value.decode("utf-8"))
        if not isinstance(body, dict):
            return None
        if "alarmId" in body or "alarmCode" in body:  # 内网真实契约体
            from infra.external import alert_inet_contract

            return alert_inet_contract.map_kafka_alarm(body)
        return body
    except Exception:  # noqa: BLE001 —— 坏消息跳过不阻塞分区
        return None


# 消费健康心跳（admin overview 消费；2026-08-11 观测补强——此前 real 档消费死活无处可看）。
# 进程内单消费循环，普通 dict 无并发写者；reset 供测试隔离。
health: dict[str, Any] = {"started_at": None, "last_batch_at": None, "last_counters": None,
                          "batches": 0, "consumed": 0, "skipped_bad": 0}


def reset_health() -> None:
    health.update({"started_at": None, "last_batch_at": None, "last_counters": None,
                   "batches": 0, "consumed": 0, "skipped_bad": 0})


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


async def consume_loop(consumer: Any | None = None) -> None:
    """消费循环：getmany → ingest_batch → commit。CancelledError 优雅退出；
    其余异常记日志退避 1s 继续（不 commit 的批次由 Kafka 重投）。"""
    from alerts import ingest, service

    own = consumer is None
    if own:
        consumer = build_consumer()
        await consumer.start()
        log.warning("[alerts][kafka] 消费已启动 group=%s", kafka_config()["group_id"])
    health["started_at"] = _now_iso()
    try:
        while True:
            try:
                cfg = await service.get_config()
                if not cfg["alert_enabled"]:
                    await asyncio.sleep(3)  # 全局热停：不拉不 commit，恢复后续传
                    continue
                batches = await consumer.getmany(
                    timeout_ms=1000, max_records=int(cfg["alert_pull_batch_limit"]))
                if not batches:
                    continue
                alerts: list[dict[str, Any]] = []
                skipped_bad = 0
                bad_sample: str | None = None
                for _tp, records in batches.items():
                    for record in records:
                        body = _parse(record.value)
                        if body is None:
                            skipped_bad += 1
                            if bad_sample is None:  # 追踪开启时留首条原文，防「要找的恰好是解析失败那条」盲区
                                raw = record.value
                                bad_sample = (raw if isinstance(raw, str)
                                              else raw.decode("utf-8", "replace"))[:120]
                            continue
                        alerts.append(body)
                if skipped_bad:
                    sample = (f" 首条样本：{bad_sample!r}"
                              if os.environ.get("OPENOPS_ALERT_TRACE", "").strip() else "")
                    log.warning("[alerts][kafka] 跳过坏消息 %d 条（非 JSON 对象）%s",
                                skipped_bad, sample)
                counters = None
                if alerts:
                    counters = await ingest.ingest_batch(alerts, cfg=cfg)
                    # warning 级批摘要：默认部署 root 无 handler、info 不可见——unmatched 全靠它
                    # 现形（此前零日志，排查只能直查 DB）。空轮不打，风暴期一批一行可接受。
                    # apps 分布（top6+其余）回答「我的应用 ID 有没有进这批」，「无」= appIdList 缺失量。
                    apps = Counter(str(a.get("app_id") or "") or "无" for a in alerts)
                    top = dict(apps.most_common(6))
                    if (rest := len(alerts) - sum(top.values())):
                        top["其余"] = rest
                    log.warning("[alerts][kafka] 本批 %d 条：%s apps=%s", len(alerts),
                                {k: v for k, v in (counters or {}).items() if v}, top)
                health.update({"last_batch_at": _now_iso(), "last_counters": counters,
                               "batches": health["batches"] + 1,
                               "consumed": health["consumed"] + len(alerts),
                               "skipped_bad": health["skipped_bad"] + skipped_bad})
                await consumer.commit()  # 落库成功才走到这——批级异常在上面抛出即不 commit
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 —— DB/网络抖动：本批不 commit（重投），退避续跑
                log.warning("[alerts][kafka] 本批消费失败（不 commit，等待重投）", exc_info=True)
                await asyncio.sleep(1)
    except asyncio.CancelledError:
        raise
    finally:
        if own:
            try:
                await consumer.stop()
            except Exception:  # noqa: BLE001
                pass
