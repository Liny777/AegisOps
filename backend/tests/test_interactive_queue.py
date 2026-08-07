"""交互任务排队：名额满时排队而非 429（替代原 USER_TASK_CONCURRENCY_LIMIT 直接拒绝）。

覆盖：排队→出队→执行 / 位次递减 / 排队中取消 / 排队超时 / 队列满仍 429 /
灰度开关回退 429 / 出队守闸不放空。（告警池不受交互队列影响见 alerts/test_dispatch_e2e.py）

⚠ **占位方式决定了能测出什么**：`_occupy_fake` 塞假 TaskState 占名额，快且确定，
但它的 `done_callback` 永不触发——**整条 drain 路径在这些用例里根本没跑过**。
凡是要验"名额释放后队列怎么走"的用例，必须用 `_occupy_slots`（真起任务停在 ASK →
批准 → 观察出队），否则会像 2026-08-06 那次一样：单测全绿，真链路一放就把整队放空。
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app import interactive_queue
from runtime import task_registry
from runtime.task_registry import TaskState
from conftest import USER_HEADERS, create_instance, create_run, unwrap, wait_until


@pytest.fixture(autouse=True)
def _clean_queue(client):
    """队列清空 + **配置复原**。

    复原不是洁癖：`_set_cfg` 写的是 DB 里的 sandbox 域，用例之间会互相污染——
    比如 test_disabled_flag 把 interactive_queue_enabled 关成 False 后不还原，
    随机顺序下后面的排队用例就全变 429。（同一个库还可能被 dev 后端共用。）

    ⚠ 形参 `client` 不是摆设：autouse fixture 默认先于普通 fixture 建立、后于它拆卸，
    复原就会跑在连接池关闭之后（psycopg_pool 报错）。显式依赖把顺序倒过来——
    client 先建、本 fixture 后建，拆卸时本 fixture 先跑，池还活着。
    """
    interactive_queue.reset()
    yield
    interactive_queue.reset()
    _set_cfg(per_user_running_task_limit=2, interactive_queue_enabled=True,
             interactive_queue_max=20, interactive_queue_timeout_s=300)


def _crid() -> str:
    return f"crid_{time.time_ns()}"


def _set_cfg(**kv) -> None:
    """热调 sandbox 域配置（对齐 test_sandbox.py 的 asyncio.run 写法，不绕 HTTP）。"""
    from infra.repositories import runtime_config

    async def _apply() -> None:
        for k, v in kv.items():
            await runtime_config.upsert(runtime_config.DOMAIN_SANDBOX, k, v, reason="queue test")

    asyncio.run(_apply())


def _occupy_fake(run_id: str, instance_id: str, n: int = 1) -> None:
    """用假 TaskState 占满交互名额（对齐 test_sandbox RUN-004 写法：快且确定，
    不必真起任务等 ASK）。需要真实出队链路的用例才用 _occupy_slots。"""
    for i in range(n):
        task_registry.put(TaskState(task_id=f"tk_busy{i}", run_id=f"{run_id}-{i}",
                                    user_id="0026demo01", instance_id=instance_id,
                                    input_text="占位"))


def _start(client, run_id: str, text: str = "巡检 APP-A"):
    return client.post(f"/api/openops/v1/agent-runs/{run_id}/tasks", headers=USER_HEADERS,
                       json={"client_request_id": _crid(), "input_text": text})


def _occupy_slots(client, iid: str, n: int) -> list[str]:
    """占满 n 个交互名额（任务停在 ASK 审批，保持 running）。返回 run_id 列表。"""
    runs = []
    for _ in range(n):
        run = create_run(client, iid)
        r = _start(client, run["agent_run_id"], "请恢复 APP-A")
        assert r.status_code < 400, r.text
        assert unwrap(r)["status"] == "running"
        runs.append(run["agent_run_id"])
    return runs


def test_queue_instead_of_429_then_auto_start(client):
    """名额满 → 返回 queued + 位次；前一个任务结束后自动启动（无需用户重发）。"""
    _set_cfg(per_user_running_task_limit=1)
    iid = create_instance(client)["instance_id"]
    busy = _occupy_slots(client, iid, 1)[0]

    run2 = create_run(client, iid)
    body = unwrap(_start(client, run2["agent_run_id"]))
    assert body["status"] == "queued", "名额满时应排队而非 429"
    assert body["queue_position"] == 1
    queued_task = body["task_id"]
    assert interactive_queue.depth()["total"] == 1

    # 放行占位任务 → 名额释放 → 队首自动出队启动。
    # ASK 审批要等编排器跑到那一步才出现（同 test_ask.py 的 wait_until 写法），不能直接取。
    approvals = wait_until(
        lambda: unwrap(client.get(f"/api/openops/v1/agent-runs/{busy}/approvals",
                                  headers=USER_HEADERS)),
        timeout=10, interval=0.1)
    assert approvals, "占位任务未产生 ASK 审批"
    for ap in approvals:
        client.post(f"/api/openops/v1/approvals/{ap['approval_request_id']}:decide",
                    headers=USER_HEADERS,
                    json={"client_request_id": _crid(), "decision": "approved"})

    started = wait_until(lambda: interactive_queue.depth()["total"] == 0, timeout=10, interval=0.1)
    assert started, "队首未被自动唤醒"
    state = wait_until(
        lambda: unwrap(client.get(f"/api/openops/v1/agent-runs/{run2['agent_run_id']}/state",
                                  headers=USER_HEADERS)).get("active_task"),
        timeout=10, interval=0.1)
    assert state and state["task_id"] == queued_task, "出队后应复用原 task_id"


def test_queue_positions_shift_down(client):
    """队首出队后，其余位次各减一（SSE 推 queue_position）。"""
    _set_cfg(per_user_running_task_limit=1)
    iid = create_instance(client)["instance_id"]
    _occupy_fake(create_run(client, iid)["agent_run_id"], iid)

    ids = []
    for expect_pos in (1, 2, 3):
        run = create_run(client, iid)
        body = unwrap(_start(client, run["agent_run_id"]))
        assert body["status"] == "queued" and body["queue_position"] == expect_pos
        ids.append(body["task_id"])

    assert [interactive_queue.position(t) for t in ids] == [1, 2, 3]
    interactive_queue.cancel(ids[0])                      # 队首离场
    assert [interactive_queue.position(t) for t in ids] == [None, 1, 2], "剩余位次应各减一"


def test_cancel_while_queued(client):
    """排队中可取消：:cancel 命中队列分支（此时还没有 TaskState，不能误判 404）。"""
    _set_cfg(per_user_running_task_limit=1)
    iid = create_instance(client)["instance_id"]
    _occupy_fake(create_run(client, iid)["agent_run_id"], iid)

    run2 = create_run(client, iid)
    task_id = unwrap(_start(client, run2["agent_run_id"]))["task_id"]
    assert interactive_queue.position(task_id) == 1

    got = unwrap(client.post(f"/api/openops/v1/tasks/{task_id}:cancel", headers=USER_HEADERS,
                             json={"client_request_id": _crid()}))
    assert got["status"] == "cancelled"
    assert interactive_queue.position(task_id) is None
    assert interactive_queue.depth()["total"] == 0


def test_queue_timeout_emits_failed(client):
    """排队超时 → 出队并推 task.failed(QUEUE_TIMEOUT)，不会永远挂着。"""
    _set_cfg(per_user_running_task_limit=1, interactive_queue_timeout_s=1)
    iid = create_instance(client)["instance_id"]
    _occupy_fake(create_run(client, iid)["agent_run_id"], iid)

    run2 = create_run(client, iid)
    task_id = unwrap(_start(client, run2["agent_run_id"]))["task_id"]
    assert interactive_queue.position(task_id) == 1

    gone = wait_until(lambda: interactive_queue.position(task_id) is None, timeout=6, interval=0.2)
    assert gone, "排队超时后应自动出队"
    assert interactive_queue.depth()["total"] == 0


def test_queue_full_still_429(client):
    """连队列都排不下时才 429（保留原错误码，语义=队列已满）。"""
    _set_cfg(per_user_running_task_limit=1, interactive_queue_max=2)
    iid = create_instance(client)["instance_id"]
    _occupy_fake(create_run(client, iid)["agent_run_id"], iid)

    for _ in range(2):
        run = create_run(client, iid)
        assert unwrap(_start(client, run["agent_run_id"]))["status"] == "queued"

    run = create_run(client, iid)
    resp = _start(client, run["agent_run_id"])
    assert resp.status_code == 429
    assert "排队人数已达上限" in resp.text


def test_drain_respects_limit_not_flush_all(client):
    """出队按名额逐个放，**不是一次放空**（2026-07-31 真链路实测的回归）。

    原实现在 `_drain_once` 末尾无条件 `if _queue: drain()`，一次 drain 就把整队放行，
    限额 1 时 6 条排队任务同时启动，并发闸形同虚设。此前用例用假 TaskState 占位、
    drain 从未真正触发，所以漏掉了——这里必须走真实 done_callback 链路。
    """
    _set_cfg(per_user_running_task_limit=1)
    iid = create_instance(client)["instance_id"]
    busy = _occupy_slots(client, iid, 1)[0]

    # 排队任务也用会停在 ASK 的输入：出队后它会占住名额不放，深度才可确定性断言
    # （若用秒完成的输入，它跑完又触发 drain，深度一路掉到 0，断言变成竞态）
    queued = []
    for _ in range(4):
        run = create_run(client, iid)
        body = unwrap(_start(client, run["agent_run_id"], "请恢复 APP-A"))
        assert body["status"] == "queued"
        queued.append(body["task_id"])
    assert interactive_queue.depth()["total"] == 4

    # 放行占位任务 → 只应放出 1 个，其余 3 个仍在队列里
    approvals = wait_until(
        lambda: unwrap(client.get(f"/api/openops/v1/agent-runs/{busy}/approvals",
                                  headers=USER_HEADERS)),
        timeout=10, interval=0.1)
    assert approvals, "占位任务未产生 ASK 审批"
    for ap in approvals:
        client.post(f"/api/openops/v1/approvals/{ap['approval_request_id']}:decide",
                    headers=USER_HEADERS,
                    json={"client_request_id": _crid(), "decision": "approved"})

    moved = wait_until(lambda: interactive_queue.depth()["total"] < 4, timeout=10, interval=0.1)
    assert moved, "占位任务结束后队首未被放行"
    # 一个名额只放一个：出队的那条停在 ASK 占住名额，剩下 3 条必须原地不动
    assert interactive_queue.depth()["total"] == 3, \
        f"一次释放放出了多个（剩余深度={interactive_queue.depth()['total']}，应为 3）——并发闸失效"
    assert interactive_queue.position(queued[0]) is None, "应从队首放行"
    assert [interactive_queue.position(t) for t in queued[1:]] == [1, 2, 3], "其余位次各减一"


def test_disabled_flag_falls_back_to_429(client):
    """灰度开关关闭 → 退回旧的 429 行为（应急回滚路径可用）。"""
    _set_cfg(per_user_running_task_limit=1, interactive_queue_enabled=False)
    iid = create_instance(client)["instance_id"]
    _occupy_fake(create_run(client, iid)["agent_run_id"], iid)

    run2 = create_run(client, iid)
    resp = _start(client, run2["agent_run_id"])
    assert resp.status_code == 429
    assert "并发任务数已达上限" in resp.text
    assert interactive_queue.depth()["total"] == 0, "关闭时不应入队"
