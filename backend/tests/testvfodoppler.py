"""Observation Doppler frequency delivered to waterfall and IQ consumers."""

from queue import Queue

import pytest

from pipeline.streaming.iqbroadcaster import IQBroadcaster
from session.tracker import session_tracker
from vfos.state import VFOManager
from vfos.updates import handle_vfo_updates_for_tracking


@pytest.fixture
def observation_vfos():
    manager = VFOManager()
    session_id = session_tracker.register_internal_session(
        "obs-doppler-test", "sdr-test", 1, session_key="rx1"
    )
    other_id = session_tracker.register_internal_session(
        "obs-other-test", "sdr-test", 1, session_key="rx1"
    )
    for observation_id, current_id in (
        ("obs-doppler-test", session_id),
        ("obs-other-test", other_id),
    ):
        manager.configure_internal_vfo(
            observation_id=observation_id,
            vfo_number=1,
            center_freq=436_888_000,
            bandwidth=20_000,
            modulation="FM",
            locked_transmitter_id="flock-tx",
            session_id=current_id,
        )
    yield manager, session_id, other_id
    session_tracker.unregister_internal_session(session_id)
    session_tracker.unregister_internal_session(other_id)
    manager.clear_session_vfos(session_id)
    manager.clear_session_vfos(other_id)


@pytest.mark.asyncio
async def test_observation_doppler_reaches_snapshot_and_iq_without_cross_tuning(
    observation_vfos,
):
    manager, session_id, other_id = observation_vfos
    tracking_data = {
        "tracker_id": "obs-doppler-test",
        "rig_data": {
            "doppler_status": "tracking",
            "transmitters": [{"id": "flock-tx", "downlink_observed_freq": 436_897_500}],
        },
    }
    await handle_vfo_updates_for_tracking(None, tracking_data)

    state = manager.get_vfo_state(session_id, 1)
    assert state.center_freq == 436_897_500
    assert state.doppler_status == "tracking"
    assert manager.get_vfo_state(other_id, 1).center_freq == 436_888_000

    iq_message = IQBroadcaster(Queue(), "sdr-test")._enrich_iq_message_with_vfo_states(
        {"samples": []}, session_id
    )
    assert iq_message["vfo_states"][1]["center_freq"] == 436_897_500
    snapshot = session_tracker.get_runtime_snapshot(type("ProcessManager", (), {"processes": {}})())
    assert snapshot["sessions"][session_id]["vfos"][1]["center_freq"] == 436_897_500
    assert snapshot["sessions"][session_id]["vfos"][1]["doppler_status"] == "tracking"


@pytest.mark.asyncio
async def test_unavailable_doppler_does_not_apply_nominal_frequency(observation_vfos):
    manager, session_id, other_id = observation_vfos
    await handle_vfo_updates_for_tracking(
        None,
        {
            "tracker_id": "obs-doppler-test",
            "rig_data": {"doppler_status": "unavailable", "transmitters": None},
        },
    )

    state = manager.get_vfo_state(session_id, 1)
    assert state.center_freq == 436_888_000
    assert state.doppler_status == "unavailable"
    assert manager.get_vfo_state(other_id, 1).doppler_status == "waiting"


@pytest.mark.asyncio
async def test_tracker_slot_prefix_can_resolve_observation_without_obs_prefix(observation_vfos):
    manager, session_id, _ = observation_vfos
    extra_id = session_tracker.register_internal_session("other-format", "sdr-test", 1)
    try:
        manager.configure_internal_vfo(
            observation_id="other-format",
            vfo_number=1,
            center_freq=436_888_000,
            bandwidth=20_000,
            modulation="FM",
            locked_transmitter_id="flock-tx",
            session_id=extra_id,
        )
        await handle_vfo_updates_for_tracking(
            None,
            {
                "tracker_id": "obs-other-format",
                "rig_data": {
                    "doppler_status": "tracking",
                    "transmitters": [{"id": "flock-tx", "downlink_observed_freq": 436_897_500}],
                },
            },
        )
        assert manager.get_vfo_state(extra_id, 1).center_freq == 436_897_500
        assert manager.get_vfo_state(session_id, 1).center_freq == 436_888_000
    finally:
        session_tracker.unregister_internal_session(extra_id)
        manager.clear_session_vfos(extra_id)
