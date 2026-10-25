# Copyright (c) 2025 Efstratios Goudelis
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Tracked celestial-row construction and grouped projection orchestration."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from celestial.bodies import _build_body_snapshot_by_id
from celestial.horizons import get_horizons_status
from celestial.observer import (
    _attach_observer_view_local,
    _build_observer_sun,
    _load_earth_observer_vectors,
    _load_observer_location,
)
from celestial.passes import _build_celestial_passes
from celestial.relative import build_earth_relative_metrics
from celestial.settings import (
    CACHE_TTL_SECONDS,
    CELESTIAL_PASS_HORIZON_DEG,
    COMPUTED_EPOCH_BUCKET_SECONDS,
    DEFAULT_CENTER,
    DEFAULT_FRAME,
    VECTOR_DB_TTL_SECONDS,
    _bucket_epoch,
    _parse_epoch,
    _parse_projection_options,
)
from celestial.snapshots import _get_vectors_snapshot
from celestial.targets import (
    BODY_HORIZONS_COMMANDS,
    _ensure_scene_targets_registered,
    _normalize_targets,
    _target_key_from_parts,
)
from celestial.trajectory import _extract_orbit_samples, _refresh_payload_dynamics_at_epoch


@dataclass
class CacheEntry:
    payload: Dict[str, Any]
    fetched_at_monotonic: float


_computed_cache: Dict[str, CacheEntry] = {}
_computed_cache_lock = threading.Lock()


