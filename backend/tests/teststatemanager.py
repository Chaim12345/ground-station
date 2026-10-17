# Copyright (c) 2026 Efstratios Goudelis

from types import SimpleNamespace

import pytest

from tracker.statemanager import StateManager


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tracker_id", "previous_norad_id", "preserve_rig"),
    [
        ("obs-one", None, True),
        ("obs-one", 25544, False),
        ("target-one", None, False),
    ],
)
async def test_first_observation_target_keeps_selected_rig_and_transmitter(
    tracker_id, previous_norad_id, preserve_rig
):
    messages = []
    tracker = SimpleNamespace(
        tracker_id=tracker_id,
        rotator_data={},
        rotator_handler=SimpleNamespace(reset_overlap_lane_plan=lambda: None),
        satellite_data={},
        queue_out=SimpleNamespace(put=messages.append),
        input_tracking_state={
            "norad_id": 43880,
            "rig_state": "tracking",
            "transmitter_id": "tx-43880",
        },
        current_rig_state="tracking",
        rig_data={"tracking": True, "stopped": False},
    )

    await StateManager(tracker).handle_satellite_change(previous_norad_id, 43880)

    assert tracker.input_tracking_state["rig_state"] == ("tracking" if preserve_rig else "stopped")
    assert tracker.input_tracking_state["transmitter_id"] == (
        "tx-43880" if preserve_rig else "none"
    )
    assert len(messages) == (1 if preserve_rig else 2)
