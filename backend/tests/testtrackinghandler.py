# Copyright (c) 2026 Efstratios Goudelis

from datetime import datetime, timedelta, timezone

import pytest

from common.constants import RigStates
from common.events import EventBroker
from handlers.entities import tracking as tracking_handler
from handlers.entities.tracking import (
    _build_non_satellite_transmitter_target_key,
    _normalize_target_update_payload,
    _normalize_tracker_target_type,
    _pick_active_or_upcoming_pass,
)


class _SessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, traceback):
        return None


@pytest.mark.asyncio
async def test_delete_tracker_keeps_operations_lock_free_during_process_stop(monkeypatch):
    lock_states = []
    broker = EventBroker()
    subscription = broker.subscribe("station-token", ["station:read"])
    monkeypatch.setattr(tracking_handler, "public_events", broker)

    def _remove_tracker(_tracker_id):
        lock_states.append(tracking_handler.operations.lock.locked())
        return {"success": True}

    async def _delete_tracking_state(_session, _state_name):
        return {"success": True, "deleted": True}

    async def _emit_instances(_sio):
        return None

    monkeypatch.setattr(
        tracking_handler,
        "get_tracker_instances_payload",
        lambda: {"instances": [{"tracker_id": "target-1"}]},
    )
    monkeypatch.setattr(tracking_handler, "remove_tracker_instance", _remove_tracker)
    monkeypatch.setattr(tracking_handler, "AsyncSessionLocal", _SessionContext)
    monkeypatch.setattr(
        tracking_handler.crud.trackingstate,
        "delete_tracking_state",
        _delete_tracking_state,
    )
    monkeypatch.setattr(tracking_handler, "emit_tracker_instances", _emit_instances)

    result = await tracking_handler.delete_tracker_instance(
        sio=object(),
        data={"tracker_id": "target-1"},
        logger=None,
        sid="client-1",
    )

    assert result["success"] is True
    assert lock_states == [False]
    assert subscription.queue.get_nowait() == (
        "target.slot.deleted",
        {"tracker_id": "target-1"},
    )


def test_mission_target_normalization_preserves_rig_control_fields():
    result = _normalize_target_update_payload(
        {
            "target_type": "mission",
            "command": "Juno",
            "target_name": "Juno",
            "rig_id": "rig-123",
            "rig_state": RigStates.CONNECTED,
            "transmitter_id": "tx-123",
            "rig_vfo": "1",
        }
    )

    assert result["success"] is True
    payload = result["value"]
    assert payload["target_type"] == "mission"
    assert payload["target_key"] == "mission:juno"
    assert payload["rig_id"] == "rig-123"
    assert payload["rig_state"] == RigStates.CONNECTED
    assert payload["transmitter_id"] == "tx-123"
    assert payload["rig_vfo"] == "1"


def test_body_target_normalization_preserves_rig_control_fields():
    result = _normalize_target_update_payload(
        {
            "target_type": "body",
            "body_id": "JUPITER",
            "target_name": "JUPITER",
            "rig_id": "rig-abc",
            "rig_state": RigStates.TRACKING,
            "transmitter_id": "tx-abc",
            "rig_vfo": "2",
        }
    )

    assert result["success"] is True
    payload = result["value"]
    assert payload["target_type"] == "body"
    assert payload["body_id"] == "jupiter"
    assert payload["target_key"] == "body:jupiter"
    assert payload["rig_id"] == "rig-abc"
    assert payload["rig_state"] == RigStates.TRACKING
    assert payload["transmitter_id"] == "tx-abc"
    assert payload["rig_vfo"] == "2"


def test_tracker_target_type_inference_uses_command_and_body_id():
    assert _normalize_tracker_target_type({"mission_id": "mission:command_id"}) == "mission"
    assert _normalize_tracker_target_type({"command": "Voyager 1"}) == "mission"
    assert _normalize_tracker_target_type({"body_id": "rhea"}) == "body"
    assert _normalize_tracker_target_type({"norad_id": "25544"}) == "satellite"


def test_tracking_target_uses_backend_key_as_stable_identity():
    result = _normalize_target_update_payload(
        {
            "target_type": "mission",
            "target_key": "mission:pioneer_10",
            "command": "Pioneer 10 Extended",
        }
    )

    assert result["success"] is True
    assert result["value"]["target_key"] == "mission:pioneer_10"


def test_tracking_target_rejects_legacy_key_namespace():
    result = _normalize_target_update_payload(
        {
            "target_type": "mission",
            "target_key": "missioncmd:Voyager 1",
            "command": "Voyager 1",
        }
    )

    assert result["success"] is False
    assert result["error"] == "invalid_target_key"


def test_transmitter_lookup_never_rebuilds_identity_from_metadata():
    assert (
        _build_non_satellite_transmitter_target_key(
            {
                "target_key": "mission:pioneer_10",
                "command": "Pioneer 10 Extended",
            },
            "mission",
        )
        == "mission:pioneer_10"
    )
    assert _build_non_satellite_transmitter_target_key({"command": "Pioneer 10"}, "mission") == ""


def test_pick_active_or_upcoming_pass_supports_celestial_peak_elevation_field():
    now = datetime.now(timezone.utc)
    live_pass = {
        "event_start": (now - timedelta(minutes=5)).isoformat(),
        "event_end": (now + timedelta(minutes=5)).isoformat(),
        "peak_elevation_deg": 21.0,
    }
    next_pass = {
        "event_start": (now + timedelta(minutes=20)).isoformat(),
        "event_end": (now + timedelta(minutes=32)).isoformat(),
        "peak_elevation_deg": 43.0,
    }
    active, upcoming = _pick_active_or_upcoming_pass(
        candidate_passes=[live_pass, next_pass],
        now_ms=int(now.timestamp() * 1000),
        min_elevation=10.0,
    )
    assert active == live_pass
    assert upcoming == next_pass
