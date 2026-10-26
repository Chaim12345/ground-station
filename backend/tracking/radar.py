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

"""Live radar-view geometry: az/el/range from many stations to many satellites.

Pure math only (no DB / socket IO) so the functions stay unit testable.
All TLEs are propagated in one vectorized sgp4 call; TEME output is rotated
into ECEF via a GMST rotation about Z (polar motion ignored, plenty accurate
for a live overview map).
"""

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import numpy as np
from sgp4.api import Satrec, SatrecArray, jday

WGS84_A_KM = 6378.1370
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = 2.0 * WGS84_F - WGS84_F * WGS84_F
J2000_JD = 2451545.0


def epoch_to_jday(epoch: datetime) -> Tuple[float, float]:
    """Split a UTC datetime into sgp4's (jd, fr) pair."""
    utc = epoch.astimezone(timezone.utc) if epoch.tzinfo else epoch
    second = utc.second + utc.microsecond / 1_000_000.0
    return jday(utc.year, utc.month, utc.day, utc.hour, utc.minute, second)


def gmst_rad(jd: float) -> float:
    """Greenwich mean sidereal time in radians from a whole Julian date."""
    t = (jd - J2000_JD) / 36525.0
    gmst_deg = (
        280.46061837
        + 360.98564736629 * (jd - J2000_JD)
        + 0.000387933 * t * t
        - t * t * t / 38710000.0
    )
    return math.radians(gmst_deg % 360.0)


def geodetic_to_ecef(lat_deg: np.ndarray, lon_deg: np.ndarray, alt_km: np.ndarray) -> np.ndarray:
    """WGS84 geodetic coordinates to ECEF positions, shape (..., 3)."""
    lat = np.radians(np.asarray(lat_deg, dtype=float))
    lon = np.radians(np.asarray(lon_deg, dtype=float))
    alt = np.asarray(alt_km, dtype=float)
    sin_lat = np.sin(lat)
    cos_lat = np.cos(lat)
    # Prime vertical radius of curvature
    n = WGS84_A_KM / np.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    x = (n + alt) * cos_lat * np.cos(lon)
    y = (n + alt) * cos_lat * np.sin(lon)
    z = (n * (1.0 - WGS84_E2) + alt) * sin_lat
    return np.stack([x, y, z], axis=-1)


def teme_to_ecef(pos_teme: np.ndarray, gmst: float) -> np.ndarray:
    """Rotate TEME positions (..., 3) into ECEF by the GMST angle about Z."""
    cos_g = math.cos(gmst)
    sin_g = math.sin(gmst)
    x, y, z = pos_teme[..., 0], pos_teme[..., 1], pos_teme[..., 2]
    # Inverse of the ecef->eci rotation used in tracking/doppler.py
    x_ecef = cos_g * x + sin_g * y
    y_ecef = -sin_g * x + cos_g * y
    return np.stack([x_ecef, y_ecef, z], axis=-1)


def enu_unit_vectors(lat_deg: np.ndarray, lon_deg: np.ndarray):
    """East/north/up ECEF unit vectors for each station, each shape (..., 3)."""
    lat = np.radians(np.asarray(lat_deg, dtype=float))
    lon = np.radians(np.asarray(lon_deg, dtype=float))
    sin_lat, cos_lat = np.sin(lat), np.cos(lat)
    sin_lon, cos_lon = np.sin(lon), np.cos(lon)
    east = np.stack([-sin_lon, cos_lon, np.zeros_like(lon)], axis=-1)
    north = np.stack([-sin_lat * cos_lon, -sin_lat * sin_lon, cos_lat], axis=-1)
    up = np.stack([cos_lat * cos_lon, cos_lat * sin_lon, sin_lat], axis=-1)
    return east, north, up


