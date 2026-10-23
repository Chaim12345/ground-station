# Copyright (c) 2025 Efstratios Goudelis

import time
from datetime import datetime, timedelta, timezone

import pytest

from tracker.rotatorhandler import RotatorHandler


class _Queue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


class _DummyTracker:
    def __init__(self, azimuth_mode: str):
        self.rotator_controller = object()
        self.current_rotator_state = "tracking"
        self.input_tracking_state = {"rotator_state": "tracking"}
        self.rotator_details = {"azimuth_mode": azimuth_mode}
        self.rotator_data = {
            "outofbounds": False,
            "minelevation": False,
            "az": 0.0,
            "el": 0.0,
            "slewing": False,
            "error": False,
        }
        if azimuth_mode == "-180_180":
            self.azimuth_limits = (-180, 180)
        elif azimuth_mode == "0_450":
            self.azimuth_limits = (0, 450)
        else:
            self.azimuth_limits = (0, 360)
        self.elevation_limits = (0, 90)
        self.rotator_command_state = {
            "in_flight": False,
            "target_az": None,
            "target_el": None,
            "last_command_ts": 0.0,
            "settle_hits": 0,
            "overlap_lane": None,
            "overlap_plan_signature": None,
            "overlap_plan_lane": None,
            "overlap_plan_pass_end": None,
            "overlap_plan_retry_at": 0.0,
        }
        self.nudge_offset = {"az": 0, "el": 0}
        self.manual_rotator_target = None
        self.az_tolerance = 2.0
        self.el_tolerance = 2.0
        self.rotator_retarget_threshold_deg = 2.0
        self.rotator_command_refresh_sec = 6.0
        self.rotator_settle_hits_required = 2
        self.queue_out = _Queue()


@pytest.mark.asyncio
async def test_tracking_command_uses_negative_azimuth_when_mode_is_negative_range():
    tracker = _DummyTracker("-180_180")
    handler = RotatorHandler(tracker)
    sent = []

    async def _capture_issue(target_az, target_el):
        sent.append((target_az, target_el))

    handler._issue_rotator_command = _capture_issue

    await handler.control_rotator_position((270.0, 45.0))

    assert sent == [(-90.0, 45.0)]


@pytest.mark.asyncio
async def test_tracking_command_stays_0_to_360_in_default_mode():
    tracker = _DummyTracker("0_360")
    handler = RotatorHandler(tracker)
    sent = []

    async def _capture_issue(target_az, target_el):
        sent.append((target_az, target_el))

    handler._issue_rotator_command = _capture_issue

    await handler.control_rotator_position((270.0, 45.0))

    assert sent == [(270.0, 45.0)]


@pytest.mark.asyncio
async def test_manual_command_moves_to_the_exact_requested_position():
    tracker = _DummyTracker("0_360")
    tracker.current_rotator_state = "stopped"
    tracker.nudge_offset = {"az": 2, "el": 0}
    tracker.manual_rotator_target = {"az": 123.4, "el": 45.6}
    handler = RotatorHandler(tracker)
    sent = []

    async def _capture_issue(target_az, target_el):
        sent.append((target_az, target_el))

    handler._issue_rotator_command = _capture_issue

    await handler.control_rotator_position((0.0, 0.0))

    assert sent == [(123.4, 45.6)]
    assert tracker.manual_rotator_target is None
    assert tracker.nudge_offset == {"az": 0, "el": 0}


