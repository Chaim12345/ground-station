# Copyright (c) 2026 Efstratios Goudelis

import pytest

from observations.tasks import trackerhandler as handler_module
from observations.tasks.trackerhandler import TrackerHandler, TrackerHandoffError
from tracker.operations import OperationRegistry


class FakeManager:
    def __init__(self, state=None):
        self.state = dict(state or {})

    async def get_tracking_state(self):
        return dict(self.state)


class SessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, traceback):
        return None


@pytest.mark.asyncio
async def test_observation_tracker_removal_keeps_operations_lock_free(monkeypatch):
    lock_states = []

    def remove(_tracker_id):
        lock_states.append(handler_module.operations.lock.locked())
        return {"success": True}

    async def delete(_session, _state_name):
        return {"success": True, "deleted": True}

    monkeypatch.setattr(handler_module, "remove_tracker_instance", remove)
    monkeypatch.setattr(handler_module, "AsyncSessionLocal", SessionContext)
    monkeypatch.setattr(handler_module.trackingstate_crud, "delete_tracking_state", delete)

    assert await TrackerHandler()._remove_observation_tracker_instance("obs-1")
    assert lock_states == [False]


@pytest.mark.asyncio
async def test_command_wait_requires_confirmed_success(monkeypatch):
    registry = OperationRegistry()
    monkeypatch.setattr(handler_module, "operations", registry)

    async def update(tracker_id, value, requester_sid=None, operation=None):
        assert requester_sid == "observation:one"
        assert operation == {"action": "configure"}
        registry.records["command-1"] = {
            "command_id": "command-1",
            "tracker_id": tracker_id,
            "status": "succeeded" if value["rig_state"] == "disconnected" else "failed",
            "reason": "connection failed",
        }
        return {"success": True, "result": {"command_id": "command-1"}}

    monkeypatch.setattr(handler_module, "update_tracking_state_with_ownership", update)
    handler = TrackerHandler()
    await handler._update_and_confirm("target-1", {"rig_state": "disconnected"}, "one")
    with pytest.raises(TrackerHandoffError, match="connection failed"):
        await handler._update_and_confirm("target-1", {"rig_state": "tracking"}, "one")


@pytest.mark.asyncio
async def test_observation_handoff_preserves_targets_and_cleans_up_hardware(monkeypatch):
    managers = {
        "target-1": FakeManager(
            {
                "norad_id": 100,
                "target_name": "Interactive target",
                "rotator_id": "rot-1",
                "rotator_state": "tracking",
                "rig_id": "old-rig",
                "rig_state": "tracking",
            }
        ),
        "target-2": FakeManager(
            {
                "norad_id": 200,
                "target_name": "Other target",
                "rotator_id": "none",
                "rotator_state": "disconnected",
                "rig_id": "new-rig",
                "rig_state": "connected",
            }
        ),
    }
    calls = []
    removed = []

    def manager(tracker_id):
        return managers.setdefault(tracker_id, FakeManager())

    async def confirmed(_self, tracker_id, value, _observation_id):
        calls.append((tracker_id, dict(value)))
        managers[tracker_id].state.update(value)

    async def remove(tracker_id):
        removed.append(tracker_id)
        return True

    async def valid_transmitter(_norad_id, _transmitter_id):
        return True

    monkeypatch.setattr(handler_module, "get_tracker_manager", manager)
    monkeypatch.setattr(handler_module, "get_all_tracker_managers", lambda: managers)
    monkeypatch.setattr(
        handler_module,
        "get_assigned_tracker_for_rotator",
        lambda rotator_id: (
            "target-1"
            if rotator_id == "rot-1" and managers["target-1"].state.get("rotator_id") == "rot-1"
            else None
        ),
    )
    monkeypatch.setattr(
        handler_module,
        "create_observation_tracker_slot",
        lambda _observation_id: {"success": True, "tracker_id": "obs-one"},
    )
    monkeypatch.setattr(TrackerHandler, "_update_and_confirm", confirmed)
    monkeypatch.setattr(TrackerHandler, "_rig_transmitter_exists", staticmethod(valid_transmitter))
    monkeypatch.setattr(
        TrackerHandler,
        "_remove_observation_tracker_instance",
        lambda self, tracker_id: remove(tracker_id),
    )

    handler = TrackerHandler()
    result = await handler.start_tracker_task(
        "one",
        {"norad_id": 300, "name": "Observation target"},
        {"id": "rot-1", "tracking_enabled": True},
        [],
        {"id": "new-rig", "transmitter_id": "tx-300"},
    )

    assert result["success"] is True
    assert result["tracker_id"] == "obs-one"
    assert [item for item in calls if item[0] == "target-1"] == [
        ("target-1", {"rig_state": "disconnected"}),
        ("target-1", {"rig_id": "none", "rig_vfo": "none"}),
        ("target-1", {"rotator_state": "disconnected"}),
        ("target-1", {"rotator_id": "none"}),
    ]
    assert [item for item in calls if item[0] == "target-2"] == [
        ("target-2", {"rig_state": "disconnected"}),
        ("target-2", {"rig_id": "none", "rig_vfo": "none"}),
    ]
    assert managers["target-1"].state["norad_id"] == 100
    assert managers["target-2"].state["norad_id"] == 200
    assert managers["obs-one"].state["norad_id"] == 300
    assert managers["obs-one"].state["rig_state"] == "tracking"
    assert managers["obs-one"].state["transmitter_id"] == "tx-300"

    assert await handler.stop_tracker_task("one", {}, result)
    assert managers["obs-one"].state["rig_state"] == "disconnected"
    assert managers["obs-one"].state["rig_id"] == "none"
    assert managers["obs-one"].state["rotator_state"] == "disconnected"
    assert managers["obs-one"].state["rotator_id"] == "none"
    assert removed == ["obs-one"]


