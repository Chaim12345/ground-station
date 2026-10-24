# Copyright (c) 2026 Efstratios Goudelis
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Unit tests for the orbital synchronization control flow."""

import copy
import logging
import uuid

import pytest
import requests

from db.models import OrbitalSources, OrbitalSourceSyncState, SatelliteOrbits, Satellites
from tlesync import logic


class _Response:
    def __init__(self, payload):
        self.status_code = 200
        self.text = payload


def _source_payload(source):
    return {
        "id": str(source.id),
        "name": source.name,
        "identifier": source.identifier,
        "url": source.url,
        "provider": source.provider,
        "adapter": source.adapter,
        "enabled": source.enabled,
        "priority": source.priority,
    }


@pytest.mark.asyncio
async def test_sync_reports_no_enabled_sources_without_fetching(db_session, monkeypatch):
    disabled = OrbitalSources(
        id=uuid.uuid4(),
        name="Disabled source",
        identifier="disabled-source",
        url="https://example.test/orbits",
        enabled=False,
    )
    db_session.add(disabled)
    await db_session.commit()

    emitted = []

    async def emit(state):
        emitted.append(copy.deepcopy(state))

    result = await logic.synchronize_satellite_data_internal(
        db_session, logging.getLogger(__name__), emit
    )

    assert result is False
    assert emitted[-1]["status"] == "complete"
    assert emitted[-1]["success"] is False
    assert "0 enabled" in emitted[-1]["message"]


@pytest.mark.asyncio
async def test_sync_persists_generic_fetch_failure_and_returns_failure(db_session, monkeypatch):
    source = OrbitalSources(
        id=uuid.uuid4(),
        name="Generic source",
        identifier="generic-source",
        url="https://example.test/orbits",
        format="omm",
        provider="generic_http",
        adapter="http_omm",
        enabled=True,
    )
    db_session.add(source)
    await db_session.commit()

    async def fetch_sources(_session):
        return {"success": True, "data": [_source_payload(source)]}

    async def fail_fetch(_source, _pool):
        raise requests.exceptions.RequestException("service unavailable")

    monkeypatch.setattr(logic, "fetch_orbital_source", fetch_sources)
    monkeypatch.setattr(logic, "async_fetch_source_orbit_records", fail_fetch)

    result = await logic.synchronize_satellite_data_internal(
        db_session, logging.getLogger(__name__), lambda _state: None
    )
    state = await db_session.get(OrbitalSourceSyncState, source.id)

    assert result is False
    assert state.last_http_status is None
    assert state.last_error == "service unavailable"
    assert state.suspended_at is None


@pytest.mark.asyncio
async def test_sync_imports_orbit_and_completes_with_metadata_unavailable(db_session, monkeypatch):
    source = OrbitalSources(
        id=uuid.uuid4(),
        name="Test source",
        identifier="test-source",
        url="https://example.test/orbits",
        format="3le",
        provider="generic_http",
        adapter="http_3le",
        enabled=True,
    )
    await db_session.merge(source)
    await db_session.commit()

    async def fetch_sources(_session):
        return {"success": True, "data": [_source_payload(source)]}

    async def fetch_orbits(_source, _pool):
        return [
            {
                "norad_id": 44001,
                "name": "Test satellite",
                "line1": "1 44001U 00000A   26001.00000000  .00000000  00000-0  00000-0 0  9990",
                "line2": "2 44001  51.0000 000.0000 0000000   0.0000   0.0000 15.00000000000000",
                "model_kind": "tle",
                "source_id": str(source.id),
            }
        ]

    async def fetch_metadata(_url, _pool):
        return _Response("[]")

    async def no_removed_items(**_kwargs):
        return {"satellites": [], "transmitters": []}

    async def no_transmitter_import(**_kwargs):
        return {"success": True}

    async def no_tinygs_transmitters(_pool):
        return []

    emitted = []
    monkeypatch.setattr(logic, "fetch_orbital_source", fetch_sources)
    monkeypatch.setattr(logic, "async_fetch_source_orbit_records", fetch_orbits)
    monkeypatch.setattr(logic, "async_fetch", fetch_metadata)
    monkeypatch.setattr(logic, "update_satellite_group_with_removal_detection", no_removed_items)
    monkeypatch.setattr(logic, "import_gr_satellites_transmitters", no_transmitter_import)
    monkeypatch.setattr(logic, "import_satdump_transmitters", no_transmitter_import)
    monkeypatch.setattr(logic, "fetch_tinygs_transmitters", no_tinygs_transmitters)
    monkeypatch.setattr(logic, "get_all_tracker_managers", lambda: {})

    result = await logic.synchronize_satellite_data_internal(
        db_session, logging.getLogger(__name__), lambda state: emitted.append(copy.deepcopy(state))
    )
    satellite = await db_session.get(Satellites, 44001)
    orbit = await db_session.get(SatelliteOrbits, (44001, "earth"))
    state = await db_session.get(OrbitalSourceSyncState, source.id)

    assert result is True
    assert satellite.name == "Test satellite"
    assert satellite.source == "tlesync"
    assert orbit.model_kind == "tle"
    assert orbit.source_id == source.id
    assert state.last_http_status == 200
    assert emitted[-1]["success"] is True
    assert emitted[-1]["stats"]["satellites_processed"] == 1
