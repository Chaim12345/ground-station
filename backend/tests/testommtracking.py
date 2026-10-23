"""Six-digit OMM coverage at the tracking boundary."""

import math
from datetime import datetime, timezone
from queue import Queue

import pytest

from orbits import CentralBody, build_satellite_ephemeris_payload, get_propagation_input
from tracker.ipc import TRACKER_MSG_SET_SATELLITE_EPHEMERIS
from tracker.logic import SatelliteTracker
from tracking.doppler import (
    calculate_doppler_shift_from_range_rate,
    calculate_range_rate_from_propagation,
)
from tracking.satellite import get_satellite_az_el_from_propagation

OMM_PAYLOAD = {
    "OBJECT_NAME": "New catalogue object",
    "OBJECT_ID": "2025-001A",
    "EPOCH": "2025-01-01T12:00:00.000000",
    "MEAN_MOTION": "15.50000000",
    "ECCENTRICITY": "0.0006703",
    "INCLINATION": "51.6416",
    "RA_OF_ASC_NODE": "247.4627",
    "ARG_OF_PERICENTER": "130.5360",
    "MEAN_ANOMALY": "325.0288",
    "EPHEMERIS_TYPE": "0",
    "CLASSIFICATION_TYPE": "U",
    "NORAD_CAT_ID": "100000",
    "ELEMENT_SET_NO": "999",
    "REV_AT_EPOCH": "12345",
    "BSTAR": "0.00021914",
    "MEAN_MOTION_DOT": "0.00012345",
    "MEAN_MOTION_DDOT": "0.0",
}


def test_six_digit_omm_doppler_changes_sign_across_orbit():
    propagation_input = get_propagation_input(
        {
            "norad_id": 100000,
            "name": "New catalogue object",
            "orbit_format": "omm",
            "orbit_payload": OMM_PAYLOAD,
        },
        central_body=CentralBody.EARTH,
    )
    nominal_freq = 436_888_000
    receding_rate = calculate_range_rate_from_propagation(
        propagation_input,
        37.9838,
        23.7275,
        0,
        datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
    )
    approaching_rate = calculate_range_rate_from_propagation(
        propagation_input,
        37.9838,
        23.7275,
        0,
        datetime(2025, 1, 1, 13, 0, tzinfo=timezone.utc),
    )
    receding_freq, _ = calculate_doppler_shift_from_range_rate(receding_rate, nominal_freq)
    approaching_freq, _ = calculate_doppler_shift_from_range_rate(approaching_rate, nominal_freq)

    assert receding_rate > 0
    assert approaching_rate < 0
    assert receding_freq < nominal_freq < approaching_freq


def test_tracking_azimuth_and_elevation_accept_omm_without_tle():
    satellite = {
        "norad_id": 100000,
        "name": "New catalogue object",
        "orbit_format": "omm",
        "orbit_payload": OMM_PAYLOAD,
    }
    propagation_input = get_propagation_input(satellite, central_body=CentralBody.EARTH)

    azimuth, elevation = get_satellite_az_el_from_propagation(
        37.9838,
        23.7275,
        propagation_input,
        datetime(2026, 8, 26, tzinfo=timezone.utc),
    )

    assert isinstance(azimuth, float)
    assert isinstance(elevation, float)


def test_tracker_compiles_six_digit_omm_ephemeris_without_tle():
    satellite = {
        "norad_id": 100000,
        "name": "New catalogue object",
        "orbit_format": "omm",
        "orbit_payload": OMM_PAYLOAD,
    }
    # The manager sends this payload to the worker, which must be able to
    # propagate it without TLE lines before commanding the rotator.
    payload = build_satellite_ephemeris_payload(satellite)

    tracker = SatelliteTracker(Queue(), Queue(), tracker_id="target-1")
    tracker.apply_input_message({"type": TRACKER_MSG_SET_SATELLITE_EPHEMERIS, "payload": payload})
    location = {"lat": 37.9838, "lon": 23.7275, "alt": 0}
    tracker.input_location = location
    tracker.input_map_settings = {"orbitProjectionDuration": 1}
    context = tracker._resolve_target_context(
        {"target_type": "satellite", "norad_id": 100000}, location
    )

    assert context is not None
    assert context["satellite_data"]["error"] is False
    assert context["satellite_data"]["details"]["norad_id"] == 100000
    assert context["satellite_tles"] is None
    assert math.isfinite(context["range_rate_km_s"])
    assert context["range_rate_km_s"] != 0
    assert all(math.isfinite(value) for value in context["skypoint"])
    assert all(math.isfinite(value) for value in context["command_skypoint"])


@pytest.mark.asyncio
async def test_omm_range_rate_reaches_transmitter_telemetry():
    satellite = {
        "norad_id": 100000,
        "name": "New catalogue object",
        "orbit_format": "omm",
        "orbit_payload": OMM_PAYLOAD,
    }
    tracker = SatelliteTracker(Queue(), Queue(), tracker_id="obs-six-digit")
    tracker.apply_input_message(
        {
            "type": TRACKER_MSG_SET_SATELLITE_EPHEMERIS,
            "payload": build_satellite_ephemeris_payload(satellite),
        }
    )
    tracker.input_location = {"lat": 37.9838, "lon": 23.7275, "alt": 0}
    tracker.input_map_settings = {"orbitProjectionDuration": 1}
    tracker.input_transmitters = [
        {"id": "flock-tx", "downlink_low": 436_888_000, "uplink_low": 145_900_000}
    ]
    tracker.current_transmitter_id = "flock-tx"
    context = tracker._resolve_target_context(
        {"target_type": "satellite", "norad_id": 100000}, tracker.input_location
    )
    assert context is not None
    assert context["satellite_tles"] is None

    await tracker.rig_handler.update_transmitter_doppler(range_rate_km_s=context["range_rate_km_s"])
    telemetry = tracker.rig_data["transmitters"][0]
    assert telemetry["downlink_observed_freq"] != 436_888_000
    assert telemetry["doppler_shift"] != 0
    assert telemetry["uplink_observed_freq"] != 145_900_000
