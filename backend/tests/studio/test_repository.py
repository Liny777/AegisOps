"""sre_agent_studio_span 仓储：插查按 run 归组 / stats 聚合 / 过期清理。"""
from __future__ import annotations

import os
import uuid

import psycopg

from conftest import reset_database
from infra import db
from studio import repository as studio_spans

RUN_A = str(uuid.uuid4())
RUN_B = str(uuid.uuid4())


async def test_insert_and_list_by_run():
    reset_database()
    await db.open_pool()
    try:
        await studio_spans.insert_span(
            user_id="0026demo01", agent_run_id=RUN_A, task_id="tsk_1", session_id="fsid-a",
            agent_role="main", agent_name="sre-rca", kind="agent",
            started_at=1700000000.0, ended_at=1700000001.5,
            input_messages="用户问题", output_messages="最终结论")
        await studio_spans.insert_span(
            user_id="0026demo01", agent_run_id=RUN_A, task_id="tsk_1", session_id="fsid-a",
            agent_role="main", kind="llm", model="glm-5.1", provider="zhipu",
            input_tokens=100, output_tokens=30, latency_ms=2100.5,
            started_at=1700000000.1, ended_at=1700000002.2,
            input_messages='[{"role":"user"}]', output_messages='[{"role":"assistant"}]',
            finish_reason="stop", span_status="OK")
        await studio_spans.insert_span(
            user_id="0026demo01", agent_run_id=RUN_A, task_id="tsk_1.inspect-abcd1234",
            session_id="fsid-a:inspect-abcd1234", agent_role="inspect", kind="tool",
            tool_name="query_metrics", tool_args='{"q":1}', tool_result="ok",
            latency_ms=80.0, started_at=1700000003.0, ended_at=1700000003.1, span_status="OK")
        # 另一个 run 的行不得串场
        await studio_spans.insert_span(
            user_id="0099other", agent_run_id=RUN_B, session_id="fsid-b",
            agent_role="main", kind="llm", started_at=1.0, ended_at=2.0)

        rows = await studio_spans.list_spans_by_run(RUN_A)
        assert [r["kind"] for r in rows] == ["agent", "llm", "tool"]  # id 序 = 插入序
        assert all(str(r["agent_run_id"]) == RUN_A for r in rows)
        llm = rows[1]
        assert llm["model"] == "glm-5.1" and llm["input_tokens"] == 100
        assert llm["input_messages"] == '[{"role":"user"}]'
        assert llm["started_at"].timestamp() == 1700000000.1  # epoch → timestamptz 往返
        assert (await studio_spans.list_spans_by_run(RUN_B))[0]["user_id"] == "0099other"
        assert await studio_spans.list_spans_by_run(str(uuid.uuid4())) == []
    finally:
        await db.close_pool()


async def test_stats_by_run_ids():
    reset_database()
    await db.open_pool()
    try:
        for kind, tokens in (("llm", (100, 30)), ("llm", (50, 10)), ("tool", (0, 0)), ("agent", (0, 0))):
            await studio_spans.insert_span(
                agent_run_id=RUN_A, session_id="s-main", agent_role="main", kind=kind,
                input_tokens=tokens[0], output_tokens=tokens[1], latency_ms=100.0,
                started_at=1.0, ended_at=2.0)
        await studio_spans.insert_span(
            agent_run_id=RUN_A, session_id="s-sub", agent_role="inspect", kind="llm",
            input_tokens=5, output_tokens=5, latency_ms=50.0, started_at=1.0, ended_at=2.0)
        stats = await studio_spans.stats_by_run_ids([RUN_A, RUN_B])
        assert RUN_B not in stats
        a = stats[RUN_A]
        assert a["agents"] == 2 and a["llm_calls"] == 3 and a["tool_calls"] == 1
        assert a["total_tokens"] == 200
        assert a["latency_ms"] == 350.0  # 2×主llm + 主tool + 子llm；agent 类 span 不计入耗时
        assert await studio_spans.stats_by_run_ids([]) == {}
    finally:
        await db.close_pool()


async def test_purge_expired():
    reset_database()
    await db.open_pool()
    try:
        await studio_spans.insert_span(agent_run_id=RUN_A, kind="llm", started_at=1.0, ended_at=2.0)
        await studio_spans.insert_span(agent_run_id=RUN_A, kind="tool", started_at=1.0, ended_at=2.0)
        with psycopg.connect(os.environ["OPENOPS_DATABASE_URL"], autocommit=True) as conn:
            conn.execute(
                "update sre_agent_studio_span set expire_at = now() - interval '1 day' "
                "where kind = 'llm'")
        assert await studio_spans.purge_expired() == 1
        rows = await studio_spans.list_spans_by_run(RUN_A)
        assert [r["kind"] for r in rows] == ["tool"]
    finally:
        await db.close_pool()
