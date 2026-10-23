# Copyright (c) 2026 Efstratios Goudelis
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

"""Tests for tracker satellite-path cache refresh behavior."""

from datetime import datetime, timedelta, timezone

import pytest

from orbits import CentralBody, get_propagation_input
from tracker.data import (
    _orbit_cache_keys,
    _resolve_min_future_buffer_seconds,
    cache_manager,
    cache_satellite_paths,
    get_cached_satellite_paths,
)

TLE_LINE_1 = "1 25544U 98067A   25001.50000000  .00012345  00000-0  21914-3 0  9999"
TLE_LINE_2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.50000000999999"


def test_omm_path_cache_key_changes_with_inclination():
    from tests.testommtracking import OMM_PAYLOAD

    def orbit_keys(payload):
        propagation = get_propagation_input(
            {"norad_id": 100000, "orbit_format": "omm", "orbit_payload": payload},
            central_body=CentralBody.EARTH,
        )
        return _orbit_cache_keys(propagation)

    assert orbit_keys(OMM_PAYLOAD) != orbit_keys({**OMM_PAYLOAD, "INCLINATION": "52.0"})


@pytest.fixture(autouse=True)
def _clear_cache():
    cache_manager._cache.clear()
    yield
    cache_manager._cache.clear()


def _cache_key(duration_minutes: int, step_minutes: float) -> str:
    return cache_manager._generate_cache_key(
        "satellite_paths",
        tle1=TLE_LINE_1,
        tle2=TLE_LINE_2,
        duration=duration_minutes,
        step=step_minutes,
        schema=2,
    )


def test_satellite_paths_cache_hit_returns_cached_data():
    paths = {"past": [[{"lat": 1.0, "lon": 2.0}]], "future": [[{"lat": 3.0, "lon": 4.0}]]}
    duration_minutes = 120
    step_minutes = 0.5

    cache_satellite_paths(
        TLE_LINE_1,
        TLE_LINE_2,
        duration_minutes=duration_minutes,
        step_minutes=step_minutes,
        paths=paths,
        ttl_minutes=30,
    )

    cached = get_cached_satellite_paths(
        TLE_LINE_1, TLE_LINE_2, duration_minutes=duration_minutes, step_minutes=step_minutes
    )
    assert cached == paths


def test_satellite_paths_cache_refreshes_before_future_window_is_fully_consumed():
    paths = {"past": [[{"lat": 1.0, "lon": 2.0}]], "future": [[{"lat": 3.0, "lon": 4.0}]]}
    duration_minutes = 120
    step_minutes = 0.5

    cache_satellite_paths(
        TLE_LINE_1,
        TLE_LINE_2,
        duration_minutes=duration_minutes,
        step_minutes=step_minutes,
        paths=paths,
        ttl_minutes=30,
    )

    key = _cache_key(duration_minutes, step_minutes)
    now_utc = datetime.now(timezone.utc)
    buffer_seconds = _resolve_min_future_buffer_seconds(step_minutes)

    # Move cache creation time close enough to path-end so the early refresh rule triggers.
    cache_manager._cache[key]["created_at"] = (
        now_utc - timedelta(minutes=duration_minutes) + timedelta(seconds=buffer_seconds - 10)
    )
    cache_manager._cache[key]["expires_at"] = now_utc + timedelta(minutes=10)

    cached = get_cached_satellite_paths(
        TLE_LINE_1, TLE_LINE_2, duration_minutes=duration_minutes, step_minutes=step_minutes
    )
    assert cached is None
    assert key not in cache_manager._cache


def test_satellite_paths_cache_kept_when_future_window_still_has_safe_buffer():
    paths = {"past": [[{"lat": 1.0, "lon": 2.0}]], "future": [[{"lat": 3.0, "lon": 4.0}]]}
    duration_minutes = 120
    step_minutes = 0.5

    cache_satellite_paths(
        TLE_LINE_1,
        TLE_LINE_2,
        duration_minutes=duration_minutes,
        step_minutes=step_minutes,
        paths=paths,
        ttl_minutes=30,
    )

    key = _cache_key(duration_minutes, step_minutes)
    now_utc = datetime.now(timezone.utc)
    buffer_seconds = _resolve_min_future_buffer_seconds(step_minutes)

    # Keep enough future headroom so we can still reuse the cached path.
    cache_manager._cache[key]["created_at"] = (
        now_utc - timedelta(minutes=duration_minutes) + timedelta(seconds=buffer_seconds + 10)
    )
    cache_manager._cache[key]["expires_at"] = now_utc + timedelta(minutes=10)

    cached = get_cached_satellite_paths(
        TLE_LINE_1, TLE_LINE_2, duration_minutes=duration_minutes, step_minutes=step_minutes
    )
    assert cached == paths