def compute_topocentric(
    sat_pos_ecef: np.ndarray,
    station_pos_ecef: np.ndarray,
    station_lat_deg: np.ndarray,
    station_lon_deg: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Az/el/range from every station to every satellite.

    Returns az (degrees, [0, 360)), el (degrees) and range (km), each shaped
    (n_stations, n_satellites).
    """
    delta = sat_pos_ecef[None, :, :] - station_pos_ecef[:, None, :]
    east, north, up = enu_unit_vectors(station_lat_deg, station_lon_deg)
    e = np.einsum("ijk,ik->ij", delta, east)
    n = np.einsum("ijk,ik->ij", delta, north)
    u = np.einsum("ijk,ik->ij", delta, up)
    rng = np.linalg.norm(delta, axis=-1)
    az = np.degrees(np.arctan2(e, n)) % 360.0
    with np.errstate(invalid="ignore", divide="ignore"):
        el = np.degrees(np.arcsin(np.clip(u / rng, -1.0, 1.0)))
    return az, el, rng


def compute_radar_positions(
    targets: List[Dict[str, Any]],
    stations: List[Dict[str, Any]],
    epoch: datetime,
    min_elevation_deg: float = 0.0,
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[int, float]]:
    """Propagate all targets to one epoch and compute visibility per station.

    Args:
        targets: list of {"norad_id": int, "tle1": str, "tle2": str}
        stations: list of {"id": str, "lat": float, "lon": float, "alt_km": float?}
        epoch: UTC propagation instant
        min_elevation_deg: keep only targets at or above this elevation

    Returns:
        positions: {station_id: [{"norad_id", "az", "el", "range_km"}, ...]}
            sorted by elevation descending; only stations with >= 1 target.
        max_ranges: {norad_id: max slant range in km over all stations}
    """
    positions: Dict[str, List[Dict[str, Any]]] = {}
    max_ranges: Dict[int, float] = {}
    if not targets or not stations:
        return positions, max_ranges

    # Build sgp4 records, skipping TLEs that fail to parse
    satrecs = []
    kept_norads = []
    for target in targets:
        try:
            satrecs.append(Satrec.twoline2rv(target["tle1"], target["tle2"]))
            kept_norads.append(int(target["norad_id"]))
        except (ValueError, TypeError, KeyError):
            continue
    if not satrecs:
        return positions, max_ranges

    jd, fr = epoch_to_jday(epoch)
    # SatrecArray exposes sgp4() (not propagate()) and takes array time inputs;
    # a single-epoch call still returns a length-1 time axis, which we squeeze.
    error_codes, pos_teme, _vel = SatrecArray(satrecs).sgp4(np.array([jd]), np.array([fr]))
    error_codes = np.asarray(error_codes)[:, 0]
    pos_teme = np.asarray(pos_teme, dtype=float)[:, 0, :]
    good = (error_codes == 0) & np.all(np.isfinite(pos_teme), axis=1)
    if not np.any(good):
        return positions, max_ranges

    kept_norads = [kept_norads[i] for i in np.flatnonzero(good)]
    sat_ecef = teme_to_ecef(np.asarray(pos_teme, dtype=float)[good], gmst_rad(jd + fr))

    station_lat = np.array([float(s["lat"]) for s in stations])
    station_lon = np.array([float(s["lon"]) for s in stations])
    station_alt = np.array([float(s.get("alt_km", 0.0)) for s in stations])
    station_ecef = geodetic_to_ecef(station_lat, station_lon, station_alt)

    az, el, rng = compute_topocentric(sat_ecef, station_ecef, station_lat, station_lon)

    # Max slant range per target over all stations (visibility independent)
    per_sat_max = np.nanmax(rng, axis=0)
    for idx, norad_id in enumerate(kept_norads):
        max_ranges[norad_id] = round(float(per_sat_max[idx]), 1)

    visible = el >= min_elevation_deg
    for s_idx, station in enumerate(stations):
        sat_indices = np.flatnonzero(visible[s_idx])
        if sat_indices.size == 0:
            continue
        # Sort by elevation descending
        sat_indices = sat_indices[np.argsort(-el[s_idx, sat_indices])]
        entries = [
            {
                "norad_id": kept_norads[t_idx],
                "az": round(float(az[s_idx, t_idx]), 1),
                "el": round(float(el[s_idx, t_idx]), 1),
                "range_km": round(float(rng[s_idx, t_idx]), 1),
            }
            for t_idx in sat_indices
        ]
        positions[station["id"]] = entries

    return positions, max_ranges
