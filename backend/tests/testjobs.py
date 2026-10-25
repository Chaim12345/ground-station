# Copyright (c) 2026 Efstratios Goudelis
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Unit tests for background scheduler job orchestration."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

import server.scheduler as scheduler_module


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Scheduler:
    def __init__(self):
        self.jobs = []
        self.started = False
        self.shutdown_wait = None

    def add_job(self, func, trigger, args=(), id=None, name=None, **kwargs):
        self.jobs.append(
            SimpleNamespace(
                func=func,
                trigger=trigger,
                args=args,
                id=id,
                name=name,
                kwargs=kwargs,
                next_run_time=None,
            )
        )

    def get_jobs(self):
        return self.jobs

    def shutdown(self, wait):
        self.shutdown_wait = wait

    def start(self):
        self.started = True


class _TaskManager:
    def __init__(self, running_tasks=None):
        self.running_tasks = running_tasks or []
        self.started = []

    def get_running_tasks(self):
        return self.running_tasks

    async def start_task(self, **kwargs):
        self.started.append(kwargs)
        return "task-123"


@pytest.mark.asyncio
async def test_orbital_sync_job_starts_registered_task(monkeypatch):
    manager = _TaskManager()
    task = object()
    monkeypatch.setattr(
        scheduler_module, "get_task", lambda name: task if name == "orbital_sync" else None
    )

    await scheduler_module.sync_satellite_data_job(manager)

    assert manager.started == [
        {
            "func": task,
            "args": (),
            "kwargs": {},
            "name": "Scheduled Orbital Data Sync",
            "task_id": None,
        }
    ]


@pytest.mark.asyncio
async def test_generate_observations_syncs_clients_only_when_rows_change(monkeypatch):
    emitted = []
    synced = []

    class ObservationSync:
        async def sync_all_observations(self):
            synced.append(True)
            return {"success": True, "stats": {"scheduled": 2}}

    async def generate(_session):
        return {
            "success": True,
            "data": {"generated": 1, "updated": 0, "skipped": 0, "satellites_processed": 1},
        }

    async def emit():
        emitted.append(True)

    monkeypatch.setattr(scheduler_module, "AsyncSessionLocal", lambda: _SessionContext(object()))
    monkeypatch.setattr(
        scheduler_module, "generate_observations_for_monitored_satellites", generate
    )
    monkeypatch.setattr(scheduler_module.obs_events, "emit_scheduled_observations_changed", emit)
    monkeypatch.setattr(scheduler_module.obs_events, "observation_sync", ObservationSync())

    await scheduler_module.generate_observations_job()

    assert emitted == [True]
    assert synced == [True]


@pytest.mark.asyncio
async def test_celestial_sync_skips_setup_or_running_orbital_task(monkeypatch):
    calls = []

    async def setup_required(**_kwargs):
        return True

    async def refresh(**_kwargs):
        calls.append(True)
        return {"success": True}

    monkeypatch.setattr(scheduler_module.authsvc, "is_setup_required", setup_required)
    monkeypatch.setattr(scheduler_module, "refresh_celestial_vector_snapshots_cache", refresh)
    await scheduler_module.sync_celestial_vector_snapshots_job(_TaskManager())
    assert calls == []

    async def setup_complete(**_kwargs):
        return False

    monkeypatch.setattr(scheduler_module.authsvc, "is_setup_required", setup_complete)
    await scheduler_module.sync_celestial_vector_snapshots_job(
        _TaskManager([{"name": "Orbital Data Sync", "command": "orbital_sync"}])
    )
    assert calls == []


@pytest.mark.asyncio
async def test_celestial_sync_resyncs_trackers_and_broadcasts_after_success(monkeypatch):
    calls = []

    async def setup_complete(**_kwargs):
        return False

    async def refresh(**_kwargs):
        calls.append("refresh")
        return {"success": True, "refreshed": 2, "failed": 0, "count": 2}

    async def resync():
        calls.append("resync")
        return {"active": 1, "resynced": 1, "failed": 0}

    async def broadcast(sio):
        calls.append(("broadcast", sio))

    async def emit_status(sio, _logger):
        calls.append(("status", sio))

    sio = object()
    monkeypatch.setattr(scheduler_module.authsvc, "is_setup_required", setup_complete)
    monkeypatch.setattr(scheduler_module, "refresh_celestial_vector_snapshots_cache", refresh)
    monkeypatch.setattr(scheduler_module, "_resync_active_non_satellite_trackers", resync)
    monkeypatch.setattr(scheduler_module, "emit_cached_celestial_tracks_job", broadcast)
    monkeypatch.setattr(scheduler_module, "emit_celestial_ephemeris_status", emit_status)

    await scheduler_module.sync_celestial_vector_snapshots_job(_TaskManager(), sio=sio)

    assert calls == ["refresh", ("status", sio), "resync", ("broadcast", sio)]


