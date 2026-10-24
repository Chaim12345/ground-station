"""CelesTrak synchronization safety regression coverage."""

import copy
import logging
import uuid

import pytest
import requests

from db.models import OrbitalSources, OrbitalSourceSyncState, Satellites
from tlesync import logic
from tlesync.celestrak import CelestrakRequestError


class _Response:
    """Minimal response object for the exact-200 CelesTrak request helper."""

    def __init__(self, status_code: int):
        self.status_code = status_code
        self.text = "CelesTrak request rejected for regression test"


class _JsonResponse:
    """Minimal successful JSON response for supplemental source requests."""

    def __init__(self, payload: str):
        self.status_code = 200
        self.text = payload


def _celestrak_source(name: str, url: str, priority: int) -> OrbitalSources:
    return OrbitalSources(
        id=uuid.uuid4(),
        name=name,
        identifier=f"{name.lower().replace(' ', '-')}-{priority}",
        url=url,
        format="omm",
        provider="generic_http",
        adapter="http_omm",
        enabled=True,
        priority=priority,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [301, 403, 404, 500, 503])
async def test_non_200_celestrak_response_suspends_source_and_stops_later_requests(
    db_session, monkeypatch, status_code
):
    """The sync-run circuit breaker must allow only its first CelesTrak request."""
    first_url = "https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=CSV"
    later_url = "https://celestrak.org/NORAD/elements/gp.php?GROUP=weather&FORMAT=CSV"
    first_source = _celestrak_source("First CelesTrak source", first_url, priority=1)
    later_source = _celestrak_source("Later CelesTrak source", later_url, priority=2)
    db_session.add_all([first_source, later_source])
    await db_session.commit()

    requests_seen = []

    def fake_get(url, **kwargs):
        requests_seen.append((url, kwargs))
        return _Response(status_code)

    emitted_states = []

    async def emit_callback(state):
        emitted_states.append(copy.deepcopy(state))

    monkeypatch.setattr(requests, "get", fake_get)

    result = await logic.synchronize_satellite_data_internal(
        db_session, logging.getLogger(__name__), emit_callback
    )

    assert result is False
    assert [url for url, _kwargs in requests_seen] == [first_url]
    assert requests_seen[0][1]["allow_redirects"] is False

    state = await db_session.get(OrbitalSourceSyncState, first_source.id)
    assert state is not None
    assert state.last_http_status == status_code
    assert state.suspended_at is not None
    assert state.last_error is not None
    assert f"HTTP {status_code}" in state.last_error

    suspended_source = await db_session.get(OrbitalSources, first_source.id)
    assert suspended_source.enabled is False
    assert await db_session.get(OrbitalSourceSyncState, later_source.id) is None
    assert emitted_states[-1]["success"] is False
    assert "no orbit data" in emitted_states[-1]["message"].lower()


@pytest.mark.asyncio
async def test_celestrak_failure_skips_later_celestrak_sources_but_processes_generic_sources(
    db_session, monkeypatch
):
    """A CelesTrak circuit breaker must not block independent providers."""
    first_url = "https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=CSV"
    generic_url = "https://example.test/orbits"
    later_url = "https://celestrak.org/NORAD/elements/gp.php?GROUP=weather&FORMAT=CSV"
    first_source = _celestrak_source("First CelesTrak source", first_url, priority=1)
    generic_source = OrbitalSources(
        id=uuid.uuid4(),
        name="Independent source",
        identifier="independent-source",
        url=generic_url,
        format="3le",
        provider="generic_http",
        adapter="http_3le",
        enabled=True,
        priority=2,
    )
    later_source = _celestrak_source("Later CelesTrak source", later_url, priority=3)
    db_session.add_all([first_source, generic_source, later_source])
    await db_session.commit()

    attempted_urls = []

    async def fetch_orbits(source, _pool):
        attempted_urls.append(source["url"])
        if source["url"] == first_url:
            raise CelestrakRequestError(first_url, 503, "Service Unavailable")
        if source["url"] == generic_url:
            return [
                {
                    "norad_id": 44001,
                    "name": "Independent satellite",
                    "line1": "1 44001U 00000A   26001.00000000  .00000000  00000-0  00000-0 0  9990",
                    "line2": "2 44001  51.0000 000.0000 0000000   0.0000   0.0000 15.00000000000000",
                    "model_kind": "tle",
                    "source_id": str(generic_source.id),
                }
            ]
        raise AssertionError("a later CelesTrak source must not be requested")

    async def fetch_metadata(_url, _pool):
        return _JsonResponse("[]")

    async def no_removed_items(**_kwargs):
        return {"satellites": [], "transmitters": []}

    async def no_transmitter_import(**_kwargs):
        return {"success": True}

    async def no_tinygs_transmitters(_pool):
        return []

    monkeypatch.setattr(logic, "async_fetch_source_orbit_records", fetch_orbits)
    monkeypatch.setattr(logic, "async_fetch", fetch_metadata)
    monkeypatch.setattr(logic, "update_satellite_group_with_removal_detection", no_removed_items)
    monkeypatch.setattr(logic, "import_gr_satellites_transmitters", no_transmitter_import)
    monkeypatch.setattr(logic, "import_satdump_transmitters", no_transmitter_import)
    monkeypatch.setattr(logic, "fetch_tinygs_transmitters", no_tinygs_transmitters)
    monkeypatch.setattr(logic, "get_all_tracker_managers", lambda: {})

    emitted_states = []
    result = await logic.synchronize_satellite_data_internal(
        db_session,
        logging.getLogger(__name__),
        lambda state: emitted_states.append(copy.deepcopy(state)),
    )

    assert attempted_urls == [first_url, generic_url]
    assert result is False
    assert await db_session.get(Satellites, 44001) is not None

    first_state = await db_session.get(OrbitalSourceSyncState, first_source.id)
    assert first_state is not None
    assert first_state.last_http_status == 503
    assert first_state.suspended_at is not None
    assert (await db_session.get(OrbitalSources, first_source.id)).enabled is False
    assert await db_session.get(OrbitalSourceSyncState, later_source.id) is None
    assert "completed with errors" in emitted_states[-1]["message"].lower()
