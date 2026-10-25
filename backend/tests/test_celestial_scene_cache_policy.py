import asyncio
import threading
from datetime import datetime, timedelta, timezone

import pytest

from celestial import (
    bodies,
    horizons,
    observer,
    scene,
    settings,
    snapshots,
    sync,
    syncstate,
    tracks,
    trajectory,
)
from crud.celestialvectors import fetch_celestial_vector_snapshot_stats
from db.models import CelestialTargets, CelestialVectorSnapshots
from handlers.entities import celestial as celestial_handlers


class _DummyLogger:
    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None

    def debug(self, *_args, **_kwargs):
        return None


@pytest.mark.asyncio
async def test_live_tracks_skip_pass_forecast_without_losing_current_row(monkeypatch):
    async def observer_location():
        return None

    async def earth_vectors(**_kwargs):
        return None, []

    async def celestial_rows(*_args, **_kwargs):
        return [{"target_key": "mission:voyager-1", "sky_position": {"az_deg": 42.0}}]

    def unexpected_pass_build(**_kwargs):
        raise AssertionError("live ticks must not rebuild pass forecasts")

    monkeypatch.setattr(tracks, "_load_observer_location", observer_location)
    monkeypatch.setattr(tracks, "_load_earth_observer_vectors", earth_vectors)
    monkeypatch.setattr(tracks, "_fetch_celestial_with_cache", celestial_rows)
    monkeypatch.setattr(tracks, "_build_celestial_passes", unexpected_pass_build)

    result = await tracks.build_celestial_tracks(
        data={"celestial": [{"target_type": "mission", "command": "Voyager 1"}]},
        logger=_DummyLogger(),
        register_targets=False,
        allow_network_fetch=False,
        include_passes=False,
    )

    assert result["success"] is True
    assert result["data"]["celestial"][0]["sky_position"]["az_deg"] == 42.0
    assert result["data"]["celestial_passes"] == []


def test_projection_options_use_operational_defaults_and_limits():
    assert settings._parse_projection_options(None) == (1, 24, 60)

    past_hours, future_hours, _step_minutes = settings._parse_projection_options(
        {
            "past_hours": 4320,
            "future_hours": 4320,
            "step_minutes": 60,
        }
    )

    assert past_hours == 168
    assert future_hours == 720


def test_earth_cache_projections_keep_only_non_dominated_coverage():
    targets = [
        {"past_hours": 12, "future_hours": 168, "step_minutes": 60},
        {"past_hours": 168, "future_hours": 720, "step_minutes": 60},
        {"past_hours": 1, "future_hours": 24, "step_minutes": 60},
        {"past_hours": 12, "future_hours": 72, "step_minutes": 10},
    ]

    assert sync._build_earth_cache_projections(targets) == [
        (12, 72, 10),
        (168, 720, 60),
    ]


@pytest.mark.asyncio
async def test_build_tracks_groups_targets_by_their_persisted_projection(monkeypatch):
    calls = []

    async def _register(*_args, **_kwargs):
        return None

    async def _build_group(*, data, **_kwargs):
        calls.append((data["past_hours"], data["future_hours"], data["step_minutes"]))
        target = data["celestial"][0]
        return {
            "success": True,
            "data": {
                "timestamp_utc": "2026-01-01T12:00:00+00:00",
                "celestial": [{"target_key": target["target_key"]}],
                "celestial_passes": [],
                "observer_bodies": [],
                "meta": {"passes": {"count": 0}},
            },
        }

    monkeypatch.setattr(tracks, "_ensure_scene_targets_registered", _register)
    monkeypatch.setattr(tracks, "_build_celestial_tracks_single_projection", _build_group)

    result = await scene.build_celestial_tracks(
        data={
            "celestial": [
                {
                    "target_type": "mission",
                    "target_key": "mission:short",
                    "command": "Short",
                    "past_hours": 1,
                    "future_hours": 24,
                    "step_minutes": 60,
                },
                {
                    "target_type": "mission",
                    "target_key": "mission:long",
                    "command": "Long",
                    "past_hours": 24,
                    "future_hours": 168,
                    "step_minutes": 15,
                },
            ]
        },
        logger=_DummyLogger(),
    )

    assert calls == [(24, 168, 15), (1, 24, 60)]
    assert [row["target_key"] for row in result["data"]["celestial"]] == [
        "mission:short",
        "mission:long",
    ]
    assert result["data"]["meta"]["projection_by_target"] == {
        "mission:short": {"past_hours": 1, "future_hours": 24, "step_minutes": 60},
        "mission:long": {"past_hours": 24, "future_hours": 168, "step_minutes": 15},
    }


@pytest.mark.asyncio
async def test_earth_cannot_be_created_as_a_monitored_target():
    result = await celestial_handlers._validate_monitored_target_payload(
        {
            "target_type": "body",
            "body_id": "earth",
            "display_name": "Earth",
        }
    )

    assert result == {
        "success": False,
        "error": "Body 'Earth' cannot be monitored",
    }


@pytest.mark.asyncio
async def test_monitored_refresh_waits_for_an_active_refresh(monkeypatch):
    first_refresh_started = asyncio.Event()
    release_first_refresh = asyncio.Event()
    build_calls = 0

    class _SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, _exc_type, _exc, _traceback):
            return False

    async def _fetch_monitored(_session):
        return {
            "success": True,
            "data": [
                {
                    "id": "sun-id",
                    "target_key": "body:sun",
                    "target_type": "body",
                    "body_id": "sun",
                    "display_name": "Sun",
                    "enabled": True,
                }
            ],
        }

    async def _update_refresh_state(_session, _updates):
        return {"success": True}

    async def _load_observer_location():
        return None

    async def _build_tracks(**_kwargs):
        nonlocal build_calls
        build_calls += 1
        if build_calls == 1:
            first_refresh_started.set()
            await release_first_refresh.wait()
        return {
            "success": True,
            "data": {"celestial": [{"target_key": "body:sun", "error": None}]},
        }

    monkeypatch.setattr(celestial_handlers, "_monitored_refresh_lock", asyncio.Lock())
    monkeypatch.setattr(celestial_handlers, "AsyncSessionLocal", _SessionContext)
    monkeypatch.setattr(
        celestial_handlers.crud_monitored,
        "fetch_monitored_celestial",
        _fetch_monitored,
    )
    monkeypatch.setattr(
        celestial_handlers.crud_monitored,
        "update_monitored_celestial_refresh_state",
        _update_refresh_state,
    )
    monkeypatch.setattr(
        celestial_handlers,
        "_load_stream_observer_location",
        _load_observer_location,
    )
    monkeypatch.setattr(celestial_handlers, "_build_partial_row_emitter", lambda **_kwargs: None)
    monkeypatch.setattr(celestial_handlers, "build_celestial_tracks", _build_tracks)

    request = {"ids": ["sun-id"]}
    first = asyncio.create_task(
        celestial_handlers.refresh_monitored_celestial_now(
            object(), request, _DummyLogger(), "first-client"
        )
    )
    await first_refresh_started.wait()
    second = asyncio.create_task(
        celestial_handlers.refresh_monitored_celestial_now(
            object(), request, _DummyLogger(), "second-client"
        )
    )
    await asyncio.sleep(0)

    assert not second.done()
    assert build_calls == 1

    release_first_refresh.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert first_result["success"] is True
    assert second_result["success"] is True
    assert build_calls == 2