@pytest.mark.asyncio
async def test_celestial_sync_broadcasts_status_after_failure(monkeypatch):
    calls = []

    async def setup_complete(**_kwargs):
        return False

    async def refresh(**_kwargs):
        calls.append("refresh")
        return {"success": False, "refreshed": 0, "failed": 2, "count": 2}

    async def emit_status(sio, _logger):
        calls.append(("status", sio))

    sio = object()
    monkeypatch.setattr(scheduler_module.authsvc, "is_setup_required", setup_complete)
    monkeypatch.setattr(scheduler_module, "refresh_celestial_vector_snapshots_cache", refresh)
    monkeypatch.setattr(scheduler_module, "emit_celestial_ephemeris_status", emit_status)

    await scheduler_module.sync_celestial_vector_snapshots_job(_TaskManager(), sio=sio)

    assert calls == ["refresh", ("status", sio)]


@pytest.mark.asyncio
async def test_cached_celestial_broadcast_clears_clients_without_targets(monkeypatch):
    emitted = []

    class _Sio:
        async def emit(self, event, payload):
            emitted.append((event, payload))

    async def setup_complete(**_kwargs):
        return False

    async def empty_payload():
        return {"celestial": []}

    monkeypatch.setattr(scheduler_module.authsvc, "is_setup_required", setup_complete)
    monkeypatch.setattr(
        scheduler_module,
        "_build_enabled_monitored_celestial_payload",
        empty_payload,
    )

    await scheduler_module.emit_cached_celestial_tracks_job(_Sio())

    assert emitted == [
        (
            "celestial-tracks-update",
            {
                "celestial": [],
                "celestial_passes": [],
                "observer_bodies": [],
            },
        )
    ]


@pytest.mark.asyncio
async def test_cached_celestial_broadcast_sends_live_deltas_between_full_forecasts(monkeypatch):
    emitted = []
    build_calls = []
    revision = ["vector-1"]

    class _Sio:
        async def emit(self, event, payload):
            emitted.append((event, payload))

    async def setup_complete(**_kwargs):
        return False

    async def monitored_payload():
        return {"celestial": [{"target_type": "mission", "command": "Voyager 1"}]}

    async def build_tracks(**kwargs):
        build_calls.append(kwargs["include_passes"])
        return {
            "success": True,
            "data": {
                "timestamp_utc": "2026-10-25T12:00:00Z",
                "celestial": [
                    {
                        "target_key": "mission:voyager-1",
                        "fetched_at_utc": revision[0],
                        "orbit_sample_times_utc": ["first", "last"],
                        "orbit_samples_xyz_au": [[1, 2, 3], [4, 5, 6]],
                        "sky_position": {"az_deg": 20.0},
                    }
                ],
                "celestial_passes": [{"id": "pass-1"}] if kwargs["include_passes"] else [],
                "observer_bodies": [{"target_key": "body:sun"}],
                "meta": {"observer_location": {"lat": 42.0, "lon": 23.0}},
            },
        }

    monkeypatch.setattr(scheduler_module.authsvc, "is_setup_required", setup_complete)
    monkeypatch.setattr(
        scheduler_module, "_build_enabled_monitored_celestial_payload", monitored_payload
    )
    monkeypatch.setattr(scheduler_module, "build_celestial_tracks", build_tracks)
    monkeypatch.setattr(scheduler_module, "_celestial_last_full_signature", None)
    monkeypatch.setattr(scheduler_module, "_celestial_last_full_at", 0.0)

    await scheduler_module.emit_cached_celestial_tracks_job(_Sio())
    await scheduler_module.emit_cached_celestial_tracks_job(_Sio())
    revision[0] = "vector-2"
    await scheduler_module.emit_cached_celestial_tracks_job(_Sio())
    monkeypatch.setattr(
        scheduler_module,
        "_celestial_last_full_at",
        scheduler_module.time.monotonic()
        - scheduler_module.CELESTIAL_FULL_TRACKS_INTERVAL_SECONDS
        - 1,
    )
    await scheduler_module.emit_cached_celestial_tracks_job(_Sio())

    assert build_calls == [True, False, False, True, True]
    assert [event for event, _payload in emitted] == [
        "celestial-tracks-update",
        "celestial-tracks-live-update",
        "celestial-tracks-update",
        "celestial-tracks-update",
    ]
    assert "orbit_samples_xyz_au" not in emitted[1][1]["celestial"][0]
    assert "celestial_passes" not in emitted[1][1]


