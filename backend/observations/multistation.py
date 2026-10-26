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

"""Multi-station observation generation.

The stock generator predicts passes from the single home location and books
the SDRs carried by the monitored satellite's sessions. This module extends
capture to every configured remote station: for each monitored satellite and
each station SDR (an SDR with site coordinates, e.g. a SpyServer receiver),
passes are predicted from the STATION's own location and one observation is
booked per (satellite, station, pass) when the station's receiver range
covers the satellite's downlink and the station is free in that window.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.common import logger
from crud.monitoredsatellites import (
    fetch_enabled_monitored_satellites,
    mark_observation_as_generated,
)
from crud.satellites import fetch_satellites
from crud.scheduledobservations import add_scheduled_observation
from db.models import SDRType, SDRs, ScheduledObservations, Transmitters
from orbits import CentralBody, OrbitServiceError, get_propagation_input
from tracking.elcalculator import calculate_elevation_crossing_time
from tracking.passes import calculate_next_events

# Tolerance when comparing windows for station bookings (same as conflicts).
PASS_OVERLAP_TOLERANCE_MINUTES = 2


def _station_session_sdr(sdr: SDRs, template_sdr: Dict[str, Any]) -> Dict[str, Any]:
    """Build the session SDR config for a station from the template session."""
    session_sdr = dict(template_sdr or {})
    session_sdr["id"] = str(sdr.id)
    session_sdr["name"] = sdr.name
    return session_sdr


def _sdr_covers_transmitters(
    sdr: SDRs, downlink_low_min_hz: float, downlink_low_max_hz: float
) -> bool:
    """Check the station's advertised receive range covers the downlinks.

    SDR frequency bounds are stored in MHz; transmitter downlinks are Hz.
    Stations without advertised bounds are treated as covering everything.
    """
    min_mhz = sdr.frequency_min
    max_mhz = sdr.frequency_max
    if not min_mhz and not max_mhz:
        return True
    low_mhz = downlink_low_min_hz / 1e6
    high_mhz = downlink_low_max_hz / 1e6
    if min_mhz and high_mhz < float(min_mhz):
        return False
    if max_mhz and low_mhz > float(max_mhz):
        return False
    return True


async def _find_station_overlapping_observation(
    session: AsyncSession,
    norad_id: int,
    event_start: datetime,
    event_end: datetime,
    monitored_satellite_id: str,
    sdr_id: str,
) -> Optional[ScheduledObservations]:
    """Find an existing observation for the same satellite+station pass window.

    Unlike find_overlapping_observation, only observations whose sessions
    actually use the given station SDR count as overlaps, so two stations can
    capture the same pass independently.
    """
    tolerance = timedelta(minutes=PASS_OVERLAP_TOLERANCE_MINUTES)
    search_start = event_start - tolerance
    search_end = event_end + tolerance

    stmt = select(ScheduledObservations).filter(
        ScheduledObservations.norad_id == norad_id,
        ScheduledObservations.monitored_satellite_id == monitored_satellite_id,
        ScheduledObservations.event_start <= search_end,
        ScheduledObservations.event_end >= search_start,
    )
    result = await session.execute(stmt)
    for existing in result.scalars():
        sessions = existing.sessions or []
        for obs_session in sessions:
            if not isinstance(obs_session, dict):
                continue
            obs_sdr = obs_session.get("sdr") or {}
            if str(obs_sdr.get("id", "")) == str(sdr_id):
                return existing
    return None


async def _find_station_time_conflict(
    session: AsyncSession,
    event_start: datetime,
    event_end: datetime,
    sdr_id: str,
    exclude_observation_id: Optional[str] = None,
) -> bool:
    """Check whether the station SDR is already booked in this window.

    Any enabled, non-finished observation whose sessions reference this SDR
    and whose execution window overlaps blocks a new booking for the station.
    """
    tolerance = timedelta(minutes=PASS_OVERLAP_TOLERANCE_MINUTES)
    search_start = event_start - tolerance
    search_end = event_end + tolerance

    stmt = select(ScheduledObservations).filter(
        ScheduledObservations.event_start <= search_end,
        ScheduledObservations.event_end >= search_start,
    )
    result = await session.execute(stmt)
    for existing in result.scalars():
        if exclude_observation_id and str(existing.id) == str(exclude_observation_id):
            continue
        status = getattr(existing.status, "value", existing.status) or ""
        if str(status).upper() in ("COMPLETED", "FAILED", "CANCELLED", "MISSED"):
            continue
        if existing.enabled is False:
            continue
        for obs_session in existing.sessions or []:
            if not isinstance(obs_session, dict):
                continue
            obs_sdr = obs_session.get("sdr") or {}
            if str(obs_sdr.get("id", "")) == str(sdr_id):
                return True
    return False


async def _load_transmitter_bounds(
    session: AsyncSession, norad_id: int
) -> Optional[tuple]:
    """Return (min, max) alive downlink frequencies in Hz for a satellite."""
    result = await session.execute(
        select(Transmitters.downlink_low).filter(
            Transmitters.norad_cat_id == norad_id,
            Transmitters.alive.is_(True),
            Transmitters.downlink_low.isnot(None),
        )
    )
    frequencies = [float(row[0]) for row in result.all() if row[0]]
    if not frequencies:
        return None
    return min(frequencies), max(frequencies)


def _station_location(sdr: SDRs) -> Optional[Dict[str, float]]:
    if sdr.latitude is None or sdr.longitude is None:
        return None
    return {"lat": float(sdr.latitude), "lon": float(sdr.longitude)}


async def generate_multi_station_observations(
    session: AsyncSession, dry_run: bool = False
) -> Dict[str, Any]:
    """Generate observations for every (monitored satellite, station) pair.

    Returns summary statistics for the handler/scheduler to report.
    """
    stats = {
        "generated": 0,
        "updated": 0,
        "skipped": 0,
        "stations_processed": 0,
        "satellites_processed": 0,
        "conflict_skipped": 0,
        "errors": 0,
    }

    try:
        # 1. Station SDRs: remote receivers with known site coordinates.
        sdr_result = await session.execute(
            select(SDRs).filter(
                SDRs.type == SDRType.SPYSERVER,
                SDRs.latitude.isnot(None),
                SDRs.longitude.isnot(None),
            )
        )
        station_sdrs = [s for s in sdr_result.scalars().all() if _station_location(s)]
        stats["stations_processed"] = len(station_sdrs)
        if not station_sdrs:
            logger.info("Multi-station generation: no station SDRs with site coordinates")
            return stats

        # 2. Enabled monitored satellites (already transformed to handler format).
        monitored_result = await fetch_enabled_monitored_satellites(session)
        monitored_sats = monitored_result.get("data") or []
        stats["satellites_processed"] = len(monitored_sats)
        if not monitored_sats:
            logger.info("Multi-station generation: no enabled monitored satellites")
            return stats

        for mon_sat in monitored_sats:
            satellite = mon_sat.get("satellite") or {}
            norad_id = satellite.get("norad_id")
            template_sessions = mon_sat.get("sessions") or []
            if not norad_id or not template_sessions:
                stats["skipped"] += 1
                continue
            template_session = template_sessions[0]
            template_sdr = template_session.get("sdr") or {}
            template_tasks = template_session.get("tasks") or []
            min_elevation = mon_sat.get("min_elevation", 20)
            lookahead_hours = mon_sat.get("lookahead_hours", 24)
            task_start_elevation = mon_sat.get("task_start_elevation", 10)

            sat_result = await fetch_satellites(session, norad_id=norad_id)
            if not sat_result.get("success") or not sat_result.get("data"):
                logger.warning(
                    f"Multi-station: no satellite data for NORAD {norad_id}"
                )
                stats["skipped"] += 1
                continue
            satellite_data = sat_result["data"][0]

            try:
                propagation_input = get_propagation_input(
                    satellite_data, central_body=CentralBody.EARTH
                )
            except OrbitServiceError as e:
                logger.warning(
                    f"Multi-station: no orbit for {satellite.get('name')}: {e}"
                )
                stats["skipped"] += 1
                continue

            tx_bounds = await _load_transmitter_bounds(session, norad_id)

            for station in station_sdrs:
                station_id = str(station.id)
                station_loc = _station_location(station)

                # Receiver range must cover the satellite's downlinks.
                if tx_bounds and not _sdr_covers_transmitters(station, tx_bounds[0], tx_bounds[1]):
                    continue

                passes_result = calculate_next_events(
                    satellite_data=satellite_data,
                    home_location=station_loc,
                    hours=lookahead_hours,
                    above_el=0,
                )
                if not passes_result.get("success"):
                    logger.warning(
                        f"Multi-station: pass calculation failed for {station.name}: "
                        f"{passes_result.get('error')}"
                    )
                    continue

                try:
                    for pass_data in passes_result.get("data") or []:
                        if float(pass_data.get("peak_altitude", -90)) < min_elevation:
                            continue

                        event_start = datetime.fromisoformat(
                            pass_data["event_start"].replace("Z", "+00:00")
                        )
                        event_end = datetime.fromisoformat(
                            pass_data["event_end"].replace("Z", "+00:00")
                        )
                        if event_end <= datetime.now(timezone.utc):
                            continue

                        if dry_run:
                            stats["generated"] += 1
                            continue

                        existing = await _find_station_overlapping_observation(
                            session,
                            norad_id,
                            event_start,
                            event_end,
                            mon_sat["id"],
                            station_id,
                        )
                        if existing is not None:
                            stats["skipped"] += 1
                            continue

                        if await _find_station_time_conflict(
                            session, event_start, event_end, station_id
                        ):
                            stats["conflict_skipped"] += 1
                            continue

                        created = await _create_station_observation(
                            session,
                            mon_sat,
                            satellite,
                            propagation_input,
                            pass_data,
                            event_start,
                            event_end,
                            task_start_elevation,
                            station,
                            station_loc,
                            template_sdr,
                            template_tasks,
                        )
                        if created:
                            stats["generated"] += 1
                        else:
                            stats["errors"] += 1
                except Exception as pass_error:
                    logger.warning(
                        f"Multi-station: pass processing failed for {station.name}: "
                        f"{pass_error}"
                    )
                    stats["errors"] += 1

        logger.info(
            f"Multi-station generation complete: {stats['generated']} generated, "
            f"{stats['conflict_skipped']} conflict-skipped, {stats['skipped']} skipped, "
            f"{stats['errors']} errors across {stats['stations_processed']} station(s)"
        )
        return stats

    except Exception as e:
        await session.rollback()
        logger.error(f"Error in generate_multi_station_observations: {e}")
        logger.exception(e)
        stats["errors"] += 1
        return stats


async def _create_station_observation(
    session: AsyncSession,
    mon_sat: Dict[str, Any],
    satellite: Dict[str, Any],
    propagation_input: Any,
    pass_data: Dict[str, Any],
    event_start: datetime,
    event_end: datetime,
    task_start_elevation: float,
    station: SDRs,
    station_loc: Dict[str, float],
    template_sdr: Dict[str, Any],
    template_tasks: list,
) -> bool:
    """Create one observation for one (satellite, station, pass)."""
    # Task window from the station's own elevation-crossing times.
    task_start = None
    task_end = None
    if task_start_elevation and task_start_elevation > 0:
        try:
            get_propagation_input(pass_data, central_body=CentralBody.EARTH)
            satellite_tle = dict(pass_data)
            task_start, task_end = calculate_elevation_crossing_time(
                satellite_tle=satellite_tle,
                home_location=station_loc,
                aos_time=event_start,
                los_time=event_end,
                target_elevation=task_start_elevation,
            )
        except OrbitServiceError as e:
            logger.warning(
                f"Multi-station: crossing calculation failed for {satellite.get('name')}: {e}"
            )
    if not task_start:
        task_start = event_start
    if not task_end:
        task_end = event_end

    obs_name = (
        f"{satellite['name']} @ {station.name} - "
        f"{event_start.strftime('%Y-%m-%d %H:%M UTC')}"
    )

    station_session = {
        "sdr": _station_session_sdr(station, template_sdr),
        "tasks": template_tasks,
    }

    observation_data = {
        "id": f"obs-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "name": obs_name,
        "enabled": True,
        "status": "SCHEDULED",
        "satellite": satellite,
        "pass": {
            "event_start": pass_data["event_start"],
            "event_end": pass_data["event_end"],
            "peak_altitude": pass_data["peak_altitude"],
            "start_azimuth": pass_data.get("start_azimuth"),
            "end_azimuth": pass_data.get("end_azimuth"),
            "peak_azimuth": pass_data.get("peak_azimuth"),
            "distance_at_peak": pass_data.get("distance_at_peak"),
        },
        "rotator": mon_sat.get("rotator", {}),
        "rig": mon_sat.get("rig", {}),
        "transmitter": {},
        "sessions": [station_session],
        "task_start_elevation": mon_sat.get("task_start_elevation", 10),
        "task_start": task_start.isoformat() if task_start else None,
        "task_end": task_end.isoformat() if task_end else None,
    }

    result = await add_scheduled_observation(session, observation_data)
    if result.get("success"):
        observation_id = result["data"]["id"]
        await mark_observation_as_generated(session, observation_id, mon_sat["id"])
        logger.info(f"Created multi-station observation: {obs_name}")
        return True

    logger.error(
        f"Failed to create multi-station observation {obs_name}: {result.get('error')}"
    )
    return False