@pytest.fixture(autouse=True)
def _reset_horizons_availability():
    horizons.reset_horizons_circuit()
    syncstate.reset_celestial_sync_state()
    snapshots._VECTOR_FETCH_TASKS.clear()
    yield
    snapshots._VECTOR_FETCH_TASKS.clear()
    horizons.reset_horizons_circuit()
    syncstate.reset_celestial_sync_state()


@pytest.mark.asyncio
async def test_get_vectors_snapshot_returns_exact_cache_hit(monkeypatch):
    epoch = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    payload = {
        "command": "Voyager 1",
        "position_xyz_au": [1.0, 0.0, 0.0],
        "orbit_samples_xyz_au": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
        "orbit_sample_times_utc": [
            (epoch - timedelta(hours=1)).isoformat(),
            (epoch + timedelta(hours=24)).isoformat(),
        ],
    }

    async def _stub_load_vectors_from_db(*_args, **_kwargs):
        return {"payload": payload}

    def _unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("Network fetch should not run on exact cache hit")

    monkeypatch.setattr(snapshots, "_load_vectors_from_db", _stub_load_vectors_from_db)
    monkeypatch.setattr(snapshots, "fetch_celestial_vectors", _unexpected_fetch)

    result = await snapshots._get_vectors_snapshot(
        command="Voyager 1",
        epoch=epoch,
        past_hours=1,
        future_hours=24,
        step_minutes=60,
        observer_location={"lat": 40.0, "lon": 22.0},
        force_refresh=False,
        logger=_DummyLogger(),
        allow_network_fetch=True,
    )

    assert result["cache"] == "db-hit"
    assert result["stale"] is False
    assert result["payload"]["command"] == "Voyager 1"


@pytest.mark.asyncio
async def test_get_vectors_snapshot_refetches_shifted_exact_projection(monkeypatch):
    epoch = datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc)
    old_payload = {
        "command": "301",
        "position_xyz_au": [1.0, 0.0, 0.0],
        "orbit_samples_xyz_au": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
        "orbit_sample_times_utc": [
            (epoch - timedelta(hours=2)).isoformat(),
            epoch.isoformat(),
        ],
    }
    fetched_payload = {
        "command": "301",
        "position_xyz_au": [2.0, 0.0, 0.0],
        "orbit_samples_xyz_au": [
            [0.0, 0.0, 0.0],
            [2.0, 0.0, 0.0],
            [4.0, 0.0, 0.0],
        ],
        "orbit_sample_times_utc": [
            (epoch - timedelta(hours=2)).isoformat(),
            epoch.isoformat(),
            (epoch + timedelta(hours=2)).isoformat(),
        ],
    }
    fetch_calls = []
    stored = []

    async def _old_projection(*_args, **_kwargs):
        return {"payload": old_payload}

    async def _no_compatible_projection(*_args, **_kwargs):
        return None

    def _fetch(command, fetch_epoch, past_hours, future_hours, step_minutes, **kwargs):
        fetch_calls.append(
            (
                command,
                fetch_epoch,
                past_hours,
                future_hours,
                step_minutes,
                kwargs.get("force_probe"),
            )
        )
        return fetched_payload

    async def _store(**kwargs):
        stored.append(kwargs)

    monkeypatch.setattr(snapshots, "_load_vectors_from_db", _old_projection)
    monkeypatch.setattr(snapshots, "_load_latest_vectors_from_db", _old_projection)
    monkeypatch.setattr(
        snapshots,
        "_load_latest_vectors_for_target_from_db",
        _no_compatible_projection,
    )
    monkeypatch.setattr(snapshots, "fetch_celestial_vectors", _fetch)
    monkeypatch.setattr(snapshots, "_store_vectors_in_db", _store)

    result = await snapshots._get_vectors_snapshot(
        command="301",
        target_key="body:moon",
        epoch=epoch,
        past_hours=1,
        future_hours=1,
        step_minutes=60,
        observer_location={"lat": 40.0, "lon": 22.0},
        force_refresh=False,
        logger=_DummyLogger(),
        allow_network_fetch=True,
    )

    assert fetch_calls == [("301", epoch, 2, 49, 60, False)]
    assert len(stored) == 1
    assert stored[0]["past_hours"] == 2
    assert stored[0]["future_hours"] == 49
    assert stored[0]["payload"]["orbit_sample_times_utc"] == [
        (epoch - timedelta(hours=2)).isoformat(),
        epoch.isoformat(),
        (epoch + timedelta(hours=2)).isoformat(),
    ]
    assert result["cache"] == "db-miss"
    assert result["payload"]["orbit_sample_times_utc"] == [
        (epoch - timedelta(hours=1)).isoformat(),
        epoch.isoformat(),
        (epoch + timedelta(hours=1)).isoformat(),
    ]
    assert result["payload"]["orbit_samples_xyz_au"] == [
        [1.0, 0.0, 0.0],
        [2.0, 0.0, 0.0],
        [3.0, 0.0, 0.0],
    ]

    async def _padded_projection(*_args, **_kwargs):
        return {"payload": stored[0]["payload"]}

    def _unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("The padded snapshot should cover the shifted window")

    monkeypatch.setattr(snapshots, "_load_vectors_from_db", _padded_projection)
    monkeypatch.setattr(snapshots, "fetch_celestial_vectors", _unexpected_fetch)
    shifted_epoch = epoch + timedelta(minutes=5)
    shifted_result = await snapshots._get_vectors_snapshot(
        command="301",
        target_key="body:moon",
        epoch=shifted_epoch,
        past_hours=1,
        future_hours=1,
        step_minutes=60,
        observer_location={"lat": 40.0, "lon": 22.0},
        force_refresh=False,
        logger=_DummyLogger(),
        allow_network_fetch=True,
    )

    assert shifted_result["cache"] == "db-hit"
    assert (
        shifted_result["payload"]["orbit_sample_times_utc"][0]
        == (shifted_epoch - timedelta(hours=1)).isoformat()
    )
    assert (
        shifted_result["payload"]["orbit_sample_times_utc"][-1]
        == (shifted_epoch + timedelta(hours=1)).isoformat()
    )