@pytest.mark.asyncio
async def test_manual_stop_cancels_a_queued_replacement_target_before_stopping():
    tracker = _DummyTracker("0_360")
    tracker.current_rotator_state = "stopped"
    tracker.manual_rotator_target = {"az": 123.4, "el": 45.6}
    tracker.nudge_offset = {"az": 2, "el": 2}
    tracker.manual_rotator_stop_requested = True
    stopped = []

    class _Controller:
        async def stop(self):
            stopped.append(True)
            return True

    tracker.rotator_controller = _Controller()
    tracker.rotator_command_state.update(
        {
            "in_flight": True,
            "target_az": 120.0,
            "target_el": 45.0,
        }
    )
    handler = RotatorHandler(tracker)

    await handler.control_rotator_position((0.0, 0.0))

    assert stopped == [True]
    assert tracker.manual_rotator_target is None
    assert tracker.nudge_offset == {"az": 0, "el": 0}
    assert tracker.rotator_command_state["in_flight"] is False
    assert tracker.rotator_data["slewing"] is False
    assert tracker.rotator_data["stopped"] is False
    assert tracker.rotator_data["motion_unconfirmed"] is True
    assert len(tracker.queue_out.items) == 1


@pytest.mark.asyncio
async def test_manual_move_reports_slewing_until_live_position_reaches_target():
    tracker = _DummyTracker("0_360")
    tracker.current_rotator_state = "stopped"
    tracker.rotator_command_state.update(
        {
            "in_flight": True,
            "target_az": 120.0,
            "target_el": 45.0,
        }
    )
    handler = RotatorHandler(tracker)

    await handler.control_rotator_position((0.0, 0.0))

    assert tracker.rotator_data["slewing"] is True
    assert tracker.rotator_command_state["in_flight"] is True

    tracker.rotator_data.update({"az": 120.0, "el": 45.0})
    await handler.control_rotator_position((0.0, 0.0))

    assert tracker.rotator_data["slewing"] is False
    assert tracker.rotator_data["stopped"] is True
    assert tracker.rotator_command_state["in_flight"] is False


@pytest.mark.asyncio
async def test_manual_command_is_discarded_when_tracking_has_started():
    tracker = _DummyTracker("0_360")
    tracker.manual_rotator_target = {"az": 123.4, "el": 45.6}
    handler = RotatorHandler(tracker)
    sent = []

    async def _capture_issue(target_az, target_el):
        sent.append((target_az, target_el))

    handler._issue_rotator_command = _capture_issue

    await handler.control_rotator_position((90.0, 45.0))

    assert sent == [(90.0, 45.0)]
    assert tracker.manual_rotator_target is None


@pytest.mark.asyncio
async def test_manual_command_for_overlap_rotators_is_limited_to_360_degrees():
    tracker = _DummyTracker("0_450")
    tracker.current_rotator_state = "stopped"
    tracker.manual_rotator_target = {"az": 120.0, "el": 45.0}
    handler = RotatorHandler(tracker)
    sent = []

    async def _capture_issue(target_az, target_el):
        sent.append((target_az, target_el))

    handler._issue_rotator_command = _capture_issue

    await handler.control_rotator_position((0.0, 0.0))

    assert sent == [(120.0, 45.0)]
    assert tracker.manual_rotator_target is None


@pytest.mark.asyncio
async def test_manual_command_rejects_overlap_lane_for_overlap_rotators():
    tracker = _DummyTracker("0_450")
    tracker.current_rotator_state = "stopped"
    tracker.manual_rotator_target = {"az": 361.0, "el": 45.0}
    handler = RotatorHandler(tracker)
    sent = []

    async def _capture_issue(target_az, target_el):
        sent.append((target_az, target_el))

    handler._issue_rotator_command = _capture_issue

    await handler.control_rotator_position((0.0, 0.0))

    assert sent == []
    assert tracker.manual_rotator_target is None


@pytest.mark.asyncio
async def test_manual_command_is_discarded_when_rotator_is_parked():
    tracker = _DummyTracker("0_360")
    tracker.current_rotator_state = "parked"
    tracker.manual_rotator_target = {"az": 120.0, "el": 45.0}
    handler = RotatorHandler(tracker)
    sent = []

    async def _capture_issue(target_az, target_el):
        sent.append((target_az, target_el))

    handler._issue_rotator_command = _capture_issue

    await handler.control_rotator_position((0.0, 0.0))

    assert sent == []
    assert tracker.manual_rotator_target is None


