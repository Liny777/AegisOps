"""Kafka 消费循环单测（假 consumer 注入缝，零真 Kafka/零 DB）：

先落库后 commit 次序 / 坏 JSON 跳过 / 批级失败不 commit / 全局热停不拉取 / 配置 fail-closed。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from alerts import kafka_source
from infra.external.alert_platform_client import AlertPlatformError


class _Rec:
    def __init__(self, value: bytes):
        self.value = value


class _FakeConsumer:
    def __init__(self, batches: list[list[_Rec]]):
        self._batches = list(batches)
        self.commits = 0
        self.getmany_called = False

    async def getmany(self, timeout_ms: int, max_records: int):
        self.getmany_called = True
        await asyncio.sleep(0)
        if self._batches:
            return {"tp0": self._batches.pop(0)}
        await asyncio.sleep(0.01)
        return {}

    async def commit(self):
        self.commits += 1


async def _run_briefly(task: asyncio.Task, until, timeout_s: float = 3.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if until():
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_commit_after_persist_and_bad_message_skip(monkeypatch):
    order: list[str] = []
    received: list[dict] = []

    async def fake_get_config():
        return {"alert_enabled": True, "alert_pull_batch_limit": 200}

    async def fake_ingest_batch(alerts, cfg=None, rules=None):
        order.append("persist")
        received.extend(alerts)
        return {}

    monkeypatch.setattr("alerts.service.get_config", fake_get_config)
    monkeypatch.setattr("alerts.ingest.ingest_batch", fake_ingest_batch)

    fake = _FakeConsumer([[_Rec(b"{bad json"),
                           _Rec(json.dumps({"alert_id": "a1", "title": "t"}).encode())]])
    real_commit = fake.commit

    async def traced_commit():
        order.append("commit")
        await real_commit()

    fake.commit = traced_commit  # type: ignore[method-assign]

    task = asyncio.create_task(kafka_source.consume_loop(consumer=fake))
    await _run_briefly(task, lambda: fake.commits >= 1)
    assert order[:2] == ["persist", "commit"]  # 先落库后 commit（at-least-once 铁律）
    assert [a["alert_id"] for a in received] == ["a1"]  # 坏消息跳过不阻塞分区


async def test_batch_failure_means_no_commit(monkeypatch):
    async def fake_get_config():
        return {"alert_enabled": True, "alert_pull_batch_limit": 200}

    async def broken_ingest(alerts, cfg=None, rules=None):
        raise RuntimeError("db down")

    monkeypatch.setattr("alerts.service.get_config", fake_get_config)
    monkeypatch.setattr("alerts.ingest.ingest_batch", broken_ingest)

    fake = _FakeConsumer([[_Rec(json.dumps({"alert_id": "a1", "title": "t"}).encode())]])
    task = asyncio.create_task(kafka_source.consume_loop(consumer=fake))
    await _run_briefly(task, lambda: not fake._batches, timeout_s=2.0)
    assert fake.commits == 0  # 落库失败 → 绝不 commit（Kafka 重投兜底）


async def test_disabled_hot_stop_pauses_consumption(monkeypatch):
    async def fake_get_config():
        return {"alert_enabled": False, "alert_pull_batch_limit": 200}

    monkeypatch.setattr("alerts.service.get_config", fake_get_config)
    fake = _FakeConsumer([[_Rec(b"{}")]])
    task = asyncio.create_task(kafka_source.consume_loop(consumer=fake))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.getmany_called is False and fake.commits == 0  # 热停：不拉不 commit


def test_kafka_config_fail_closed(monkeypatch):
    monkeypatch.delenv("OPENOPS_ALERT_KAFKA_BOOTSTRAP", raising=False)
    monkeypatch.delenv("OPENOPS_ALERT_KAFKA_TOPIC", raising=False)
    with pytest.raises(AlertPlatformError) as e:
        kafka_source.kafka_config()
    assert e.value.kind == "config"

    monkeypatch.setenv("OPENOPS_ALERT_KAFKA_BOOTSTRAP", "kafka1:9092,kafka2:9092")
    monkeypatch.setenv("OPENOPS_ALERT_KAFKA_TOPIC", "ops-alert-changes")
    cfg = kafka_source.kafka_config()
    assert cfg["group_id"] == kafka_source.GROUP_DEFAULT and "sasl_mechanism" not in cfg

    monkeypatch.setenv("OPENOPS_ALERT_KAFKA_USERNAME", "openops")
    monkeypatch.setenv("OPENOPS_ALERT_KAFKA_PASSWORD", "secret")
    cfg = kafka_source.kafka_config()
    assert cfg["sasl_mechanism"] == "PLAIN" and cfg["security_protocol"] == "SASL_PLAINTEXT"