@pytest.mark.asyncio
async def test_get_vectors_snapshot_coalesces_identical_network_misses(monkeypatch):
    epoch = datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc)
    fetch_started = threading.Event()
    release_fetch = threading.Event()
    fetch_calls = 0
    store_calls = 0
    fetched_payload = {
        "command": "501",
        "position_xyz_au": [2.0, 0.0, 0.0],
        "orbit_samples_xyz_au": [
            [0.0, 0.0, 0.0],
            [2.0, 0.0, 0.0],
            [4.0, 0.0, 0.0],
        ],
        "orbit_sample_times_utc": [
            (epoch - timedelta(hours=2)).isoformat(),
            epoch.isoformat(),
            (epoch + timedelta(hours=2)).isoformat(),
        ],
    }

    def _fetch(*_args, **_kwargs):
        nonlocal fetch_calls
        fetch_calls += 1
        fetch_started.set()
        assert release_fetch.wait(timeout=2)
        return fetched_payload

    async def _store(**_kwargs):
        nonlocal store_calls
        store_calls += 1

    monkeypatch.setattr(snapshots, "fetch_celestial_vectors", _fetch)
    monkeypatch.setattr(snapshots, "_store_vectors_in_db", _store)

    request = {
        "command": "501",
        "target_key": "body:io",
        "epoch": epoch,
        "past_hours": 1,
        "future_hours": 1,
        "step_minutes": 60,
        "observer_location": {"lat": 40.0, "lon": 22.0},
        "force_refresh": True,
        "logger": _DummyLogger(),
        "allow_network_fetch": True,
    }
    first = asyncio.create_task(snapshots._get_vectors_snapshot(**request))
    assert await asyncio.to_thread(fetch_started.wait, 2)
    second = asyncio.create_task(snapshots._get_vectors_snapshot(**request))
    await asyncio.sleep(0)
    release_fetch.set()

    first_result, second_result = await asyncio.gather(first, second)

    assert fetch_calls == 1
    assert store_calls == 2
    assert first_result["payload"] == second_result["payload"]


@pytest.mark.asyncio
async def test_cache_only_snapshot_keeps_its_matching_projection_when_window_shifts(
    monkeypatch,
):
    epoch = datetime(2026, 1, 1, 13, 5, tzinfo=timezone.utc)
    matching_payload = {
        "command": "399",
        "position_xyz_au": [1.0, 0.0, 0.0],
        "orbit_samples_xyz_au": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
        "orbit_sample_times_utc": [
            (epoch - timedelta(hours=1, minutes=5)).isoformat(),
            (epoch + timedelta(hours=23, minutes=55)).isoformat(),
        ],
    }

    async def _matching_projection(*_args, **_kwargs):
        return {"payload": matching_payload}

    async def _unexpected_latest(*_args, **_kwargs):
        raise AssertionError("A matching cache-only projection should be used first")

    monkeypatch.setattr(snapshots, "_load_vectors_from_db", _matching_projection)
    monkeypatch.setattr(snapshots, "_load_latest_vectors_from_db", _unexpected_latest)

    result = await snapshots._get_vectors_snapshot(
        command="399",
        target_key="body:earth",
        epoch=epoch,
        past_hours=1,
        future_hours=24,
        step_minutes=60,
        observer_location={"lat": 40.0, "lon": 22.0},
        force_refresh=False,
        logger=_DummyLogger(),
        allow_network_fetch=False,
    )

    assert result["cache"] == "db-hit-partial"
    assert result["current_position_usable"] is True
    assert result["calculation_usable"] is True
    assert result["payload"]["orbit_sample_times_utc"] == (
        matching_payload["orbit_sample_times_utc"]
    )


async def test_get_vectors_snapshot_cache_only_returns_miss_without_exact_cache(monkeypatch):
    epoch = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    async def _stub_load_vectors_from_db(*_args, **_kwargs):
        return None

    monkeypatch.setattr(snapshots, "_load_vectors_from_db", _stub_load_vectors_from_db)
    # Cache-only mode falls back to the newest stale snapshot after its exact
    # lookup. Stub both database lookups so this test does not observe data
    # created by another test or an already-running temporary database.
    monkeypatch.setattr(snapshots, "_load_latest_vectors_from_db", _stub_load_vectors_from_db)

    result = await snapshots._get_vectors_snapshot(
        command="Voyager 1",
        epoch=epoch,
        past_hours=1,
        future_hours=24,
        step_minutes=60,
        observer_location={"lat": 40.0, "lon": 22.0},
        force_refresh=True,
        logger=_DummyLogger(),
        allow_network_fetch=False,
    )

    assert result["cache"] == "cache-only-miss"
    assert result["stale"] is True
    assert result["payload"] is None
    assert "No cached vectors available" in str(result["error"])


@pytest.mark.asyncio
async def test_get_vectors_snapshot_uses_fresh_snapshot_with_different_projection(monkeypatch):
    epoch = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    compatible_payload = {
        "command": "Venus",
        "position_xyz_au": [0.0, 0.0, 0.0],
        "orbit_samples_xyz_au": [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [3.0, 0.0, 0.0],
            [7.0, 0.0, 0.0],
        ],
        "orbit_sample_times_utc": [
            (epoch - timedelta(hours=24)).isoformat(),
            epoch.isoformat(),
            (epoch + timedelta(hours=24)).isoformat(),
            (epoch + timedelta(hours=72)).isoformat(),
        ],
    }

    async def _no_projection_match(*_args, **_kwargs):
        return None

    async def _fresh_compatible_snapshot(*_args, **_kwargs):
        return {
            "payload": compatible_payload,
            "past_hours": 24,
            "future_hours": 72,
            "step_minutes": 60,
        }

    monkeypatch.setattr(snapshots, "_load_vectors_from_db", _no_projection_match)
    monkeypatch.setattr(snapshots, "_load_latest_vectors_from_db", _no_projection_match)
    monkeypatch.setattr(
        snapshots,
        "_load_latest_vectors_for_target_from_db",
        _fresh_compatible_snapshot,
    )

    result = await snapshots._get_vectors_snapshot(
        command="Venus",
        target_key="body:venus",
        epoch=epoch,
        past_hours=0,
        future_hours=24,
        step_minutes=60,
        observer_location={"lat": 40.0, "lon": 22.0},
        force_refresh=False,
        logger=_DummyLogger(),
        allow_network_fetch=False,
    )

    assert result["cache"] == "db-compatible-hit"
    assert result["stale"] is False
    assert result["payload"]["position_xyz_au"] == [1.0, 0.0, 0.0]
    assert result["payload"]["orbit_sample_times_utc"] == [
        epoch.isoformat(),
        (epoch + timedelta(hours=24)).isoformat(),
    ]
    assert result["payload"]["orbit_samples_xyz_au"] == [
        [1.0, 0.0, 0.0],
        [3.0, 0.0, 0.0],
    ]