@pytest.mark.asyncio
async def test_tracking_command_uses_overlap_candidate_when_mode_is_0_450():
    tracker = _DummyTracker("0_450")
    tracker.rotator_data["az"] = 430.0
    handler = RotatorHandler(tracker)
    sent = []

    async def _capture_issue(target_az, target_el):
        sent.append((target_az, target_el))

    handler._issue_rotator_command = _capture_issue

    await handler.control_rotator_position((20.0, 45.0))

    assert sent == [(380.0, 45.0)]


@pytest.mark.asyncio
async def test_overlap_mode_does_not_switch_to_high_lane_mid_pass():
    tracker = _DummyTracker("0_450")
    tracker.rotator_data["az"] = 42.0
    handler = RotatorHandler(tracker)
    sent = []

    async def _capture_issue(target_az, target_el):
        sent.append((target_az, target_el))

    handler._issue_rotator_command = _capture_issue

    # This is the issue #30 video path. A late switch to +360 would cost a
    # nearly full rotation, so live samples must remain on the low lane.
    await handler.control_rotator_position((50.0, 45.0))
    await handler.control_rotator_position((45.0, 45.0))
    await handler.control_rotator_position((40.0, 45.0))

    assert sent == [(50.0, 45.0), (45.0, 45.0), (40.0, 45.0)]
    assert tracker.rotator_command_state.get("overlap_lane") is None


@pytest.mark.asyncio
async def test_overlap_mode_prepositions_high_lane_before_decreasing_north_crossing():
    tracker = _DummyTracker("0_450")
    tracker.rotator_data["az"] = 42.0
    handler = RotatorHandler(tracker)
    sent = []

    async def _capture_issue(target_az, target_el):
        sent.append((target_az, target_el))

    handler._issue_rotator_command = _capture_issue
    handler._plan_overlap_lane_for_pass(
        {
            "crosses_north": True,
            "start_azimuth": 50.0,
            "end_azimuth": 355.0,
        },
        current_bearing_az=50.0,
        current_elevation=5.0,
    )

    await handler.control_rotator_position((50.0, 5.0))
    await handler.control_rotator_position((40.0, 5.0))

    assert sent == [(410.0, 5.0), (400.0, 5.0)]
    assert tracker.rotator_command_state["overlap_lane"] == 1


def test_overlap_mode_does_not_plan_high_lane_when_preposition_is_impossible():
    tracker = _DummyTracker("0_450")
    handler = RotatorHandler(tracker)

    handler._plan_overlap_lane_for_pass(
        {
            "crosses_north": True,
            "start_azimuth": 132.0,
            "end_azimuth": 355.0,
        },
        current_bearing_az=132.0,
        current_elevation=5.0,
    )

    # 132° has no equivalent position in a 0–450° range (492° is invalid).
    assert tracker.rotator_command_state["overlap_lane"] is None


def test_overlap_mode_plans_once_from_the_predicted_pass(monkeypatch):
    tracker = _DummyTracker("0_450")
    tracker.current_norad_id = 12345
    handler = RotatorHandler(tracker)
    calls = []
    now = datetime.now(timezone.utc)

    def _predicted_passes(**kwargs):
        calls.append(kwargs)
        return {
            "success": True,
            "data": [
                {
                    "event_start": (now - timedelta(minutes=1)).isoformat(),
                    "event_end": (now + timedelta(minutes=4)).isoformat(),
                    "crosses_north": True,
                    "start_azimuth": 50.0,
                    "end_azimuth": 355.0,
                }
            ],
        }

    monkeypatch.setattr("tracker.rotatorhandler.calculate_next_events", _predicted_passes)

    satellite = {
        "norad_id": 12345,
        "tle1": "1 12345U 00000A",
        "tle2": "2 12345  90.0000",
    }
    handler.plan_overlap_lane(satellite, {"lat": 52.0, "lon": 13.0}, (50.0, 5.0))
    handler.plan_overlap_lane(satellite, {"lat": 52.0, "lon": 13.0}, (50.0, 5.0))

    assert len(calls) == 1
    assert tracker.rotator_command_state["overlap_lane"] == 1


