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

"""
Tests for tracking/radar.py vectorized radar-view geometry.
"""

from datetime import datetime, timezone

import pytest
from skyfield.api import EarthSatellite, load, wgs84

from tracking.radar import compute_radar_positions

# Real ISS TLE (epoch 2023-04-19, same fixture as test_tracking_doppler.py)
ISS_TLE1 = "1 25544U 98067A   23109.65481637  .00012345  00000-0  21914-3 0  9997"
ISS_TLE2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.72125391563537"
ISS_NORAD = 25544

# Fixed epoch close to the TLE epoch so sgp4 propagation is well conditioned
EPOCH = datetime(2023, 4, 19, 15, 45, 0, tzinfo=timezone.utc)


def _iss_subpoint():
    """Independent (skyfield) geodetic sub-satellite point at EPOCH."""
    ts = load.timescale()
    satellite = EarthSatellite(ISS_TLE1, ISS_TLE2, "ISS (ZARYA)", ts)
    t = ts.from_datetime(EPOCH)
    subpoint = wgs84.subpoint(satellite.at(t))
    return subpoint.latitude.degrees, subpoint.longitude.degrees


@pytest.fixture
def iss_target():
    return {"norad_id": ISS_NORAD, "tle1": ISS_TLE1, "tle2": ISS_TLE2}


def test_high_elevation_station_bounds(iss_target):
    """Station at the sub-satellite point sees it near zenith, range ~ altitude."""
    lat, lon = _iss_subpoint()
    stations = [{"id": "sub", "lat": lat, "lon": lon, "alt_km": 0.0}]

    positions, max_ranges = compute_radar_positions([iss_target], stations, EPOCH)

    assert "sub" in positions
    entries = positions["sub"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["norad_id"] == ISS_NORAD
    assert 0.0 <= entry["az"] < 360.0
    assert -90.0 <= entry["el"] <= 90.0
    # Near zenith: elevation well above 60 deg, slant range close to ISS altitude
    assert entry["el"] > 60.0
    assert 300.0 <= entry["range_km"] <= 2000.0
    assert max_ranges[ISS_NORAD] == pytest.approx(entry["range_km"], abs=1.0)


def test_station_east_of_subpoint_sees_satellite(iss_target):
    """A station ~500 km east of the sub-point still has the satellite above
    the horizon, looking roughly westward."""
    lat, lon = _iss_subpoint()
    # 500 km east at this latitude (cos(lat) accounts for longitude shrink)
    import math

    lon_east = lon + 500.0 / (111.32 * math.cos(math.radians(lat)))
    stations = [
        {"id": "east", "lat": lat, "lon": lon_east, "alt_km": 0.0},
        {"id": "far", "lat": -80.0, "lon": lon + 180.0, "alt_km": 0.0},
    ]

    positions, _ = compute_radar_positions([iss_target], stations, EPOCH)

    assert "east" in positions
    entry = positions["east"][0]
    assert entry["el"] > 0.0
    # Satellite is west of the station
    assert 180.0 < entry["az"] < 360.0
    # The antipodal-ish far station must not appear at all
    assert "far" not in positions


def test_min_elevation_filter(iss_target):
    """Raising the threshold drops the low-elevation station but keeps zenith."""
    lat, lon = _iss_subpoint()
    import math

    lon_east = lon + 500.0 / (111.32 * math.cos(math.radians(lat)))
    stations = [
        {"id": "sub", "lat": lat, "lon": lon, "alt_km": 0.0},
        {"id": "east", "lat": lat, "lon": lon_east, "alt_km": 0.0},
    ]

    positions, _ = compute_radar_positions([iss_target], stations, EPOCH, min_elevation_deg=60.0)

    assert "sub" in positions
    assert "east" not in positions


def test_bad_tle_skipped():
    """Unparseable TLEs are skipped instead of raising."""
    targets = [
        {"norad_id": 1, "tle1": "garbage", "tle2": "garbage"},
        {"norad_id": ISS_NORAD, "tle1": ISS_TLE1, "tle2": ISS_TLE2},
    ]
    stations = [{"id": "sub", "lat": 0.0, "lon": 0.0, "alt_km": 0.0}]

    positions, max_ranges = compute_radar_positions(targets, stations, EPOCH)

    assert 1 not in max_ranges
    assert ISS_NORAD in max_ranges