@pytest.mark.asyncio
async def test_network_sync_reuses_compatible_snapshot_without_derived_database_row(monkeypatch):
    epoch = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    compatible_payload = {
        "command": "501",
        "position_xyz_au": [1.0, 0.0, 0.0],
        "orbit_samples_xyz_au": [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [3.0, 0.0, 0.0],
            [7.0, 0.0, 0.0],
        ],
        "orbit_sample_times_utc": [
            (epoch - timedelta(hours=24)).isoformat(),
            epoch.isoformat(),
            (epoch + timedelta(hours=24)).isoformat(),
            (epoch + timedelta(hours=72)).isoformat(),
        ],
    }
    stored = []

    async def _no_projection_match(*_args, **_kwargs):
        return None

    async def _compatible_snapshot(*_args, **_kwargs):
        return {
            "payload": compatible_payload,
            "past_hours": 24,
            "future_hours": 72,
            "step_minutes": 60,
        }

    async def _store(**kwargs):
        stored.append(kwargs)

    def _unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("The compatible Horizons snapshot should avoid a network fetch")

    monkeypatch.setattr(snapshots, "_load_vectors_from_db", _no_projection_match)
    monkeypatch.setattr(snapshots, "_load_latest_vectors_from_db", _no_projection_match)
    monkeypatch.setattr(
        snapshots,
        "_load_latest_vectors_for_target_from_db",
        _compatible_snapshot,
    )
    monkeypatch.setattr(snapshots, "_store_vectors_in_db", _store)
    monkeypatch.setattr(snapshots, "fetch_celestial_vectors", _unexpected_fetch)

    result = await snapshots._get_vectors_snapshot(
        command="501",
        target_key="body:io",
        epoch=epoch,
        past_hours=1,
        future_hours=1,
        step_minutes=60,
        observer_location={"lat": 40.0, "lon": 22.0},
        force_refresh=False,
        logger=_DummyLogger(),
        allow_network_fetch=True,
    )

    assert result["cache"] == "db-compatible-hit"
    assert stored == []

    broadcast_result = await snapshots._get_vectors_snapshot(
        command="501",
        target_key="body:io",
        epoch=epoch + timedelta(seconds=5),
        past_hours=1,
        future_hours=1,
        step_minutes=60,
        observer_location={"lat": 40.0, "lon": 22.0},
        force_refresh=False,
        logger=_DummyLogger(),
        allow_network_fetch=False,
    )

    assert broadcast_result["cache"] == "db-compatible-hit"
    assert (
        broadcast_result["payload"]["orbit_sample_times_utc"][-1]
        == (epoch + timedelta(hours=1, seconds=5)).isoformat()
    )


def test_wider_cached_trajectory_is_trimmed_to_requested_window():
    epoch = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    payload = {
        "orbit_samples_xyz_au": [
            [0.0, 0.0, 0.0],
            [6.0, 0.0, 0.0],
            [18.0, 0.0, 0.0],
            [30.0, 0.0, 0.0],
        ],
        "orbit_sample_times_utc": [
            (epoch - timedelta(hours=6)).isoformat(),
            epoch.isoformat(),
            (epoch + timedelta(hours=12)).isoformat(),
            (epoch + timedelta(hours=24)).isoformat(),
        ],
    }

    trimmed = trajectory._trim_payload_to_projection_window(
        payload=payload,
        epoch=epoch,
        past_hours=1,
        future_hours=12,
    )

    assert trimmed is True
    assert payload["orbit_sample_times_utc"] == [
        (epoch - timedelta(hours=1)).isoformat(),
        epoch.isoformat(),
        (epoch + timedelta(hours=12)).isoformat(),
    ]
    assert payload["orbit_samples_xyz_au"] == [
        [5.0, 0.0, 0.0],
        [6.0, 0.0, 0.0],
        [18.0, 0.0, 0.0],
    ]


@pytest.mark.asyncio
async def test_get_vectors_snapshot_fetches_when_fresh_projection_is_too_short(monkeypatch):
    epoch = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    short_payload = {
        "position_xyz_au": [2.0, 0.0, 0.0],
        "orbit_samples_xyz_au": [[1.0, 0.0, 0.0], [3.0, 0.0, 0.0]],
        "orbit_sample_times_utc": [
            (epoch - timedelta(hours=1)).isoformat(),
            (epoch + timedelta(hours=1)).isoformat(),
        ],
    }
    fetched_payload = {
        "position_xyz_au": [4.0, 0.0, 0.0],
        "orbit_samples_xyz_au": [[4.0, 0.0, 0.0], [5.0, 0.0, 0.0]],
        "orbit_sample_times_utc": [
            (epoch - timedelta(hours=1)).isoformat(),
            (epoch + timedelta(hours=24)).isoformat(),
        ],
    }
    calls = {"fetch": 0}

    async def _no_projection_match(*_args, **_kwargs):
        return None

    async def _short_snapshot(*_args, **_kwargs):
        return {
            "payload": short_payload,
            "past_hours": 1,
            "future_hours": 1,
            "step_minutes": 60,
        }

    def _fetch(*_args, **_kwargs):
        calls["fetch"] += 1
        return fetched_payload

    async def _store(*_args, **_kwargs):
        return None

    monkeypatch.setattr(snapshots, "_load_vectors_from_db", _no_projection_match)
    monkeypatch.setattr(snapshots, "_load_latest_vectors_from_db", _no_projection_match)
    monkeypatch.setattr(snapshots, "_load_latest_vectors_for_target_from_db", _short_snapshot)
    monkeypatch.setattr(snapshots, "fetch_celestial_vectors", _fetch)
    monkeypatch.setattr(snapshots, "_store_vectors_in_db", _store)

    result = await snapshots._get_vectors_snapshot(
        command="301",
        target_key="body:moon",
        epoch=epoch,
        past_hours=1,
        future_hours=24,
        step_minutes=60,
        observer_location={"lat": 40.0, "lon": 22.0},
        force_refresh=False,
        logger=_DummyLogger(),
        allow_network_fetch=True,
    )

    assert calls["fetch"] == 1
    assert result["cache"] == "db-miss"
    assert result["payload"]["orbit_sample_times_utc"] == [
        (epoch - timedelta(hours=1)).isoformat(),
        (epoch + timedelta(hours=24)).isoformat(),
    ]
    assert result["payload"] != short_payload


@pytest.mark.asyncio
async def test_cache_only_short_projection_is_current_position_only(monkeypatch):
    epoch = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    short_payload = {
        "position_xyz_au": [0.0, 0.0, 0.0],
        "orbit_samples_xyz_au": [[1.0, 0.0, 0.0], [3.0, 0.0, 0.0]],
        "orbit_sample_times_utc": [
            (epoch - timedelta(hours=1)).isoformat(),
            (epoch + timedelta(hours=1)).isoformat(),
        ],
    }

    async def _no_projection_match(*_args, **_kwargs):
        return None

    async def _short_snapshot(*_args, **_kwargs):
        return {
            "payload": short_payload,
            "past_hours": 1,
            "future_hours": 1,
            "step_minutes": 60,
        }

    monkeypatch.setattr(snapshots, "_load_vectors_from_db", _no_projection_match)
    monkeypatch.setattr(snapshots, "_load_latest_vectors_from_db", _no_projection_match)
    monkeypatch.setattr(snapshots, "_load_latest_vectors_for_target_from_db", _short_snapshot)

    result = await snapshots._get_vectors_snapshot(
        command="301",
        target_key="body:moon",
        epoch=epoch,
        past_hours=1,
        future_hours=24,
        step_minutes=60,
        observer_location={"lat": 40.0, "lon": 22.0},
        force_refresh=False,
        logger=_DummyLogger(),
        allow_network_fetch=False,
    )

    assert result["cache"] == "db-compatible-current-only"
    assert result["current_position_usable"] is True
    assert result["calculation_usable"] is False
    assert result["payload"]["position_xyz_au"] == [2.0, 0.0, 0.0]