def test_overlap_mode_replans_when_tle_changes(monkeypatch):
    tracker = _DummyTracker("0_450")
    tracker.current_norad_id = 12345
    handler = RotatorHandler(tracker)
    calls = []
    now = datetime.now(timezone.utc)

    def _predicted_passes(**kwargs):
        calls.append(kwargs)
        return {
            "success": True,
            "data": [
                {
                    "event_start": (now - timedelta(minutes=1)).isoformat(),
                    "event_end": (now + timedelta(minutes=4)).isoformat(),
                    "crosses_north": False,
                    "start_azimuth": 50.0,
                    "end_azimuth": 355.0,
                }
            ],
        }

    monkeypatch.setattr("tracker.rotatorhandler.calculate_next_events", _predicted_passes)

    initial_satellite = {
        "norad_id": 12345,
        "tle1": "1 12345U 00000A",
        "tle2": "2 12345  90.0000",
    }
    updated_satellite = {**initial_satellite, "tle1": "1 12345U 00000B"}

    handler.plan_overlap_lane(initial_satellite, {"lat": 52.0, "lon": 13.0}, (50.0, 5.0))
    handler.plan_overlap_lane(updated_satellite, {"lat": 52.0, "lon": 13.0}, (50.0, 5.0))

    assert len(calls) == 2


def test_overlap_mode_plans_omm_only_pass_and_replans_on_element_change(monkeypatch):
    tracker = _DummyTracker("0_450")
    tracker.current_norad_id = 100000
    handler = RotatorHandler(tracker)
    calls = []
    now = datetime.now(timezone.utc)

    def _predicted_passes(**kwargs):
        calls.append(kwargs)
        return {
            "success": True,
            "data": [
                {
                    "event_start": (now - timedelta(minutes=1)).isoformat(),
                    "event_end": (now + timedelta(minutes=4)).isoformat(),
                    "crosses_north": True,
                    "start_azimuth": 50.0,
                    "end_azimuth": 355.0,
                }
            ],
        }

    monkeypatch.setattr("tracker.rotatorhandler.calculate_next_events", _predicted_passes)
    satellite = {
        "norad_id": 100000,
        "orbit_format": "omm",
        "orbit_payload": {"EPOCH": "2026-10-23T00:00:00", "INCLINATION": "51.6"},
    }
    location = {"lat": 52.0, "lon": 13.0}

    handler.plan_overlap_lane(satellite, location, (50.0, 5.0))
    handler.plan_overlap_lane(satellite, location, (50.0, 5.0))
    assert len(calls) == 1
    assert calls[0]["satellite_data"] == satellite
    assert tracker.rotator_command_state["overlap_lane"] == 1

    changed = {**satellite, "orbit_payload": {**satellite["orbit_payload"], "INCLINATION": "52.0"}}
    handler.plan_overlap_lane(changed, location, (50.0, 5.0))
    assert len(calls) == 2


def test_overlap_mode_throttles_retry_when_no_pass_is_forecast(monkeypatch):
    tracker = _DummyTracker("0_450")
    tracker.current_norad_id = 12345
    handler = RotatorHandler(tracker)
    calls = []

    def _predicted_passes(**kwargs):
        calls.append(kwargs)
        return {"success": True, "data": []}

    monkeypatch.setattr("tracker.rotatorhandler.calculate_next_events", _predicted_passes)
    satellite = {
        "norad_id": 12345,
        "tle1": "1 12345U 00000A",
        "tle2": "2 12345  90.0000",
    }

    handler.plan_overlap_lane(satellite, {"lat": 52.0, "lon": 13.0}, (50.0, 5.0))
    handler.plan_overlap_lane(satellite, {"lat": 52.0, "lon": 13.0}, (50.0, 5.0))

    assert len(calls) == 1
    assert tracker.rotator_command_state["overlap_plan_retry_at"] > time.time()