@pytest.mark.asyncio
async def test_cached_celestial_payload_uses_each_targets_projection(monkeypatch):
    class _SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    async def fetch_monitored(_session, *, enabled_only):
        assert enabled_only is True
        return {
            "success": True,
            "data": [
                {
                    "target_type": "body",
                    "body_id": "moon",
                    "display_name": "Moon",
                    "color": "#FFFFFF",
                    "projection_past_hours": 12,
                    "projection_future_hours": 72,
                    "projection_step_minutes": 10,
                },
                {
                    "target_type": "mission",
                    "command": "-170",
                    "display_name": "JWST",
                    "color": "#00FFFF",
                    "projection_past_hours": 6,
                    "projection_future_hours": 168,
                    "projection_step_minutes": 30,
                },
            ],
        }

    monkeypatch.setattr(scheduler_module, "AsyncSessionLocal", _SessionContext)
    monkeypatch.setattr(
        scheduler_module.crud_monitored,
        "fetch_monitored_celestial",
        fetch_monitored,
    )

    payload = await scheduler_module._build_enabled_monitored_celestial_payload()

    assert "past_hours" not in payload
    assert "future_hours" not in payload
    assert "step_minutes" not in payload
    assert payload["celestial"] == [
        {
            "target_type": "body",
            "body_id": "moon",
            "name": "Moon",
            "color": "#FFFFFF",
            "past_hours": 12,
            "future_hours": 72,
            "step_minutes": 10,
        },
        {
            "target_type": "mission",
            "command": "-170",
            "name": "JWST",
            "color": "#00FFFF",
            "past_hours": 6,
            "future_hours": 168,
            "step_minutes": 30,
        },
    ]


def test_start_and_stop_scheduler_register_expected_jobs(monkeypatch):
    references = []
    monkeypatch.setattr(scheduler_module, "AsyncIOScheduler", _Scheduler)
    monkeypatch.setattr(scheduler_module, "set_scheduler_reference", references.append)
    monkeypatch.setattr(scheduler_module, "scheduler", None)
    monkeypatch.setattr(scheduler_module.arguments, "celestial_periodic_sync_enabled", False)

    scheduler = scheduler_module.start_scheduler(object(), object(), _TaskManager())
    repeated = scheduler_module.start_scheduler(object(), object(), _TaskManager())
    scheduler_module.stop_scheduler()

    assert scheduler.started is True
    assert repeated is scheduler
    assert {job.id for job in scheduler.jobs} == {
        "sync_satellite_data",
        "check_restart_decoders",
        "generate_observations",
        scheduler_module.CELESTIAL_TRACKS_BROADCAST_JOB_ID,
    }
    orbital_sync_job = next(job for job in scheduler.jobs if job.id == "sync_satellite_data")
    assert orbital_sync_job.trigger.interval == timedelta(hours=12)
    assert scheduler.shutdown_wait is False
    assert references == [scheduler, None]


def test_celestial_sync_is_scheduled_before_minimum_projection_expires(monkeypatch):
    monkeypatch.setattr(scheduler_module, "AsyncIOScheduler", _Scheduler)
    monkeypatch.setattr(scheduler_module, "set_scheduler_reference", lambda _scheduler: None)
    monkeypatch.setattr(scheduler_module, "scheduler", None)
    monkeypatch.setattr(scheduler_module.arguments, "celestial_periodic_sync_enabled", True)
    monkeypatch.setattr(
        scheduler_module.arguments,
        "celestial_periodic_sync_interval_minutes",
        60,
    )

    scheduler = scheduler_module.start_scheduler(object(), object(), _TaskManager())
    try:
        sync_job = next(
            job for job in scheduler.jobs if job.id == "sync_celestial_vector_snapshots"
        )
        assert sync_job.trigger.interval == timedelta(
            minutes=scheduler_module.CELESTIAL_SYNC_MAX_INTERVAL_MINUTES
        )
    finally:
        scheduler_module.stop_scheduler()