@pytest.mark.asyncio
async def test_get_vectors_snapshot_fetches_and_stores_on_cache_miss(monkeypatch):
    epoch = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    calls = {"fetch": 0, "store": 0}
    fetched_payload = {
        "command": "Voyager 1",
        "position_xyz_au": [1.0, 0.0, 0.0],
        "orbit_samples_xyz_au": [[1.0, 0.0, 0.0], [1.0, 0.1, 0.0]],
        "orbit_sample_times_utc": [
            datetime(2026, 1, 1, 11, 0, tzinfo=timezone.utc).isoformat(),
            datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc).isoformat(),
        ],
    }

    async def _stub_load_vectors_from_db(*_args, **_kwargs):
        return None

    def _stub_fetch_celestial_vectors(*_args, **_kwargs):
        calls["fetch"] += 1
        return dict(fetched_payload)

    async def _stub_store_vectors_in_db(*_args, **_kwargs):
        calls["store"] += 1
        return None

    monkeypatch.setattr(snapshots, "_load_vectors_from_db", _stub_load_vectors_from_db)
    monkeypatch.setattr(snapshots, "_load_latest_vectors_from_db", _stub_load_vectors_from_db)
    monkeypatch.setattr(
        snapshots,
        "_load_latest_vectors_for_target_from_db",
        _stub_load_vectors_from_db,
    )
    monkeypatch.setattr(snapshots, "fetch_celestial_vectors", _stub_fetch_celestial_vectors)
    monkeypatch.setattr(snapshots, "_store_vectors_in_db", _stub_store_vectors_in_db)

    result = await snapshots._get_vectors_snapshot(
        command="Voyager 1",
        epoch=epoch,
        past_hours=1,
        future_hours=24,
        step_minutes=60,
        observer_location={"lat": 40.0, "lon": 22.0},
        force_refresh=False,
        logger=_DummyLogger(),
        allow_network_fetch=True,
    )

    assert result["cache"] == "db-miss"
    assert result["stale"] is False
    assert result["payload"]["command"] == "Voyager 1"
    assert calls["fetch"] == 1
    assert calls["store"] == 1