def test_overlap_mode_retains_plan_across_rotator_state_transition(monkeypatch):
    tracker = _DummyTracker("0_450")
    tracker.current_norad_id = 12345
    handler = RotatorHandler(tracker)
    calls = []
    now = datetime.now(timezone.utc)

    def _predicted_passes(**kwargs):
        calls.append(kwargs)
        return {
            "success": True,
            "data": [
                {
                    "event_start": (now - timedelta(minutes=1)).isoformat(),
                    "event_end": (now + timedelta(minutes=4)).isoformat(),
                    "crosses_north": True,
                    "start_azimuth": 50.0,
                    "end_azimuth": 355.0,
                }
            ],
        }

    monkeypatch.setattr("tracker.rotatorhandler.calculate_next_events", _predicted_passes)
    satellite = {
        "norad_id": 12345,
        "tle1": "1 12345U 00000A",
        "tle2": "2 12345  90.0000",
    }

    handler.plan_overlap_lane(satellite, {"lat": 52.0, "lon": 13.0}, (50.0, 5.0))
    handler._clear_overlap_lane_state()
    handler.plan_overlap_lane(satellite, {"lat": 52.0, "lon": 13.0}, (50.0, 5.0))

    assert len(calls) == 1
    assert tracker.rotator_command_state["overlap_lane"] == 1


def test_overlap_mode_replans_after_planned_pass_ends(monkeypatch):
    tracker = _DummyTracker("0_450")
    tracker.current_norad_id = 12345
    handler = RotatorHandler(tracker)
    calls = []
    now = datetime.now(timezone.utc)

    def _predicted_passes(**kwargs):
        calls.append(kwargs)
        return {
            "success": True,
            "data": [
                {
                    "event_start": (now - timedelta(minutes=1)).isoformat(),
                    "event_end": (now + timedelta(minutes=4)).isoformat(),
                    "crosses_north": False,
                    "start_azimuth": 50.0,
                    "end_azimuth": 355.0,
                }
            ],
        }

    monkeypatch.setattr("tracker.rotatorhandler.calculate_next_events", _predicted_passes)
    satellite = {
        "norad_id": 12345,
        "tle1": "1 12345U 00000A",
        "tle2": "2 12345  90.0000",
    }

    handler.plan_overlap_lane(satellite, {"lat": 52.0, "lon": 13.0}, (50.0, 5.0))
    tracker.rotator_command_state["overlap_plan_pass_end"] = time.time() - 1.0
    handler.plan_overlap_lane(satellite, {"lat": 52.0, "lon": 13.0}, (50.0, 5.0))

    assert len(calls) == 2


@pytest.mark.asyncio
async def test_overlap_mode_keeps_locked_high_lane_until_ambiguity_ends():
    tracker = _DummyTracker("0_450")
    tracker.rotator_data["az"] = 385.0
    tracker.rotator_command_state["overlap_lane"] = 1
    handler = RotatorHandler(tracker)
    sent = []

    async def _capture_issue(target_az, target_el):
        sent.append((target_az, target_el))

    handler._issue_rotator_command = _capture_issue

    # Ambiguous bearing keeps the locked +360 lane.
    await handler.control_rotator_position((30.0, 45.0))
    assert sent[-1] == (390.0, 45.0)
    assert tracker.rotator_command_state["overlap_lane"] == 1

    # Once bearing is no longer ambiguous in 0_450, lock is cleared.
    await handler.control_rotator_position((350.0, 45.0))
    assert sent[-1] == (350.0, 45.0)
    assert tracker.rotator_command_state["overlap_lane"] is None


