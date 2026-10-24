# Copyright (c) 2026 Efstratios Goudelis
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""TinyGS metadata adapter and orbital sync integration tests."""

import base64
import copy
import json
import logging
import uuid

import pytest
import requests
from sqlalchemy import select

from db.models import OrbitalSources, Transmitters
from tlesync import logic, tinygs


def _tinygs_satellite(norad=44001):
    return {
        "name": "TestSat",
        "displayName": "Test Satellite",
        "configurations": [
            {"NORAD": norad, "mode": "LoRa", "freq": 436.7013, "bw": 250, "sf": 10},
            {"NORAD": norad, "mode": "LoRa", "freq": 436.7013, "bw": 125, "sf": 9},
        ],
    }


def test_tinygs_profiles_map_to_stable_distinct_source_rows():
    satellites = [_tinygs_satellite(), _tinygs_satellite(99999), _tinygs_satellite(100956)]

    rows = tinygs.parse_tinygs_transmitters(satellites)

    assert len(rows) == 2  # Alternate receiver settings describe one downlink.
    assert rows[0]["source"] == "tinygs"
    assert rows[0]["norad_cat_id"] == 44001
    assert rows[0]["downlink_low"] == 436701300
    assert rows[0]["mode"] == "LoRa"
    assert rows[0]["alive"] is None
    assert rows[0]["status"] == "unknown"
    assert rows[1]["norad_cat_id"] == 100956
    assert rows[0]["uuid"] == tinygs.parse_tinygs_transmitters([_tinygs_satellite()])[0]["uuid"]
    assert len(rows[0]["uuid"]) == 22


def test_tinygs_fetch_sends_webapp_timestamp_header(monkeypatch):
    monkeypatch.setattr(tinygs.time, "time", lambda: 1700000000.0)
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [_tinygs_satellite()]

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(tinygs.requests, "get", fake_get)
    rows = tinygs._fetch_tinygs_transmitters()

    assert len(rows) == 1
    assert calls[0][0] == tinygs.TINYGS_SUPPORTED_URL
    encoded = base64.b64decode(calls[0][1]["headers"]["x-client-timestamp"])
    decoded = bytes(
        value ^ tinygs._TIMESTAMP_KEY[index % len(tinygs._TIMESTAMP_KEY)]
        for index, value in enumerate(encoded)
    )
    assert decoded == b"1700000000000"


@pytest.mark.asyncio
@pytest.mark.parametrize("tinygs_fails", [False, True])
async def test_orbital_sync_adds_tinygs_without_replacing_satnogs(
    db_session, monkeypatch, tinygs_fails
):
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
    db_session.add(source)
    await db_session.commit()

    async def fetch_sources(_session):
        return {
            "success": True,
            "data": [
                {
                    "id": str(source.id),
                    "name": source.name,
                    "identifier": source.identifier,
                    "url": source.url,
                    "provider": source.provider,
                    "adapter": source.adapter,
                    "enabled": source.enabled,
                    "priority": source.priority,
                }
            ],
        }

    async def fetch_orbits(_source, _pool):
        return [
            {
                "norad_id": 44001,
                "name": "Test Satellite",
                "line1": "1 44001U 00000A   26001.00000000  .00000000  00000-0  00000-0 0  9990",
                "line2": "2 44001  51.0000 000.0000 0000000   0.0000   0.0000 15.00000000000000",
                "model_kind": "tle",
                "source_id": str(source.id),
            }
        ]

    satnogs_uuid = str(uuid.uuid4())

    class Response:
        status_code = 200

        def __init__(self, payload):
            self.text = json.dumps(payload)

    async def fetch_metadata(url, _pool):
        if "transmitters" in url:
            return Response(
                [
                    {
                        "uuid": satnogs_uuid,
                        "norad_cat_id": 44001,
                        "description": "SatNOGS beacon",
                        "downlink_low": 437000000,
                        "mode": "FSK",
                        "type": "Transmitter",
                        "status": "active",
                        "alive": True,
                    }
                ]
            )
        return Response([])

    async def fetch_tinygs(_pool):
        if tinygs_fails:
            raise requests.ConnectionError("TinyGS unavailable")
        return tinygs.parse_tinygs_transmitters([_tinygs_satellite()])

    async def no_removed_items(**_kwargs):
        return {"satellites": [], "transmitters": []}

    async def no_transmitter_import(**_kwargs):
        return {"success": True}

    emitted = []
    monkeypatch.setattr(logic, "fetch_orbital_source", fetch_sources)
    monkeypatch.setattr(logic, "async_fetch_source_orbit_records", fetch_orbits)
    monkeypatch.setattr(logic, "async_fetch", fetch_metadata)
    monkeypatch.setattr(logic, "fetch_tinygs_transmitters", fetch_tinygs)
    monkeypatch.setattr(logic, "update_satellite_group_with_removal_detection", no_removed_items)
    monkeypatch.setattr(logic, "import_gr_satellites_transmitters", no_transmitter_import)
    monkeypatch.setattr(logic, "import_satdump_transmitters", no_transmitter_import)
    monkeypatch.setattr(logic, "get_all_tracker_managers", lambda: {})

    result = await logic.synchronize_satellite_data_internal(
        db_session,
        logging.getLogger(__name__),
        lambda state: emitted.append(copy.deepcopy(state)),
    )
    transmitters = (await db_session.execute(select(Transmitters))).scalars().all()

    assert result is True
    assert {transmitter.source for transmitter in transmitters} == (
        {"satnogs"} if tinygs_fails else {"satnogs", "tinygs"}
    )
    if tinygs_fails:
        assert any("TinyGS unavailable" in error for error in emitted[-1]["errors"])
    else:
        tinygs_row = next(tx for tx in transmitters if tx.source == "tinygs")
        assert tinygs_row.norad_cat_id == 44001
        assert tinygs_row.downlink_low == 436701300
        assert tinygs_row.source_transmitter_id
        assert emitted[-1]["stats"]["transmitters_processed"] == 2
        assert "TinyGS Transmitters" in emitted[-1]["completed_sources"]