@pytest.mark.asyncio
async def test_get_vectors_snapshot_returns_miss_on_fetch_error_without_fallback(monkeypatch):
    epoch = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    async def _stub_load_vectors_from_db(*_args, **_kwargs):
        return None

    def _failing_fetch(*_args, **_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(snapshots, "_load_vectors_from_db", _stub_load_vectors_from_db)
    monkeypatch.setattr(snapshots, "_load_latest_vectors_from_db", _stub_load_vectors_from_db)
    monkeypatch.setattr(
        snapshots,
        "_load_latest_vectors_for_target_from_db",
        _stub_load_vectors_from_db,
    )
    monkeypatch.setattr(snapshots, "fetch_celestial_vectors", _failing_fetch)

    result = await snapshots._get_vectors_snapshot(
        command="Voyager 1",
        epoch=epoch,
        past_hours=1,
        future_hours=24,
        step_minutes=60,
        observer_location={"lat": 40.0, "lon": 22.0},
        force_refresh=True,
        logger=_DummyLogger(),
        allow_network_fetch=True,
    )

    assert result["cache"] == "miss"
    assert result["stale"] is True
    assert result["payload"] is None
    assert "network down" in str(result["error"])


@pytest.mark.asyncio
async def test_get_vectors_snapshot_uses_expired_snapshot_after_fetch_error(monkeypatch):
    epoch = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    stale_payload = {
        "position_xyz_au": [1.0, 2.0, 3.0],
        "orbit_samples_xyz_au": [],
        "orbit_sample_times_utc": [],
        "source": "horizons",
        "fetched_at_utc": "2025-12-31T12:00:00+00:00",
    }

    async def _no_cache(*_args, **_kwargs):
        return None

    async def _stale_target_cache(*_args, **kwargs):
        return {"payload": stale_payload} if kwargs.get("valid_only") is False else None

    def _failing_fetch(*_args, **_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(snapshots, "_load_vectors_from_db", _no_cache)
    monkeypatch.setattr(snapshots, "_load_latest_vectors_from_db", _no_cache)
    monkeypatch.setattr(
        snapshots,
        "_load_latest_vectors_for_target_from_db",
        _stale_target_cache,
    )
    monkeypatch.setattr(snapshots, "fetch_celestial_vectors", _failing_fetch)

    result = await snapshots._get_vectors_snapshot(
        command="Voyager 1",
        epoch=epoch,
        past_hours=1,
        future_hours=24,
        step_minutes=60,
        observer_location=None,
        force_refresh=True,
        logger=_DummyLogger(),
        allow_network_fetch=True,
    )

    assert result["cache"] == "db-stale-fallback"
    assert result["stale"] is True
    assert result["payload"]["position_xyz_au"] == [1.0, 2.0, 3.0]


@pytest.mark.asyncio
async def test_cache_only_stale_snapshot_keeps_current_pointing_capability(monkeypatch):
    epoch = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    stale_payload = {
        "position_xyz_au": [0.0, 0.0, 0.0],
        "velocity_xyz_au_per_day": [0.0, 0.0, 0.0],
        "orbit_samples_xyz_au": [[1.0, 0.0, 0.0], [3.0, 0.0, 0.0]],
        "orbit_sample_times_utc": [
            (epoch - timedelta(hours=1)).isoformat(),
            (epoch + timedelta(hours=1)).isoformat(),
        ],
    }

    async def _no_cache(*_args, **_kwargs):
        return None

    async def _latest_projection_cache(*_args, **kwargs):
        return {"payload": stale_payload} if kwargs.get("valid_only") is False else None

    monkeypatch.setattr(snapshots, "_load_vectors_from_db", _no_cache)
    monkeypatch.setattr(snapshots, "_load_latest_vectors_from_db", _latest_projection_cache)
    monkeypatch.setattr(snapshots, "_load_latest_vectors_for_target_from_db", _no_cache)

    result = await snapshots._get_vectors_snapshot(
        command="Voyager 1",
        epoch=epoch,
        past_hours=24,
        future_hours=24,
        step_minutes=60,
        observer_location={"lat": 40.0, "lon": 22.0},
        force_refresh=False,
        logger=_DummyLogger(),
        allow_network_fetch=False,
    )

    assert result["cache"] == "db-stale-hit"
    assert result["stale"] is True
    assert result["current_position_usable"] is True
    assert result["calculation_usable"] is False
    assert result["payload"]["position_xyz_au"] == [2.0, 0.0, 0.0]


@pytest.mark.asyncio
async def test_network_request_does_not_reuse_cache_only_computed_result(monkeypatch):
    epoch = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    calls = []

    async def _snapshot(*_args, **kwargs):
        network_enabled = bool(kwargs["allow_network_fetch"])
        calls.append(network_enabled)
        return {
            "payload": {
                "command": "Voyager 1",
                "position_xyz_au": [1.0, 0.0, 0.0],
                "orbit_samples_xyz_au": [[1.0, 0.0, 0.0], [1.0, 0.1, 0.0]],
                "orbit_sample_times_utc": [
                    (epoch - timedelta(hours=1)).isoformat(),
                    (epoch + timedelta(hours=24)).isoformat(),
                ],
            },
            "cache": "db-miss" if network_enabled else "db-stale-hit",
            "stale": not network_enabled,
            "current_position_usable": network_enabled,
            "calculation_usable": network_enabled,
        }

    monkeypatch.setattr(tracks, "_get_vectors_snapshot", _snapshot)
    target = {
        "target_type": "mission",
        "target_key": "mission:voyager_1",
        "command": "Voyager 1",
        "name": "Voyager 1",
    }

    with tracks._computed_cache_lock:
        tracks._computed_cache.clear()
    try:
        cache_only_rows = await tracks._fetch_celestial_with_cache(
            targets=[target],
            epoch=epoch,
            past_hours=1,
            future_hours=24,
            step_minutes=60,
            observer_location=None,
            earth_position_xyz_au=None,
            body_snapshot_by_id={},
            force_refresh=False,
            allow_network_fetch=False,
            logger=_DummyLogger(),
        )
        network_rows = await tracks._fetch_celestial_with_cache(
            targets=[target],
            epoch=epoch,
            past_hours=1,
            future_hours=24,
            step_minutes=60,
            observer_location=None,
            earth_position_xyz_au=None,
            body_snapshot_by_id={},
            force_refresh=False,
            allow_network_fetch=True,
            logger=_DummyLogger(),
        )
    finally:
        with tracks._computed_cache_lock:
            tracks._computed_cache.clear()

    assert calls == [False, True]
    assert cache_only_rows[0]["calculation_usable"] is False
    assert network_rows[0]["calculation_usable"] is True
    assert network_rows[0]["cache"] == "db-miss"


def test_current_pointing_survives_incomplete_projection_window():
    epoch = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    row = {
        "position_xyz_au": [2.0, 1.0, 0.25],
        "calculation_usable": False,
        "current_position_usable": True,
    }

    observer._attach_observer_view_local(
        row=row,
        epoch=epoch,
        observer_location={"lat": 40.0, "lon": 22.0},
        earth_position_xyz_au=[1.0, 0.0, 0.0],
        logger=_DummyLogger(),
    )

    assert isinstance(row["sky_position"], dict)
    assert isinstance(row["sky_position"]["az_deg"], float)
    assert isinstance(row["sky_position"]["el_deg"], float)


@pytest.mark.asyncio
async def test_build_horizons_solar_system_bodies_keeps_missing_rows_without_origin_vectors(
    monkeypatch,
):
    epoch = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)

    def _stub_build_builtin_body_targets():
        return [
            {
                "body_id": "pluto",
                "target_key": "body:pluto",
                "horizons_command": "999",
                "name": "Pluto",
                "body_class": "dwarf",
                "parent_body_id": "sun",
            }
        ]

    async def _stub_ensure_scene_targets_registered(*_args, **_kwargs):
        return None

    async def _stub_get_vectors_snapshot(*_args, **_kwargs):
        return {
            "payload": None,
            "cache": "cache-only-miss",
            "stale": True,
            "error": "No data returned",
        }

    monkeypatch.setattr(bodies, "_build_builtin_body_targets", _stub_build_builtin_body_targets)
    monkeypatch.setattr(
        bodies, "_ensure_scene_targets_registered", _stub_ensure_scene_targets_registered
    )
    monkeypatch.setattr(bodies, "_get_vectors_snapshot", _stub_get_vectors_snapshot)

    solar_meta, planets = await bodies._build_horizons_solar_system_bodies(
        epoch=epoch,
        past_hours=6,
        future_hours=6,
        step_minutes=60,
        observer_location=None,
        force_refresh=True,
        allow_network_fetch=False,
        logger=_DummyLogger(),
    )

    assert solar_meta["cache"]["missing_count"] == 1
    assert len(planets) == 1
    row = planets[0]
    assert row["target_key"] == "body:pluto"
    assert row["id"] == "pluto"
    assert row["stale"] is True
    assert row["position_xyz_au"] is None
    assert row["velocity_xyz_au_per_day"] is None


@pytest.mark.asyncio
async def test_build_horizons_solar_system_bodies_uses_offline_visual_fallback(monkeypatch):
    epoch = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(
        bodies,
        "_build_builtin_body_targets",
        lambda: [
            {
                "body_id": "saturn",
                "target_key": "body:saturn",
                "horizons_command": "699",
                "name": "Saturn",
                "body_class": "planet",
                "parent_body_id": "sun",
            }
        ],
    )

    async def _noop(*_args, **_kwargs):
        return None

    async def _missing(*_args, **_kwargs):
        return {
            "payload": None,
            "cache": "miss",
            "stale": True,
            "error": "NASA JPL Horizons could not be reached",
            "error_code": "connect_timeout",
        }

    monkeypatch.setattr(bodies, "_ensure_scene_targets_registered", _noop)
    monkeypatch.setattr(bodies, "_get_vectors_snapshot", _missing)

    solar_meta, planets = await bodies._build_horizons_solar_system_bodies(
        epoch=epoch,
        past_hours=6,
        future_hours=6,
        step_minutes=60,
        observer_location=None,
        force_refresh=False,
        allow_network_fetch=True,
        logger=_DummyLogger(),
    )

    assert solar_meta["cache"]["offline_count"] == 1
    assert solar_meta["cache"]["missing_count"] == 0
    assert planets[0]["target_key"] == "body:saturn"
    assert planets[0]["source"] == "offline-analytic-kepler"
    assert planets[0]["approximate"] is True
    assert len(planets[0]["position_xyz_au"]) == 3


def test_horizons_circuit_skips_requests_during_backoff(monkeypatch):
    calls = []

    def _timeout(*_args, **kwargs):
        calls.append(kwargs)
        raise horizons.requests.exceptions.ConnectTimeout("blocked")

    monkeypatch.setattr(horizons.requests, "get", _timeout)

    with pytest.raises(horizons.HorizonsUnavailableError) as first_error:
        horizons._request_horizons_json(params={}, timeout_seconds=10.0, force_probe=False)
    with pytest.raises(horizons.HorizonsUnavailableError):
        horizons._request_horizons_json(params={}, timeout_seconds=10.0, force_probe=False)

    assert first_error.value.reason == "connect_timeout"
    assert len(calls) == 1
    assert calls[0]["timeout"] == (2.0, 10.0)
    status = horizons.get_horizons_status()
    assert status["circuit"] == "open"
    assert status["retry_at_utc"]


def test_manual_horizons_probe_closes_open_circuit(monkeypatch):
    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"result": "ok"}

    responses = [horizons.requests.exceptions.ConnectTimeout("blocked"), _Response()]

    def _request(*_args, **_kwargs):
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(horizons.requests, "get", _request)

    with pytest.raises(horizons.HorizonsUnavailableError):
        horizons._request_horizons_json(params={}, timeout_seconds=10.0, force_probe=False)
    payload, status_code = horizons._request_horizons_json(
        params={}, timeout_seconds=10.0, force_probe=True
    )

    assert payload == {"result": "ok"}
    assert status_code == 200
    assert horizons.get_horizons_status()["availability"] == "available"


