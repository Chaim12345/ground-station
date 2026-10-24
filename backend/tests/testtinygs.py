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
from pipeline.config.decoderconfigservice import DecoderConfigService
from tlesync import logic, tinygs


def _tinygs_satellite(norad=44001):
    return {
        "name": "TestSat",
        "displayName": "Test Satellite",
        "configurations": [
            {
                "NORAD": norad,
                "mode": "LoRa",
                "freq": 436.7013,
                "bw": 250,
                "sf": 10,
                "cr": 5,
                "sw": 18,
                "pl": 8,
                "crc": True,
                "fldro": 1,
                "iq": True,
            },
            {
                "NORAD": norad,
                "mode": "LoRa",
                "freq": 436.7013,
                "bw": 125,
                "sf": 9,
                "cr": 8,
                "sw": 52,
                "pl": 9,
                "crc": True,
                "fldro": 0,
            },
        ],
    }


def test_tinygs_profiles_map_to_stable_distinct_source_rows():
    satellites = [_tinygs_satellite(), _tinygs_satellite(99999), _tinygs_satellite(100956)]

    rows = tinygs.parse_tinygs_transmitters(satellites)

    assert len(rows) == 4  # Two profiles for each cataloged satellite.
    assert rows[0]["source"] == "tinygs"
    assert rows[0]["norad_cat_id"] == 44001
    assert rows[0]["downlink_low"] == 436701300
    assert rows[0]["mode"] == "LoRa"
    assert {row["uuid"] for row in rows[:2]} == {
        row["uuid"]
        for row in tinygs.parse_tinygs_transmitters(
            [
                {
                    **_tinygs_satellite(),
                    "configurations": list(reversed(_tinygs_satellite()["configurations"])),
                }
            ]
        )
    }
    assert {row["lora_config"]["sf"] for row in rows[:2]} == {9, 10}
    assert {row["lora_config"]["bw"] for row in rows[:2]} == {125000, 250000}
    assert {row["lora_config"]["cr"] for row in rows[:2]} == {1, 4}
    assert {tuple(row["lora_config"]["sync_word"]) for row in rows[:2]} == {(18,), (52,)}
    assert {row["lora_config"]["preamble_len"] for row in rows[:2]} == {8, 9}
    assert {row["lora_config"]["ldro_mode"] for row in rows[:2]} == {1, 2}
    assert all(row["lora_config"]["has_crc"] for row in rows[:2])
    assert (
        next(row for row in rows[:2] if row["lora_config"]["sf"] == 10)["lora_config"]["invert_iq"]
        is True
    )
    assert rows[0]["alive"] is True
    assert rows[0]["status"] == "active"
    assert rows[2]["norad_cat_id"] == 100956
    assert rows[0]["uuid"] == tinygs.parse_tinygs_transmitters([_tinygs_satellite()])[0]["uuid"]
    isolated = _tinygs_satellite()
    isolated["configurations"] = [isolated["configurations"][1]]
    assert next(row for row in rows[:2] if row["lora_config"]["sf"] == 9)["uuid"] == (
        tinygs.parse_tinygs_transmitters([isolated])[0]["uuid"]
    )
    assert len(rows[0]["uuid"]) == 22


def test_identical_tinygs_receiver_profiles_do_not_create_duplicate_rows():
    satellite = _tinygs_satellite()
    satellite["configurations"].append(copy.deepcopy(satellite["configurations"][0]))

    rows = tinygs.parse_tinygs_transmitters([satellite])

    assert len(rows) == 2
    assert len({row["uuid"] for row in rows}) == 2


def test_tle_identity_corrects_reused_tianqi_receiver_norad():
    tianqi_18 = _tinygs_satellite(62367)
    tianqi_18["displayName"] = "Tianqi-18"
    tianqi_18["tle"] = [
        "TIANQI 18",
        "1 64060U 25105C   26296.92363436  .00000062  00000-0  13037-3 0  9995",
        "2 64060  44.9834 169.4577 0010269 282.1871  77.7811 13.99172842 73227",
    ]
    tianqi_34 = _tinygs_satellite(62367)
    tianqi_34["displayName"] = "Tianqi-34"

    rows = tinygs.parse_tinygs_transmitters([tianqi_18, tianqi_34])

    assert len(rows) == 4
    assert {row["norad_cat_id"] for row in rows if "Tianqi-18" in row["description"]} == {64060}
    assert {row["norad_cat_id"] for row in rows if "Tianqi-34" in row["description"]} == {62367}
    assert len({row["uuid"] for row in rows}) == 4


def test_mismatched_tle_lines_fall_back_to_configuration_norad():
    satellite = _tinygs_satellite(44001)
    satellite["tle"] = ["1 64060U", "2 62367 "]

    rows = tinygs.parse_tinygs_transmitters([satellite])

    assert {row["norad_cat_id"] for row in rows} == {44001}


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

    assert len(rows) == 2
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
        tinygs_rows = [tx for tx in transmitters if tx.source == "tinygs"]
        assert len(tinygs_rows) == 2
        tinygs_row = next(tx for tx in tinygs_rows if tx.lora_config["sf"] == 10)
        assert tinygs_row.norad_cat_id == 44001
        assert tinygs_row.downlink_low == 436701300
        assert tinygs_row.alive is True
        assert tinygs_row.status == "active"
        assert tinygs_row.lora_config["bw"] == 250000
        config = DecoderConfigService().get_config(
            "lora",
            transmitter={
                "description": tinygs_row.description,
                "lora_config": tinygs_row.lora_config,
            },
        )
        assert (config.sf, config.bw, config.cr, config.ldro_mode) == (10, 250000, 1, 1)
        assert config.config_source == "transmitter_metadata"
        assert tinygs_row.source_transmitter_id
        assert emitted[-1]["stats"]["transmitters_processed"] == 3
        assert "TinyGS Transmitters" in emitted[-1]["completed_sources"]

        # A profile whose frequency disappeared must be removed after a complete fetch.
        stale_id = tinygs.uuid_to_short_transmitter_id(uuid.uuid4())
        db_session.add(
            Transmitters(
                id=stale_id,
                source="tinygs",
                description="Stale TinyGS profile",
                norad_cat_id=44001,
                downlink_low=436702000,
                mode="LoRa",
                status="active",
                alive=True,
            )
        )
        await db_session.commit()
        assert (
            await logic.synchronize_satellite_data_internal(
                db_session,
                logging.getLogger(__name__),
                lambda state: None,
            )
            is True
        )
        refreshed = (await db_session.execute(select(Transmitters))).scalars().all()
        assert stale_id not in {tx.id for tx in refreshed}

        async def unavailable_tinygs(_pool):
            raise requests.ConnectionError("TinyGS unavailable")

        monkeypatch.setattr(logic, "fetch_tinygs_transmitters", unavailable_tinygs)
        assert (
            await logic.synchronize_satellite_data_internal(
                db_session,
                logging.getLogger(__name__),
                lambda state: None,
            )
            is True
        )
        after_failed_fetch = (await db_session.execute(select(Transmitters))).scalars().all()
        assert {tx.id for tx in after_failed_fetch if tx.source == "tinygs"} == {
            tx.id for tx in refreshed if tx.source == "tinygs"
        }