@pytest.mark.asyncio
async def test_failed_disconnect_does_not_create_obs_slot(monkeypatch):
    owner = FakeManager(
        {
            "rotator_id": "rot-1",
            "rotator_state": "tracking",
            "rig_id": "rig-1",
            "rig_state": "tracking",
        }
    )
    created = []

    async def fail_disconnect(*_args):
        raise TrackerHandoffError("hardware_command_failed", "rig did not disconnect")

    monkeypatch.setattr(handler_module, "get_assigned_tracker_for_rotator", lambda _id: "target-1")
    monkeypatch.setattr(handler_module, "get_tracker_manager", lambda _id: owner)
    monkeypatch.setattr(
        handler_module, "create_observation_tracker_slot", lambda _id: created.append(True)
    )
    monkeypatch.setattr(TrackerHandler, "_update_and_confirm", fail_disconnect)

    result = await TrackerHandler().start_tracker_task(
        "one",
        {"norad_id": 300},
        {"id": "rot-1", "tracking_enabled": True},
        [],
    )
    assert result["success"] is False
    assert result["error"] == "hardware_command_failed"
    assert created == []
    assert owner.state["rotator_id"] == "rot-1"


@pytest.mark.asyncio
async def test_other_observation_hardware_is_not_taken(monkeypatch):
    monkeypatch.setattr(handler_module, "get_assigned_tracker_for_rotator", lambda _id: "obs-other")
    result = await TrackerHandler().start_tracker_task(
        "one",
        {"norad_id": 300},
        {"id": "rot-1", "tracking_enabled": True},
        [],
    )
    assert result["success"] is False
    assert result["error"] == "rotator_in_use"


@pytest.mark.asyncio
async def test_selected_rig_requires_tracking_transmitter():
    result = await TrackerHandler().start_tracker_task(
        "one",
        {"norad_id": 300},
        {},
        [{"type": "decoder", "config": {"transmitter_id": "decoder-tx"}}],
        {"id": "rig-1"},
    )
    assert result["success"] is False
    assert result["error"] == "missing_rig_transmitter"


@pytest.mark.asyncio
async def test_cleanup_keeps_obs_slot_when_disconnect_is_unconfirmed(monkeypatch):
    manager = FakeManager(
        {
            "rotator_id": "rot-1",
            "rotator_state": "tracking",
            "rig_id": "none",
            "rig_state": "disconnected",
        }
    )
    removed = []

    async def fail_disconnect(_self, _tracker_id, _value, _observation_id):
        raise TrackerHandoffError("hardware_command_failed", "rotator did not disconnect")

    async def remove(_self, tracker_id):
        removed.append(tracker_id)
        return True

    monkeypatch.setattr(handler_module, "get_tracker_manager", lambda _id: manager)
    monkeypatch.setattr(TrackerHandler, "_update_and_confirm", fail_disconnect)
    monkeypatch.setattr(TrackerHandler, "_remove_observation_tracker_instance", remove)

    ok = await TrackerHandler().stop_tracker_task(
        "one", {}, {"tracker_id": "obs-one", "ephemeral": True}
    )
    assert ok is False
    assert removed == []