@pytest.mark.asyncio
async def test_vector_snapshot_stats_report_cache_health(db_session):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    db_session.add(
        CelestialTargets(
            id="mission:voyager-1",
            target_type="mission",
            display_name="Voyager 1",
            horizons_command="Voyager 1",
            always_in_scene=True,
        )
    )
    common = {
        "target_id": "mission:voyager-1",
        "epoch_bucket_utc": now,
        "past_hours": 1,
        "future_hours": 24,
        "step_minutes": 60,
        "frame": "heliocentric-ecliptic",
        "center": "sun",
        "position_xyz_au": [1, 2, 3],
        "velocity_xyz_au_per_day": [0, 0, 0],
        "orbit_samples_xyz_au": [],
        "orbit_sample_times_utc": [],
        "source": "horizons",
        "fetched_at": now - timedelta(minutes=5),
    }
    db_session.add_all(
        [
            CelestialVectorSnapshots(
                id="fresh",
                expires_at=now + timedelta(hours=1),
                **common,
            ),
            CelestialVectorSnapshots(
                id="expired",
                epoch_bucket_utc=now - timedelta(hours=1),
                expires_at=now - timedelta(minutes=1),
                error="old response",
                **{key: value for key, value in common.items() if key != "epoch_bucket_utc"},
            ),
        ]
    )
    await db_session.commit()

    result = await fetch_celestial_vector_snapshot_stats(db_session, as_of=now)

    assert result["success"] is True
    assert result["data"]["total_snapshots"] == 2
    assert result["data"]["distinct_targets"] == 1
    assert result["data"]["fresh_snapshots"] == 1
    assert result["data"]["expired_snapshots"] == 1
    assert result["data"]["error_snapshots"] == 1
    assert result["data"]["operational_targets"] == 1
    assert result["data"]["ready_targets"] == 0
    assert result["data"]["refresh_due_targets"] == 1
    assert result["data"]["targets"][0]["status"] == "refresh_due"


