"""Persisted transmitter modem settings must reach decoder recommendations."""

import pytest

from crud.satellites import add_satellite
from crud.transmitters import add_transmitter, edit_transmitter, fetch_transmitter
from db.models import Transmitters
from pipeline.config.decoderconfigservice import DecoderConfigService


@pytest.mark.asyncio
async def test_transmitter_settings_override_satellite_defaults(db_session, monkeypatch):
    satellite = await add_satellite(
        db_session,
        {
            "name": "Test FSK satellite",
            "sat_id": "FSK-TEST",
            "norad_id": 25544,
            "status": "alive",
            "is_frequency_violator": False,
            "tle1": "1 25544U 00000A   21001.00000000  .00000000  00000-0  00000-0 0  9990",
            "tle2": "2 25544  51.0000 000.0000 0000000   0.0000   0.0000 15.00000000000000",
        },
    )
    assert satellite["success"]

    created = await add_transmitter(
        db_session,
        {
            "satelliteId": 25544,
            "description": "200 baud FSK",
            "type": "Telemetry",
            "status": "active",
            "downlinkLow": 436888000,
            "mode": "FSK",
            "baud": 200,
            "deviation": "562.5",
            "framing": "GEOSCAN",
        },
    )
    assert created["success"], created["error"]
    transmitter = created["data"]
    assert transmitter["deviation"] == 562.5
    assert transmitter["framing"] == "geoscan"

    service = DecoderConfigService()
    monkeypatch.setattr(
        service.satconfig_service,
        "get_decoder_parameters",
        lambda **_: {"source": "satellite_config", "deviation": 100, "framing": "ax25"},
    )
    satellite_info = {"norad_id": 25544, "name": "Test FSK satellite"}
    suggested = service.get_config("fsk", satellite=satellite_info, transmitter=transmitter)
    assert suggested.baudrate == 200
    assert suggested.deviation == 562.5
    assert suggested.framing == "geoscan"

    manual = service.get_config(
        "fsk",
        satellite=satellite_info,
        transmitter=transmitter,
        overrides={"deviation": 600, "framing": "ax25"},
    )
    assert manual.deviation == 600
    assert manual.framing == "ax25"

    edited = await edit_transmitter(
        db_session, {"id": transmitter["id"], "deviation": -562.5, "framing": "ax25"}
    )
    assert edited["success"], edited["error"]
    fetched = await fetch_transmitter(db_session, transmitter["id"])
    assert fetched["data"]["deviation"] == -562.5
    assert fetched["data"]["framing"] == "ax25"

    # Satellite sync merges source-owned fields without these optional settings.
    await db_session.merge(
        Transmitters(id=transmitter["id"], status="active", description="Refreshed source")
    )
    await db_session.commit()
    refreshed = await fetch_transmitter(db_session, transmitter["id"])
    assert refreshed["data"]["deviation"] == -562.5
    assert refreshed["data"]["framing"] == "ax25"


@pytest.mark.asyncio
async def test_transmitter_rejects_unsupported_framing(db_session):
    result = await add_transmitter(
        db_session,
        {
            "target_key": "mission:test",
            "description": "Unsupported packet type",
            "type": "Telemetry",
            "status": "active",
            "downlinkLow": 436888000,
            "mode": "FSK",
            "baud": 200,
            "deviation": 562.5,
            "framing": "unne1b",
        },
    )
    assert not result["success"]
    assert "unsupported FSK framing" in result["error"]
