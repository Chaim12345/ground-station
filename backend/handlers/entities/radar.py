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

"""Live radar view: current az/el of all satellites from all remote
SpyServer stations plus the app's home location, served over the
api.call pipeline as "get-radar-view"."""

import time
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

from sqlalchemy import case, func, select

import crud.locations as crud_locations
import crud.monitoredsatellites as crud_monitored
from db import AsyncSessionLocal
from db.models import Satellites, Transmitters
from hardware.soapysdrbrowser import get_discovered_servers
from tracking.radar import compute_radar_positions

# Multiple UIs poll this command; cache the computed snapshot briefly
CACHE_TTL_SECONDS = 20.0
_cache: Dict[str, Any] = {"key": None, "ts": 0.0, "payload": None}


async def collect_radar_payload(session, min_elevation_deg: float = 0.0) -> Dict[str, Any]:
    """Query DB + discovered stations and compute the full radar snapshot."""
    started = time.perf_counter()
    now = datetime.now(timezone.utc)

    # Satellites with usable TLEs that have not decayed
    stmt = (
        select(Satellites.norad_id, Satellites.name, Satellites.tle1, Satellites.tle2)
        .where(
            Satellites.tle1.isnot(None),
            Satellites.tle1 != "",
            Satellites.tle2.isnot(None),
            Satellites.tle2 != "",
            # decayed is a datetime column: NULL means still in orbit
            Satellites.decayed.is_(None),
        )
        .order_by(Satellites.norad_id)
    )
    sat_rows = (await session.execute(stmt)).all()

    # Monitored flag: only enabled monitored satellites count
    monitored_result = await crud_monitored.fetch_monitored_satellites(session)
    monitored_norads = set()
    if monitored_result["success"]:
        for row in monitored_result.get("data") or []:
            norad_id = (row.get("satellite") or {}).get("norad_id")
            if norad_id is not None and row.get("enabled", True):
                monitored_norads.add(int(norad_id))

    # One aggregate query for transmitter info per satellite
    tx_stmt = (
        select(
            Transmitters.norad_cat_id,
            func.min(Transmitters.downlink_low).label("downlink_min"),
            func.max(Transmitters.downlink_low).label("downlink_max"),
            func.sum(case((Transmitters.alive == 1, 1), else_=0)).label("alive_count"),
        )
        .where(Transmitters.norad_cat_id.isnot(None))
        .group_by(Transmitters.norad_cat_id)
    )
    tx_rows = (await session.execute(tx_stmt)).all()
    tx_by_norad = {int(row.norad_cat_id): row for row in tx_rows}

    # Remote SpyServer stations discovered on the network
    stations = []
    for entry in get_discovered_servers().values():
        if entry.get("latitude") is None or entry.get("longitude") is None:
            continue
        station_id = f"{entry.get('ip')}:{entry.get('port')}"
        stations.append(
            {
                "id": station_id,
                "name": entry.get("name") or station_id,
                "host": entry.get("ip"),
                "port": entry.get("port"),
                "lat": float(entry["latitude"]),
                "lon": float(entry["longitude"]),
                "device_type": entry.get("device_type"),
                "max_clients": entry.get("max_clients"),
                "current_clients": entry.get("current_clients"),
                "source": "spyserver",
            }
        )

    # Home location (first row, same convention as the tracking system)
    locations_result = await crud_locations.fetch_all_locations(session)
    if locations_result["success"]:
        locations = locations_result.get("data") or []
        if locations:
            first = locations[0]
            stations.append(
                {
                    "id": "home",
                    "name": first.get("name") or "Home",
                    "lat": float(first["lat"]),
                    "lon": float(first["lon"]),
                    "alt_km": float(first.get("alt") or 0) / 1000.0,
                    "source": "home",
                }
            )

    target_inputs = [
        {
            "norad_id": int(row.norad_id),
            "name": row.name,
            "tle1": row.tle1,
            "tle2": row.tle2,
        }
        for row in sat_rows
    ]

    positions, max_ranges = compute_radar_positions(
        target_inputs, stations, now, min_elevation_deg=min_elevation_deg
    )

    targets: Dict[str, Dict[str, Any]] = {}
    for target in target_inputs:
        norad_id = target["norad_id"]
        tx = tx_by_norad.get(norad_id)
        downlink_min = float(tx.downlink_min) / 1_000_000.0 if tx and tx.downlink_min else None
        downlink_max = float(tx.downlink_max) / 1_000_000.0 if tx and tx.downlink_max else None
        targets[str(norad_id)] = {
            "norad_id": norad_id,
            "name": target["name"],
            "monitored": norad_id in monitored_norads,
            "transmitter_count": int(tx.alive_count or 0) if tx else 0,
            "downlink_min_mhz": round(downlink_min, 3) if downlink_min else None,
            "downlink_max_mhz": round(downlink_max, 3) if downlink_max else None,
            "max_range_km": max_ranges.get(norad_id, 0.0),
        }

    stations_out = []
    for station in stations:
        stations_out.append({**station, "target_count": len(positions.get(station["id"], []))})

    return {
        "generated_at": now.isoformat(),
        "compute_ms": round((time.perf_counter() - started) * 1000.0, 2),
        "stations": stations_out,
        "targets": targets,
        "positions": positions,
    }


async def get_radar_view(
    sio: Any, data: Optional[Dict], logger: Any, sid: str
) -> Dict[str, Union[bool, Dict, str]]:
    """
    Get the current radar view (az/el of all satellites from all stations).

    Args:
        sio: Socket.IO server instance
        data: Optional dict with 'min_elevation' (degrees, default 0)
        logger: Logger instance
        sid: Socket.IO session ID

    Returns:
        Dictionary with success status and the radar snapshot
    """
    try:
        min_elevation = 0.0
        if data and data.get("min_elevation") is not None:
            min_elevation = float(data["min_elevation"])

        # Cache key includes the threshold so custom requests bypass stale hits
        cache_key = round(min_elevation, 2)
        now_mono = time.monotonic()
        if (
            _cache["payload"] is not None
            and _cache["key"] == cache_key
            and (now_mono - _cache["ts"]) < CACHE_TTL_SECONDS
        ):
            return {"success": True, "data": _cache["payload"]}

        async with AsyncSessionLocal() as dbsession:
            payload = await collect_radar_payload(dbsession, min_elevation_deg=min_elevation)

        _cache.update({"key": cache_key, "ts": time.monotonic(), "payload": payload})
        return {"success": True, "data": payload}

    except Exception as e:
        logger.error(f"Error getting radar view: {e}")
        logger.error(traceback.format_exc())
        return {"success": False, "error": str(e)}


def register_handlers(registry):
    """Register radar handlers with the command registry."""
    registry.register_batch(
        {
            "get-radar-view": (get_radar_view, "api_call"),
        }
    )