async def _fetch_celestial_with_cache(
    targets: List[Dict[str, Any]],
    epoch: datetime,
    past_hours: int,
    future_hours: int,
    step_minutes: int,
    observer_location: Optional[Dict[str, Any]],
    earth_position_xyz_au: Optional[List[float]],
    body_snapshot_by_id: Dict[str, Dict[str, Any]],
    force_refresh: bool,
    allow_network_fetch: bool,
    logger,
    per_row_callback: Optional[Any] = None,
    use_computed_cache: bool = True,
    retry_horizons: bool = False,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    now_monotonic = time.monotonic()
    total_targets = len(targets)

    for index, target in enumerate(targets):
        target_type = str(target.get("target_type") or "mission").strip().lower()
        target_key = str(target.get("target_key") or "").strip()
        name = target["name"]
        color = target.get("color")

        if target_type == "body":
            body_id = str(target.get("body_id") or "").strip().lower()
            body_row = body_snapshot_by_id.get(body_id)
            if body_row:
                body_payload = dict(body_row)
                body_payload["target_type"] = "body"
                body_payload["target_key"] = target_key or _target_key_from_parts(
                    "body", body_id=body_id
                )
                body_payload["body_id"] = body_id
                body_payload["command"] = str(
                    target.get("horizons_command") or target.get("command") or body_id
                )
                body_payload["name"] = name or body_payload.get("name") or body_id
                body_payload["color"] = color
                body_payload["source"] = body_payload.get("source") or "horizons"
                body_payload["stale"] = bool(body_payload.get("stale"))
                body_payload["cache"] = body_payload.get("cache") or "scene-base-hit"
                body_payload["orbit_sampling"] = {
                    "past_hours": past_hours,
                    "future_hours": future_hours,
                    "step_minutes": step_minutes,
                }
                _refresh_payload_dynamics_at_epoch(
                    payload=body_payload,
                    epoch=epoch,
                    past_hours=past_hours,
                    future_hours=future_hours,
                )
                _attach_observer_view_local(
                    row=body_payload,
                    epoch=epoch,
                    observer_location=observer_location,
                    earth_position_xyz_au=earth_position_xyz_au,
                    logger=logger,
                )
                rows.append(body_payload)
                if per_row_callback:
                    await per_row_callback(dict(body_payload), index + 1, total_targets)
                continue

            horizons_command = str(
                target.get("horizons_command")
                or target.get("command")
                or BODY_HORIZONS_COMMANDS.get(body_id)
                or ""
            ).strip()
            if not horizons_command:
                body_error = {
                    "target_type": "body",
                    "target_key": target_key or _target_key_from_parts("body", body_id=body_id),
                    "body_id": body_id,
                    "command": body_id,
                    "name": name,
                    "color": color,
                    "source": "horizons",
                    "stale": True,
                    "cache": "missing-command",
                    "error": f"No Horizons command mapping configured for body '{body_id}'",
                    "sky_position": None,
                    "visibility": {
                        "above_horizon": None,
                        "visible": None,
                        "horizon_threshold_deg": 0.0,
                    },
                }
                rows.append(body_error)
                if per_row_callback:
                    await per_row_callback(dict(body_error), index + 1, total_targets)
                continue

            snapshot = await _get_vectors_snapshot(
                target_key=target_key or _target_key_from_parts("body", body_id=body_id),
                command=horizons_command,
                epoch=epoch,
                past_hours=past_hours,
                future_hours=future_hours,
                step_minutes=step_minutes,
                observer_location=observer_location,
                force_refresh=force_refresh,
                logger=logger,
                allow_network_fetch=allow_network_fetch,
                retry_horizons=retry_horizons,
            )
            payload = snapshot.get("payload")
            if isinstance(payload, dict):
                row_payload = dict(payload)
                row_payload["target_type"] = "body"
                row_payload["target_key"] = target_key or _target_key_from_parts(
                    "body", body_id=body_id
                )
                row_payload["body_id"] = body_id
                row_payload["command"] = horizons_command
                row_payload["name"] = name
                row_payload["color"] = color
                row_payload["body_class"] = target.get("body_class")
                row_payload["parent_body_id"] = target.get("parent_body_id")
                row_payload["stale"] = bool(snapshot.get("stale"))
                row_payload["calculation_usable"] = snapshot.get("calculation_usable", True)
                row_payload["current_position_usable"] = snapshot.get(
                    "current_position_usable", True
                )
                row_payload["cache"] = snapshot.get("cache")
                row_payload["orbit_sampling"] = {
                    "past_hours": past_hours,
                    "future_hours": future_hours,
                    "step_minutes": step_minutes,
                }
                if snapshot.get("error"):
                    row_payload["error"] = snapshot.get("error")
                if snapshot.get("error_code"):
                    row_payload["error_code"] = snapshot.get("error_code")
                _refresh_payload_dynamics_at_epoch(
                    payload=row_payload,
                    epoch=epoch,
                    past_hours=past_hours,
                    future_hours=future_hours,
                )
                _attach_observer_view_local(
                    row=row_payload,
                    epoch=epoch,
                    observer_location=observer_location,
                    earth_position_xyz_au=earth_position_xyz_au,
                    logger=logger,
                )
                rows.append(row_payload)
                if per_row_callback:
                    await per_row_callback(dict(row_payload), index + 1, total_targets)
                continue

            body_error = {
                "target_type": "body",
                "target_key": target_key or _target_key_from_parts("body", body_id=body_id),
                "body_id": body_id,
                "command": horizons_command,
                "name": name,
                "color": color,
                "source": "horizons",
                "stale": True,
                "calculation_usable": False,
                "cache": snapshot.get("cache"),
                "error": snapshot.get("error") or "No data returned",
                "error_code": snapshot.get("error_code"),
                "sky_position": None,
                "visibility": {
                    "above_horizon": None,
                    "visible": None,
                    "horizon_threshold_deg": 0.0,
                },
            }
            rows.append(body_error)
            if per_row_callback:
                await per_row_callback(dict(body_error), index + 1, total_targets)
            continue

        command = str(target.get("horizons_command") or target.get("command") or "").strip()
        if not command:
            continue
        observer_cache_key = "no-observer"
        if observer_location:
            observer_cache_key = (
                f"{observer_location.get('id')}"
                f"|{observer_location.get('lat')}"
                f"|{observer_location.get('lon')}"
                f"|{observer_location.get('alt_m')}"
            )
        epoch_cache_key = _bucket_epoch(epoch, COMPUTED_EPOCH_BUCKET_SECONDS).isoformat()
        cache_key = (
            f"{target_key or _target_key_from_parts('mission', command=command)}|{epoch_cache_key}"
            f"|p{past_hours}|f{future_hours}|s{step_minutes}"
            f"|obs:{observer_cache_key}"
            # A cache-only lookup can contain an expired projection. Do not let
            # it suppress a later request that is allowed to refresh Horizons.
            f"|network:{int(bool(allow_network_fetch))}"
        )

        use_cached = False
        cached_entry: Optional[CacheEntry] = None

        with _computed_cache_lock:
            cached_entry = _computed_cache.get(cache_key)
            if (
                use_computed_cache
                and cached_entry
                and not force_refresh
                and now_monotonic - cached_entry.fetched_at_monotonic <= CACHE_TTL_SECONDS
            ):
                use_cached = True

        if use_cached and cached_entry:
            cached_payload = dict(cached_entry.payload)
            cached_payload["target_type"] = "mission"
            cached_payload["target_key"] = target_key or _target_key_from_parts(
                "mission", command=command
            )
            cached_payload["name"] = name
            cached_payload["color"] = color
            cached_payload["stale"] = bool(cached_payload.get("stale"))
            cached_payload["cache"] = "computed-hit"
            cached_payload["orbit_sampling"] = {
                "past_hours": past_hours,
                "future_hours": future_hours,
                "step_minutes": step_minutes,
            }
            rows.append(cached_payload)
            if per_row_callback:
                await per_row_callback(dict(cached_payload), index + 1, total_targets)
            continue

        snapshot = await _get_vectors_snapshot(
            target_key=target_key or _target_key_from_parts("mission", command=command),
            command=command,
            epoch=epoch,
            past_hours=past_hours,
            future_hours=future_hours,
            step_minutes=step_minutes,
            observer_location=observer_location,
            force_refresh=force_refresh,
            logger=logger,
            allow_network_fetch=allow_network_fetch,
            retry_horizons=retry_horizons,
        )

        payload = snapshot.get("payload")
        if isinstance(payload, dict):
            row_payload = dict(payload)
            row_payload["target_type"] = "mission"
            row_payload["target_key"] = target_key or _target_key_from_parts(
                "mission", command=command
            )
            row_payload["name"] = name
            row_payload["color"] = color
            row_payload["stale"] = bool(snapshot.get("stale"))
            row_payload["calculation_usable"] = snapshot.get("calculation_usable", True)
            row_payload["current_position_usable"] = snapshot.get("current_position_usable", True)
            row_payload["cache"] = snapshot.get("cache")
            row_payload["orbit_sampling"] = {
                "past_hours": past_hours,
                "future_hours": future_hours,
                "step_minutes": step_minutes,
            }
            if snapshot.get("error"):
                row_payload["error"] = snapshot.get("error")
            if snapshot.get("error_code"):
                row_payload["error_code"] = snapshot.get("error_code")
            # Keep "current" vectors fresh between periodic Horizons syncs.
            _refresh_payload_dynamics_at_epoch(
                payload=row_payload,
                epoch=epoch,
                past_hours=past_hours,
                future_hours=future_hours,
            )
            _attach_observer_view_local(
                row=row_payload,
                epoch=epoch,
                observer_location=observer_location,
                earth_position_xyz_au=earth_position_xyz_au,
                logger=logger,
            )
            if use_computed_cache:
                with _computed_cache_lock:
                    _computed_cache[cache_key] = CacheEntry(
                        payload=dict(row_payload),
                        fetched_at_monotonic=time.monotonic(),
                    )
            rows.append(row_payload)
            if per_row_callback:
                await per_row_callback(dict(row_payload), index + 1, total_targets)
            continue

        error_row = {
            "target_type": "mission",
            "target_key": target_key or _target_key_from_parts("mission", command=command),
            "name": name,
            "command": command,
            "color": color,
            "source": "horizons",
            "stale": True,
            "calculation_usable": False,
            "cache": snapshot.get("cache"),
            "error": snapshot.get("error") or "No data returned",
            "error_code": snapshot.get("error_code"),
            "sky_position": None,
            "visibility": {
                "above_horizon": None,
                "visible": None,
                "horizon_threshold_deg": 0.0,
            },
        }
        rows.append(error_row)
        if per_row_callback:
            await per_row_callback(dict(error_row), index + 1, total_targets)

    return rows


async def _build_celestial_tracks_single_projection(
    data: Optional[Dict[str, Any]],
    logger,
    force_refresh: bool = False,
    allow_network_fetch: bool = True,
    per_row_callback: Optional[Any] = None,
    register_targets: bool = True,
    use_computed_cache: bool = True,
    force_refresh_earth: Optional[bool] = None,
    include_passes: bool = True,
) -> Dict[str, Any]:
    """Build only Horizons-backed tracked celestial objects."""
    epoch = _parse_epoch(data)
    targets = _normalize_targets(data)
    past_hours, future_hours, step_minutes = _parse_projection_options(data)
    retry_horizons = bool(data.get("retry_horizons")) if isinstance(data, dict) else False
    observer_location = await _load_observer_location()
    if register_targets:
        await _ensure_scene_targets_registered(targets, logger)
    earth_position_xyz_au, earth_orbit_samples = await _load_earth_observer_vectors(
        epoch=epoch,
        past_hours=past_hours,
        future_hours=future_hours,
        step_minutes=step_minutes,
        observer_location=observer_location,
        force_refresh=force_refresh if force_refresh_earth is None else force_refresh_earth,
        allow_network_fetch=allow_network_fetch,
        logger=logger,
        retry_horizons=retry_horizons,
    )
    # Tracks-only payloads do not build the full solar-system scene, but body
    # targets still need the synthetic Sun origin from the scene snapshot map.
    body_snapshot_by_id = _build_body_snapshot_by_id([])
    celestial = await _fetch_celestial_with_cache(
        targets,
        epoch,
        past_hours,
        future_hours,
        step_minutes,
        observer_location,
        earth_position_xyz_au,
        body_snapshot_by_id,
        force_refresh,
        allow_network_fetch,
        logger,
        per_row_callback,
        use_computed_cache,
        retry_horizons,
    )
    for row in celestial:
        target_samples = _extract_orbit_samples(
            row,
            epoch_fallback=epoch,
            past_hours=past_hours,
            future_hours=future_hours,
        )
        earth_relative = build_earth_relative_metrics(
            target_position_xyz_au=row.get("position_xyz_au"),
            target_velocity_xyz_au_per_day=row.get("velocity_xyz_au_per_day"),
            earth_position_xyz_au=earth_position_xyz_au,
            epoch=epoch,
            target_samples=target_samples,
            earth_samples=earth_orbit_samples,
        )
        if earth_relative is not None:
            row["earth_relative"] = earth_relative
    # Live pointing ticks keep positions fresh without recalculating forecast
    # windows. Full scene requests and periodic snapshots still include passes.
    celestial_passes = (
        _build_celestial_passes(
            rows=celestial,
            epoch=epoch,
            past_hours=past_hours,
            future_hours=future_hours,
            step_minutes=step_minutes,
            observer_location=observer_location,
            earth_position_xyz_au=earth_position_xyz_au,
            earth_orbit_samples=earth_orbit_samples,
            logger=logger,
        )
        if include_passes
        else []
    )
    observer_bodies = [
        _build_observer_sun(
            epoch=epoch,
            observer_location=observer_location,
            earth_position_xyz_au=earth_position_xyz_au,
            logger=logger,
        )
    ]

    return {
        "success": True,
        "data": {
            "timestamp_utc": epoch.isoformat(),
            "frame": DEFAULT_FRAME,
            "center": DEFAULT_CENTER,
            "units": {
                "position": "au",
                "velocity": "au/day",
            },
            "celestial": celestial,
            "observer_bodies": observer_bodies,
            "celestial_passes": celestial_passes,
            "meta": {
                "celestial_source": "horizons",
                "horizons": {
                    **get_horizons_status(),
                    "stale_count": sum(1 for row in celestial if row.get("stale")),
                    "missing_count": sum(
                        1 for row in celestial if not isinstance(row.get("position_xyz_au"), list)
                    ),
                    "offline_count": 0,
                },
                "cache_ttl_seconds": CACHE_TTL_SECONDS,
                "vector_db_ttl_seconds": VECTOR_DB_TTL_SECONDS,
                "projection": {
                    "past_hours": past_hours,
                    "future_hours": future_hours,
                    "step_minutes": step_minutes,
                },
                "passes": {
                    "horizon_threshold_deg": CELESTIAL_PASS_HORIZON_DEG,
                    "count": len(celestial_passes),
                },
                "observer_location": observer_location,
                "visibility_definition": "visible == elevation_deg > 0",
            },
        },
    }


async def build_celestial_tracks(
    data: Optional[Dict[str, Any]],
    logger,
    force_refresh: bool = False,
    allow_network_fetch: bool = True,
    per_row_callback: Optional[Any] = None,
    register_targets: bool = True,
    use_computed_cache: bool = True,
    include_passes: bool = True,
) -> Dict[str, Any]:
    """Build tracked objects, honoring optional projection settings on each target."""
    targets = _normalize_targets(data)
    has_target_projections = any(
        any(target.get(name) is not None for name in ("past_hours", "future_hours", "step_minutes"))
        for target in targets
    )
    if not has_target_projections:
        return await _build_celestial_tracks_single_projection(
            data=data,
            logger=logger,
            force_refresh=force_refresh,
            allow_network_fetch=allow_network_fetch,
            per_row_callback=per_row_callback,
            register_targets=register_targets,
            use_computed_cache=use_computed_cache,
            include_passes=include_passes,
        )

    fallback_past, fallback_future, fallback_step = _parse_projection_options(data)
    groups: Dict[Tuple[int, int, int], List[Dict[str, Any]]] = {}
    projection_by_target: Dict[str, Dict[str, int]] = {}
    for target in targets:
        projection_payload = {
            "past_hours": target.get("past_hours", fallback_past),
            "future_hours": target.get("future_hours", fallback_future),
            "step_minutes": target.get("step_minutes", fallback_step),
        }
        projection = _parse_projection_options(projection_payload)
        groups.setdefault(projection, []).append(target)
        target_key = str(target.get("target_key") or "").strip()
        if target_key:
            projection_by_target[target_key] = {
                "past_hours": projection[0],
                "future_hours": projection[1],
                "step_minutes": projection[2],
            }

    if register_targets:
        await _ensure_scene_targets_registered(targets, logger)

    # Fetch the broadest, densest projection first. Its Earth snapshot can then
    # satisfy narrower groups through the compatible-snapshot cache path.
    ordered_groups = sorted(
        groups.items(),
        key=lambda item: (item[0][0] + item[0][1], -item[0][2]),
        reverse=True,
    )
    combined_rows: List[Dict[str, Any]] = []
    combined_passes: List[Dict[str, Any]] = []
    combined_payload: Optional[Dict[str, Any]] = None
    processed_targets = 0
    total_targets = len(targets)

    for group_index, (
        (past_hours, future_hours, step_minutes),
        group_targets,
    ) in enumerate(ordered_groups):
        group_data = dict(data) if isinstance(data, dict) else {}
        group_data.update(
            {
                "past_hours": past_hours,
                "future_hours": future_hours,
                "step_minutes": step_minutes,
                "celestial": group_targets,
            }
        )

        group_callback = None
        if per_row_callback:

            async def emit_group_row(
                row: Dict[str, Any],
                index: int,
                _group_total: int,
                *,
                offset: int = processed_targets,
            ) -> None:
                await per_row_callback(row, offset + index, total_targets)

            group_callback = emit_group_row

        result = await _build_celestial_tracks_single_projection(
            data=group_data,
            logger=logger,
            force_refresh=force_refresh,
            allow_network_fetch=allow_network_fetch,
            per_row_callback=group_callback,
            register_targets=False,
            use_computed_cache=use_computed_cache,
            include_passes=include_passes,
            # One fresh Earth trajectory is enough. Later groups reuse it when
            # compatible while their own target vectors still force-refresh.
            force_refresh_earth=force_refresh if group_index == 0 else False,
        )
        if not result.get("success"):
            return result

        result_data_obj = result.get("data")
        result_data = result_data_obj if isinstance(result_data_obj, dict) else {}
        if combined_payload is None:
            combined_payload = dict(result_data)
        rows_obj = result_data.get("celestial")
        passes_obj = result_data.get("celestial_passes")
        combined_rows.extend(rows_obj if isinstance(rows_obj, list) else [])
        combined_passes.extend(passes_obj if isinstance(passes_obj, list) else [])
        processed_targets += len(group_targets)

    if combined_payload is None:
        return await _build_celestial_tracks_single_projection(
            data=data,
            logger=logger,
            force_refresh=force_refresh,
            allow_network_fetch=allow_network_fetch,
            per_row_callback=per_row_callback,
            register_targets=False,
            use_computed_cache=use_computed_cache,
            include_passes=include_passes,
        )

    target_order = {
        str(target.get("target_key") or "").strip(): index for index, target in enumerate(targets)
    }
    combined_rows.sort(
        key=lambda row: target_order.get(str(row.get("target_key") or "").strip(), len(targets))
    )
    combined_passes.sort(key=lambda item: str(item.get("event_start") or ""))
    combined_payload["celestial"] = combined_rows
    combined_payload["celestial_passes"] = combined_passes

    meta_obj = combined_payload.get("meta")
    meta = dict(meta_obj) if isinstance(meta_obj, dict) else {}
    meta["projection"] = {
        "past_hours": max(projection[0] for projection in groups),
        "future_hours": max(projection[1] for projection in groups),
        "step_minutes": min(projection[2] for projection in groups),
    }
    meta["projection_by_target"] = projection_by_target
    horizons_obj = meta.get("horizons")
    horizons = dict(horizons_obj) if isinstance(horizons_obj, dict) else {}
    horizons["stale_count"] = sum(1 for row in combined_rows if row.get("stale"))
    horizons["missing_count"] = sum(
        1 for row in combined_rows if not isinstance(row.get("position_xyz_au"), list)
    )
    meta["horizons"] = horizons
    passes_meta_obj = meta.get("passes")
    passes_meta = dict(passes_meta_obj) if isinstance(passes_meta_obj, dict) else {}
    passes_meta["count"] = len(combined_passes)
    meta["passes"] = passes_meta
    combined_payload["meta"] = meta

    return {"success": True, "data": combined_payload}