@pytest.mark.asyncio
async def test_cache_refresh_reports_per_target_progress(monkeypatch):
    class _SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    async def fetch_monitored(*_args, **_kwargs):
        return {"success": True, "data": []}

    async def fetch_settings(*_args, **_kwargs):
        return {"success": True, "data": {"value": {}}}

    async def noop(*_args, **_kwargs):
        return None

    async def snapshot(*_args, **kwargs):
        if kwargs["target_key"] == "body:mars":
            return {
                "payload": None,
                "error": "NASA JPL Horizons could not be reached",
                "error_code": "connection_failure",
            }
        return {"payload": {"position_xyz_au": [1, 0, 0]}, "error": None}

    targets = [
        {"target_key": "body:earth", "name": "Earth", "horizons_command": "399"},
        {"target_key": "body:mars", "name": "Mars", "horizons_command": "499"},
    ]
    progress = []

    async def collect_progress(event):
        progress.append(event)

    async def finish_without_database(result):
        return result

    monkeypatch.setattr(sync, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(sync.crud_monitored, "fetch_monitored_celestial", fetch_monitored)
    monkeypatch.setattr(sync.crud_preferences, "get_map_settings", fetch_settings)
    monkeypatch.setattr(sync, "_build_builtin_body_targets", lambda: targets)
    monkeypatch.setattr(sync, "_ensure_scene_targets_registered", noop)
    monkeypatch.setattr(sync, "_get_vectors_snapshot", snapshot)
    monkeypatch.setattr(sync, "finish_celestial_sync", finish_without_database)

    result = await sync.refresh_celestial_vector_snapshots_cache(
        _DummyLogger(),
        progress_callback=collect_progress,
    )

    assert result["refreshed"] == 1
    assert result["provider_fetched"] == 1
    assert result["cache_reused"] == 0
    assert result["stale_fallback"] == 0
    assert result["failed"] == 1
    assert result["errors"] == [
        {
            "target_key": "body:mars",
            "target_name": "Mars",
            "error_code": "connection_failure",
            "error": "NASA JPL Horizons could not be reached",
        }
    ]
    assert [event["phase"] for event in progress] == [
        "starting",
        "processing",
        "processed",
        "processing",
        "processed",
    ]
    assert progress[-1]["processed"] == 2
    assert progress[-1]["percent"] == 100.0
    assert progress[-1]["failed"] == 1


@pytest.mark.asyncio
async def test_cache_refresh_uses_each_monitored_target_projection(monkeypatch):
    class _SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    async def fetch_monitored(*_args, **_kwargs):
        return {
            "success": True,
            "data": [
                {
                    "target_type": "mission",
                    "target_key": "mission:voyager_1",
                    "command": "Voyager 1",
                    "display_name": "Voyager 1",
                    "projection_past_hours": 12,
                    "projection_future_hours": 168,
                    "projection_step_minutes": 15,
                }
            ],
        }

    async def fetch_settings(*_args, **_kwargs):
        return {
            "success": True,
            "data": {"value": {"pastHours": 1, "futureHours": 24, "stepMinutes": 60}},
        }

    async def noop(*_args, **_kwargs):
        return None

    requested_projections = []

    async def snapshot(*_args, **kwargs):
        requested_projections.append(
            (
                kwargs["target_key"],
                kwargs["past_hours"],
                kwargs["future_hours"],
                kwargs["step_minutes"],
            )
        )
        return {"payload": {"position_xyz_au": [1, 0, 0]}, "error": None}

    async def finish_without_database(result):
        return result

    monkeypatch.setattr(sync, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(sync.crud_monitored, "fetch_monitored_celestial", fetch_monitored)
    monkeypatch.setattr(sync.crud_preferences, "get_map_settings", fetch_settings)
    monkeypatch.setattr(sync, "_build_builtin_body_targets", lambda: [])
    monkeypatch.setattr(sync, "_ensure_scene_targets_registered", noop)
    monkeypatch.setattr(sync, "_get_vectors_snapshot", snapshot)
    monkeypatch.setattr(sync, "finish_celestial_sync", finish_without_database)

    result = await sync.refresh_celestial_vector_snapshots_cache(_DummyLogger())

    assert result["success"] is True
    assert requested_projections == [
        ("mission:voyager_1", 12, 168, 15),
        ("body:earth", 12, 168, 15),
    ]
    assert result["earth_projection_count"] == 1


@pytest.mark.asyncio
async def test_solar_body_progress_is_scoped_to_requesting_client(monkeypatch):
    emitted = []

    class _Sio:
        async def emit(self, event, payload, to=None):
            emitted.append((event, payload, to))

    async def build_scene(*, per_body_callback=None, **_kwargs):
        await per_body_callback(
            {"target_key": "body:mars", "name": "Mars"},
            1,
            2,
        )
        return {"success": True, "data": {"planets": []}}

    monkeypatch.setattr(celestial_handlers, "build_solar_system_scene", build_scene)

    result = await celestial_handlers.get_solar_system_scene(
        _Sio(),
        {"allow_network_fetch": True, "request_id": "batch-1"},
        _DummyLogger(),
        "requesting-client",
    )

    assert result["success"] is True
    assert emitted == [
        (
            "solar-system-body-update",
            {
                "request_id": "batch-1",
                "body": {"target_key": "body:mars", "name": "Mars"},
                "progress": {"current": 1, "total": 2, "percent": 50.0},
            },
            "requesting-client",
        )
    ]


@pytest.mark.asyncio
async def test_cache_refresh_progress_is_sent_only_to_requesting_client(monkeypatch):
    emitted = []

    class _Sio:
        async def emit(self, event, payload, to=None):
            emitted.append((event, payload, to))

    async def refresh(_logger, *, progress_callback=None, trigger="manual"):
        assert trigger == "manual"
        await progress_callback({"processed": 1, "total": 2, "percent": 50.0})
        return {"success": True, "count": 2, "refreshed": 2, "failed": 0}

    monkeypatch.setattr(
        celestial_handlers,
        "refresh_celestial_vector_snapshots_cache",
        refresh,
    )

    async def emit_status(sio, _logger):
        payload = {"success": True, "data": {"sync": {"state": {"status": "complete"}}}}
        await sio.emit("celestial-ephemeris-status-update", payload)

    monkeypatch.setattr(celestial_handlers, "emit_celestial_ephemeris_status", emit_status)

    async def build_empty_payload(_data, _logger):
        return {"celestial": []}

    monkeypatch.setattr(
        celestial_handlers,
        "_build_scene_payload",
        build_empty_payload,
    )

    result = await celestial_handlers.refresh_celestial_cache_now(
        sio=_Sio(),
        data=None,
        logger=_DummyLogger(),
        sid="requesting-client",
    )

    assert result["success"] is True
    assert emitted == [
        (
            "celestial-cache-refresh-progress",
            {"processed": 1, "total": 2, "percent": 50.0},
            "requesting-client",
        ),
        (
            "celestial-ephemeris-status-update",
            {"success": True, "data": {"sync": {"state": {"status": "complete"}}}},
            None,
        ),
        (
            "celestial-tracks-update",
            {
                "timestamp_utc": emitted[2][1]["timestamp_utc"],
                "frame": "heliocentric-ecliptic",
                "center": "sun",
                "units": {"position": "au", "velocity": "au/day"},
                "celestial": [],
                "celestial_passes": [],
                "observer_bodies": [],
            },
            None,
        ),
    ]


@pytest.mark.asyncio
async def test_successful_cache_refresh_broadcasts_rebuilt_cached_tracks(monkeypatch):
    emitted = []
    build_calls = []

    class _Sio:
        async def emit(self, event, payload, to=None):
            emitted.append((event, payload, to))

    async def refresh(_logger, *, progress_callback=None, trigger="manual"):
        assert trigger == "manual"
        return {"success": True, "count": 1, "refreshed": 1, "failed": 0}

    async def build_payload(_data, _logger):
        return {
            "celestial": [
                {
                    "target_type": "mission",
                    "command": "Voyager 1",
                    "name": "Voyager 1",
                }
            ]
        }

    async def build_tracks(**kwargs):
        build_calls.append(kwargs)
        return {
            "success": True,
            "data": {
                "celestial": [
                    {
                        "name": "Voyager 1",
                        "sky_position": {"az_deg": 120.0, "el_deg": 30.0},
                    }
                ]
            },
        }

    monkeypatch.setattr(
        celestial_handlers,
        "refresh_celestial_vector_snapshots_cache",
        refresh,
    )

    async def emit_status(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        celestial_handlers,
        "emit_celestial_ephemeris_status",
        emit_status,
    )
    monkeypatch.setattr(celestial_handlers, "_build_scene_payload", build_payload)
    monkeypatch.setattr(celestial_handlers, "build_celestial_tracks", build_tracks)

    result = await celestial_handlers.refresh_celestial_cache_now(
        sio=_Sio(),
        data=None,
        logger=_DummyLogger(),
        sid="requesting-client",
    )

    assert result["success"] is True
    assert len(build_calls) == 1
    assert build_calls[0]["force_refresh"] is False
    assert build_calls[0]["allow_network_fetch"] is False
    assert build_calls[0]["register_targets"] is False
    assert build_calls[0]["use_computed_cache"] is False
    assert emitted == [
        (
            "celestial-tracks-update",
            {
                "celestial": [
                    {
                        "name": "Voyager 1",
                        "sky_position": {"az_deg": 120.0, "el_deg": 30.0},
                    }
                ]
            },
            None,
        )
    ]


@pytest.mark.asyncio
async def test_failed_cache_refresh_does_not_broadcast_tracks(monkeypatch):
    emitted = []

    class _Sio:
        async def emit(self, event, payload, to=None):
            emitted.append((event, payload, to))

    async def refresh(_logger, *, progress_callback=None, trigger="manual"):
        assert trigger == "manual"
        return {
            "success": False,
            "count": 1,
            "refreshed": 0,
            "failed": 1,
            "error": "Horizons unavailable",
        }

    async def unexpected_payload(*_args, **_kwargs):
        raise AssertionError("Tracks must not be rebuilt after a failed refresh")

    monkeypatch.setattr(
        celestial_handlers,
        "refresh_celestial_vector_snapshots_cache",
        refresh,
    )
    monkeypatch.setattr(
        celestial_handlers,
        "_build_scene_payload",
        unexpected_payload,
    )
    monkeypatch.setattr(
        celestial_handlers,
        "get_horizons_status",
        lambda: {
            "availability": "unavailable",
            "circuit": "open",
            "reason": "connection_failure",
            "retry_at_utc": "2026-09-22T08:45:00+00:00",
        },
    )

    async def emit_status(sio, _logger):
        payload = {"success": True, "data": {"sync": {"state": {"success": False}}}}
        await sio.emit("celestial-ephemeris-status-update", payload)

    monkeypatch.setattr(celestial_handlers, "emit_celestial_ephemeris_status", emit_status)

    result = await celestial_handlers.refresh_celestial_cache_now(
        sio=_Sio(),
        data=None,
        logger=_DummyLogger(),
        sid="requesting-client",
    )

    assert result["success"] is False
    assert result["data"]["provider_status"] == {
        "availability": "unavailable",
        "circuit": "open",
        "reason": "connection_failure",
        "retry_at_utc": "2026-09-22T08:45:00+00:00",
    }
    assert emitted == [
        (
            "celestial-ephemeris-status-update",
            {"success": True, "data": {"sync": {"state": {"success": False}}}},
            None,
        )
    ]


@pytest.mark.asyncio
async def test_delete_monitored_target_broadcasts_current_cached_tracks(monkeypatch):
    broadcasts = []

    class _SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    async def delete_target(_session, ids):
        return {
            "success": True,
            "data": {"deleted": len(ids), "ids": ids},
            "error": None,
        }

    async def broadcast(sio, logger, projection=None):
        broadcasts.append((sio, logger, projection))

    sio = object()
    logger = _DummyLogger()
    monkeypatch.setattr(celestial_handlers, "AsyncSessionLocal", _SessionContext)
    monkeypatch.setattr(
        celestial_handlers.crud_monitored,
        "delete_monitored_celestial",
        delete_target,
    )
    monkeypatch.setattr(
        celestial_handlers,
        "_broadcast_current_cached_celestial_tracks",
        broadcast,
    )

    result = await celestial_handlers.delete_monitored_celestial(
        sio=sio,
        data={"ids": ["target-1"]},
        logger=logger,
        sid="requesting-client",
    )

    assert result["success"] is True
    assert broadcasts == [(sio, logger, None)]
