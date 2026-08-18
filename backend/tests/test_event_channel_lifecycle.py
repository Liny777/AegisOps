import pytest

from runtime import events


def setup_function():
    events.reset()


def teardown_function():
    events.reset()


def _publish(run_id: str) -> None:
    events.publish(run_id, events.envelope(run_id, "openops.test"))


def test_idle_channel_capacity_uses_lru_without_evicting_active_subscribers(monkeypatch):
    monkeypatch.setattr(events, "_MAX_CHANNELS", 2)
    monkeypatch.setattr(events, "_CHANNEL_IDLE_TTL_SECONDS", 10_000)

    active_queue, _replay, _resync = events.subscribe("active", None)
    _publish("idle-old")
    _publish("idle-new")

    assert "active" in events._channels
    assert "idle-old" not in events._channels
    assert "idle-new" in events._channels

    events.unsubscribe("active", active_queue)


def test_expired_idle_channel_is_reclaimed_but_active_channel_is_preserved(monkeypatch):
    monkeypatch.setattr(events, "_CHANNEL_IDLE_TTL_SECONDS", 5)
    idle_queue, _replay, _resync = events.subscribe("idle", None)
    events.unsubscribe("idle", idle_queue)
    active_queue, _replay, _resync = events.subscribe("active", None)

    events._channels["idle"].last_touched = 1
    events._channels["active"].last_touched = 1
    events._prune_channels(now=10)

    assert "idle" not in events._channels
    assert "active" in events._channels
    events.unsubscribe("active", active_queue)


def test_recreated_channel_marks_a_newer_client_cursor_for_resync(monkeypatch):
    monkeypatch.setattr(events, "_CHANNEL_IDLE_TTL_SECONDS", 1)
    _publish("run-1")
    _publish("run-1")
    events._channels["run-1"].last_touched = 1
    events._prune_channels(now=3)

    queue, replay, need_resync = events.subscribe("run-1", 2)
    assert replay == []
    assert need_resync is True
    events.unsubscribe("run-1", queue)


@pytest.mark.parametrize(
    "transient_type",
    ["openops.assistant.delta", "openops.assistant.thinking.delta"],
)
def test_transient_delta_broadcasts_without_buffering(transient_type):
    run_id = "run-transient"
    queue, _replay, _resync = events.subscribe(run_id, None)

    events.publish(run_id, events.envelope(run_id, "openops.task.started"))
    deltas = [
        events.publish(run_id, events.envelope(run_id, transient_type))
        for _ in range(5)
    ]
    events.publish(run_id, events.envelope(run_id, "openops.rca.updated"))

    assert all(delta["sequence"] is None for delta in deltas)
    buffered = list(events._channels[run_id].buffer)
    assert [e["event_type"] for e in buffered] == [
        "openops.task.started",
        "openops.rca.updated",
    ]
    assert [e["sequence"] for e in buffered] == [1, 2]
    assert queue.qsize() == 5 + 2
    events.unsubscribe(run_id, queue)


def test_delta_flood_does_not_evict_buffer_or_force_resync():
    run_id = "run-flood"
    events.publish(run_id, events.envelope(run_id, "openops.task.started"))
    for _ in range(1000):
        events.publish(run_id, events.envelope(run_id, "openops.assistant.delta"))

    queue, replay, need_resync = events.subscribe(run_id, 1)
    assert need_resync is False
    assert replay == []
    events.unsubscribe(run_id, queue)
