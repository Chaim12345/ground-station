# Copyright (c) 2025 Efstratios Goudelis
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""Unit tests for the orbit abstraction service."""

import os
import time

import pytest

from crud.satellites import add_satellite
from db.models import SatelliteOrbits
from orbits import (
    CentralBody,
    OrbitModelKind,
    OrbitServiceError,
    build_satellite_ephemeris_payload,
    build_skyfield_satellite,
    get_orbit_state,
    get_propagation_input,
    get_propagation_input_for_object,
)
from orbits.service import _normalize_omm_fields, orbit_service

LEGACY_SATELLITE = {
    "norad_id": 25544,
    "name": "ISS (ZARYA)",
    "tle1": "1 25544U 98067A   25001.50000000  .00012345  00000-0  21914-3 0  9999",
    "tle2": "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.50000000999999",
}

OMM_PAYLOAD = {
    "OBJECT_NAME": "ISS (ZARYA)",
    "OBJECT_ID": "1998-067A",
    "EPOCH": "2025-01-01T12:00:00.000000",
    "MEAN_MOTION": "15.50000000",
    "ECCENTRICITY": "0.0006703",
    "INCLINATION": "51.6416",
    "RA_OF_ASC_NODE": "247.4627",
    "ARG_OF_PERICENTER": "130.5360",
    "MEAN_ANOMALY": "325.0288",
    "EPHEMERIS_TYPE": "0",
    "CLASSIFICATION_TYPE": "U",
    "NORAD_CAT_ID": "25544",
    "ELEMENT_SET_NO": "999",
    "REV_AT_EPOCH": "12345",
    "BSTAR": "0.00021914",
    "MEAN_MOTION_DOT": "0.00012345",
    "MEAN_MOTION_DDOT": "0.0",
}


def test_get_propagation_input_from_legacy_tle_row():
    propagation_input = get_propagation_input(LEGACY_SATELLITE, central_body=CentralBody.EARTH)

    assert propagation_input.object_id == 25544
    assert propagation_input.model_kind == OrbitModelKind.TLE
    assert propagation_input.tle1.startswith("1 25544U")
    assert propagation_input.tle2.startswith("2 25544")


def test_get_propagation_input_from_omm_row_uses_omm_payload():
    omm_row = dict(LEGACY_SATELLITE)
    omm_row["orbit_format"] = "omm"
    omm_row["orbit_payload"] = OMM_PAYLOAD

    propagation_input = get_propagation_input(omm_row, central_body=CentralBody.EARTH)

    assert propagation_input.object_id == 25544
    assert propagation_input.model_kind == OrbitModelKind.OMM
    assert propagation_input.satrec is not None


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="requires POSIX timezone support")
def test_normalize_omm_epoch_treats_naive_timestamp_as_utc():
    """Host-local time must not shift an OMM epoch that has no explicit offset."""
    original_timezone = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "Etc/GMT-3"
        time.tzset()
        normalized = _normalize_omm_fields(OMM_PAYLOAD)
    finally:
        if original_timezone is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_timezone
        time.tzset()

    assert normalized["EPOCH"] == "2025-01-01T12:00:00.000000"


def test_omm_propagation_handles_six_digit_catalogue_number_without_tle():
    omm_row = {
        "norad_id": 100000,
        "name": "New catalogue object",
        "orbit_format": "omm",
        "orbit_payload": {**OMM_PAYLOAD, "NORAD_CAT_ID": "100000", "OBJECT_ID": "2025-001A"},
    }

    propagation_input = get_propagation_input(omm_row, central_body=CentralBody.EARTH)

    assert propagation_input.tle1 is None
    assert propagation_input.tle2 is None
    assert propagation_input.satrec is not None

    from skyfield.api import load

    skyfield_satellite = build_skyfield_satellite(propagation_input, load.timescale())
    assert skyfield_satellite.model.satnum == 100000


def test_six_digit_omm_ephemeris_payload_keeps_canonical_data_without_tle():
    """Tracker/API consumers receive OMM data instead of a lossy TLE export."""
    omm_row = {
        "norad_id": 100000,
        "name": "New catalogue object",
        "orbit_format": "omm",
        "orbit_payload": {**OMM_PAYLOAD, "NORAD_CAT_ID": "100000", "OBJECT_ID": "2025-001A"},
    }

    payload = build_satellite_ephemeris_payload(omm_row, central_body=CentralBody.EARTH)

    assert payload["norad_id"] == 100000
    assert payload["tle1"] is None
    assert payload["tle2"] is None
    assert payload["orbit_format"] == "omm"
    assert payload["orbit_payload"]["NORAD_CAT_ID"] == "100000"


def test_get_propagation_input_rejects_missing_tle_lines():
    invalid_row = {"norad_id": 25544, "name": "ISS", "orbit_format": "tle"}

    with pytest.raises(OrbitServiceError, match="Missing TLE data"):
        get_propagation_input(invalid_row, central_body=CentralBody.EARTH)


def test_build_satellite_ephemeris_payload_preserves_tracker_shape():
    payload = build_satellite_ephemeris_payload(LEGACY_SATELLITE, central_body=CentralBody.EARTH)

    assert payload == {
        "norad_id": 25544,
        "name": "ISS (ZARYA)",
        "tle1": LEGACY_SATELLITE["tle1"],
        "tle2": LEGACY_SATELLITE["tle2"],
    }


def test_get_orbit_state_rejects_non_earth_body_for_now():
    with pytest.raises(OrbitServiceError, match="not implemented yet"):
        get_orbit_state(LEGACY_SATELLITE, central_body=CentralBody.MARS)


@pytest.mark.asyncio
async def test_get_propagation_input_for_object_reads_canonical_orbit_row(db_session):
    await add_satellite(db_session, LEGACY_SATELLITE)
    await db_session.merge(
        SatelliteOrbits(
            satellite_norad_id=25544,
            central_body="earth",
            model_kind="omm",
            tle1="1 25544U 98067A   25002.50000000  .00020000  00000-0  30000-3 0  9999",
            tle2="2 25544  51.6416 248.0000 0006703 130.5360 325.0288 15.50010000999999",
            omm_payload=OMM_PAYLOAD,
        )
    )
    await db_session.commit()

    propagation_input = await get_propagation_input_for_object(
        db_session, 25544, central_body=CentralBody.EARTH
    )

    assert propagation_input.model_kind == OrbitModelKind.OMM
    assert propagation_input.satrec is not None


@pytest.mark.asyncio
async def test_get_orbit_state_from_db_falls_back_to_legacy_satellite_tle_when_no_orbit_row(
    db_session,
):
    await add_satellite(db_session, LEGACY_SATELLITE)
    orbit_state = await orbit_service.get_orbit_state(db_session, 25544, CentralBody.EARTH)

    assert orbit_state.model_kind == OrbitModelKind.TLE
    assert orbit_state.tle1 == LEGACY_SATELLITE["tle1"]
    assert orbit_state.tle2 == LEGACY_SATELLITE["tle2"]
