from __future__ import annotations

import asyncio
import json
import time

from conftest import ADMIN_HEADERS, USER_HEADERS, create_instance, create_run, unwrap
from app import event_stream_service


def _start_task(client, run_id: str) -> None:
    unwrap(
        client.post(
            f"/api/openops/v1/agent-runs/{run_id}/tasks",
            headers=USER_HEADERS,
            json={"client_request_id": f"task_{time.time_ns()}", "input_text": "巡检 APP-A"},
        )
    )


async def _read_one_chunk(run_id: str, last_event_id: int | None) -> dict:
    gen = event_stream_service.stream(run_id, last_event_id)
    try:
        data = ""
        while not data:
            chunk = await asyncio.wait_for(anext(gen), timeout=1)
            for line in chunk.splitlines():
                if line.startswith("data:"):
                    data += line.removeprefix("data:").strip()
    finally:
        await gen.aclose()
    assert data
    return json.loads(data)


def test_sse_stream_flushes_connection_immediately():
    async def read_first() -> str:
        gen = event_stream_service.stream("run_flush_probe", None)
        try:
            return await asyncio.wait_for(anext(gen), timeout=0.1)
        finally:
            await gen.aclose()

    assert asyncio.run(read_first()) == ": connected\n\n"


def test_sse_stream_can_replay_buffered_openops_event(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    _start_task(client, run["agent_run_id"])

    payload = asyncio.run(_read_one_chunk(run["agent_run_id"], 0))
    assert payload["event_type"] == "openops.task.started"
    assert payload["sequence"] == 1


def test_sse_last_event_id_replays_later_events(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    _start_task(client, run["agent_run_id"])

    payload = asyncio.run(_read_one_chunk(run["agent_run_id"], 1))
    assert payload["sequence"] > 1


def test_sse_stream_owner_isolation(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    response = client.get(
        f"/api/openops/v1/agent-runs/{run['agent_run_id']}/events/stream",
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 403