@pytest.mark.asyncio
async def test_overlap_mode_does_not_lock_high_lane_for_ccw_trend():
    tracker = _DummyTracker("0_450")
    tracker.rotator_data["az"] = 25.0
    handler = RotatorHandler(tracker)
    sent = []

    async def _capture_issue(target_az, target_el):
        sent.append((target_az, target_el))

    handler._issue_rotator_command = _capture_issue

    # Counterclockwise trend near north should not force +360 pre-positioning.
    await handler.control_rotator_position((20.0, 45.0))
    await handler.control_rotator_position((25.0, 45.0))
    await handler.control_rotator_position((30.0, 45.0))

    assert sent == [(20.0, 45.0), (25.0, 45.0), (30.0, 45.0)]
    assert tracker.rotator_command_state.get("overlap_lane") is None


def test_target_within_tolerance_handles_wraparound():
    tracker = _DummyTracker("0_360")
    handler = RotatorHandler(tracker)

    tracker.az_tolerance = 3.0
    tracker.el_tolerance = 2.0

    assert handler._target_within_tolerance(359.0, 45.0, 1.0, 45.0)


def test_target_within_tolerance_handles_mixed_azimuth_representations():
    tracker = _DummyTracker("-180_180")
    handler = RotatorHandler(tracker)

    tracker.az_tolerance = 2.0
    tracker.el_tolerance = 2.0

    assert handler._target_within_tolerance(270.0, 20.0, -90.0, 20.0)


def test_target_within_tolerance_in_overlap_mode_uses_absolute_distance():
    tracker = _DummyTracker("0_450")
    handler = RotatorHandler(tracker)

    tracker.az_tolerance = 2.0
    tracker.el_tolerance = 2.0

    assert not handler._target_within_tolerance(85.0, 20.0, 445.0, 20.0)
    assert handler._target_within_tolerance(444.5, 20.0, 445.0, 20.0)


@pytest.mark.asyncio
async def test_in_flight_command_settles_across_0_360_boundary():
    tracker = _DummyTracker("0_360")
    handler = RotatorHandler(tracker)

    tracker.rotator_data["az"] = 359.0
    tracker.rotator_data["el"] = 45.0
    tracker.rotator_command_state.update(
        {
            "in_flight": True,
            "target_az": 1.0,
            "target_el": 45.0,
            "last_command_ts": time.time(),
            "settle_hits": 0,
        }
    )
    tracker.rotator_settle_hits_required = 2
    tracker.rotator_retarget_threshold_deg = 999.0
    tracker.rotator_command_refresh_sec = 999.0

    async def _noop_issue(target_az, target_el):
        return None

    handler._issue_rotator_command = _noop_issue

    await handler.control_rotator_position((1.0, 45.0))
    await handler.control_rotator_position((1.0, 45.0))

    assert tracker.rotator_command_state["in_flight"] is False
    assert tracker.rotator_data["slewing"] is False


@pytest.mark.asyncio
async def test_in_flight_marks_not_slewing_immediately_when_target_is_reached():
    tracker = _DummyTracker("0_360")
    handler = RotatorHandler(tracker)

    tracker.rotator_data["az"] = 100.0
    tracker.rotator_data["el"] = 30.0
    tracker.rotator_data["slewing"] = True
    tracker.rotator_command_state.update(
        {
            "in_flight": True,
            "target_az": 100.0,
            "target_el": 30.0,
            "last_command_ts": time.time(),
            "settle_hits": 0,
        }
    )
    tracker.rotator_settle_hits_required = 2
    tracker.rotator_retarget_threshold_deg = 999.0
    tracker.rotator_command_refresh_sec = 999.0

    async def _noop_issue(target_az, target_el):
        return None

    handler._issue_rotator_command = _noop_issue

    await handler.control_rotator_position((100.0, 30.0))

    assert tracker.rotator_command_state["in_flight"] is True
    assert tracker.rotator_command_state["settle_hits"] == 1
    assert tracker.rotator_data["slewing"] is False


@pytest.mark.asyncio
async def test_in_flight_settle_completion_does_not_reissue_due_to_refresh():
    tracker = _DummyTracker("0_360")
    handler = RotatorHandler(tracker)

    tracker.rotator_data["az"] = 120.0
    tracker.rotator_data["el"] = 35.0
    tracker.rotator_data["slewing"] = True
    tracker.rotator_command_state.update(
        {
            "in_flight": True,
            "target_az": 120.0,
            "target_el": 35.0,
            "last_command_ts": time.time(),
            "settle_hits": 1,
        }
    )
    tracker.rotator_settle_hits_required = 2
    tracker.rotator_command_refresh_sec = 6.0

    issued = []

    async def _capture_issue(target_az, target_el):
        issued.append((target_az, target_el))

    handler._issue_rotator_command = _capture_issue

    await handler.control_rotator_position((120.0, 35.0))

    assert issued == []
    assert tracker.rotator_command_state["in_flight"] is False
    assert tracker.rotator_data["slewing"] is False


@pytest.mark.asyncio
async def test_state_change_to_tracking_does_not_force_connected_flags_on_connect_failure():
    tracker = _DummyTracker("0_360")
    tracker.rotator_controller = None
    tracker.rotator_data.update(
        {
            "connected": False,
            "tracking": False,
            "stopped": True,
            "parked": False,
            "error": True,
        }
    )
    handler = RotatorHandler(tracker)

    async def _failed_connect():
        tracker.rotator_controller = None
        tracker.rotator_data["connected"] = False
        tracker.rotator_data["error"] = True

    handler.connect_to_rotator = _failed_connect

    await handler.handle_rotator_state_change("disconnected", "tracking")

    assert tracker.rotator_data["connected"] is False
    assert tracker.rotator_data["tracking"] is False


@pytest.mark.asyncio
async def test_update_hardware_position_unwraps_overlap_reading_to_absolute_turn():
    tracker = _DummyTracker("0_450")
    tracker.rotator_data["az"] = 430.0

    class _Controller:
        async def get_position(self):
            return 10.0, 35.0

    tracker.rotator_controller = _Controller()
    handler = RotatorHandler(tracker)

    await handler.update_hardware_position()

    assert tracker.rotator_data["az"] == 370.0
    assert tracker.rotator_data["el"] == 35.0


@pytest.mark.asyncio
async def test_update_hardware_position_preserves_absolute_overlap_reading():
    tracker = _DummyTracker("0_450")
    tracker.rotator_data["az"] = 10.0

    class _Controller:
        async def get_position(self):
            return 370.0, 35.0

    tracker.rotator_controller = _Controller()
    handler = RotatorHandler(tracker)

    await handler.update_hardware_position()

    assert tracker.rotator_data["az"] == 370.0
    assert tracker.rotator_data["el"] == 35.0


@pytest.mark.asyncio
async def test_update_hardware_position_keeps_wrapped_overlap_reading_on_cold_start():
    tracker = _DummyTracker("0_450")
    tracker.rotator_data["az"] = None

    class _Controller:
        async def get_position(self):
            return 20.0, 35.0

    tracker.rotator_controller = _Controller()
    handler = RotatorHandler(tracker)

    await handler.update_hardware_position()

    assert tracker.rotator_data["az"] == 20.0
    assert tracker.rotator_data["el"] == 35.0


@pytest.mark.asyncio
async def test_overlap_mode_reports_error_when_extended_command_is_rejected():
    tracker = _DummyTracker("0_450")
    tracker.rotator_data["az"] = 430.0
    sent = []

    class _Controller:
        async def set_position(self, target_az, target_el):
            sent.append((target_az, target_el))
            raise RuntimeError("Failed to set position: RPRT -17")
            yield

    tracker.rotator_controller = _Controller()
    handler = RotatorHandler(tracker)

    await handler.control_rotator_position((20.0, 45.0))

    assert sent == [(380.0, 45.0)]
    assert tracker.rotator_data["error"] is True
    assert tracker.rotator_data["stopped"] is True
    assert tracker.rotator_data["outofbounds"] is True
    assert tracker.rotator_command_state["in_flight"] is False
    assert len(tracker.queue_out.items) == 1
