"""AO-40 FEC (FUNcube) framing is mapped, deframed and parsed end to end."""

import pytest

from constants import FramingType, payload_protocol_from_framing
from constants.framing import GR_SATELLITES_FRAMING_MAP

pytestmark = pytest.mark.unit


def test_gr_satellites_ao40_maps_to_ao40_not_ax25():
    assert GR_SATELLITES_FRAMING_MAP["AO-40 FEC"] == FramingType.AO40_FEC
    assert GR_SATELLITES_FRAMING_MAP["AO-40 FEC short"] == FramingType.AO40_FEC_SHORT
    assert payload_protocol_from_framing(FramingType.AO40_FEC) == "proprietary"


def test_satconfig_sets_differential_for_dbpsk():
    from satconfig.config import SatelliteConfigService

    svc = SatelliteConfigService()
    if not svc.satyaml:
        pytest.skip("gr-satellites SatYAML not available")
    params = svc.get_decoder_parameters(norad_id=39444, baudrate=1200, frequency=145.935e6)
    assert params["framing"] == FramingType.AO40_FEC
    assert params["differential"] is True


def test_bpsk_deframer_factory_builds_ao40():
    gr = pytest.importorskip("gnuradio.gr")  # noqa: F841
    import argparse

    from demodulators.deframerfactory import create_bpsk_deframer

    deframer, info = create_bpsk_deframer(
        FramingType.AO40_FEC, argparse.Namespace(syncword_threshold=8)
    )
    assert info == "AO-40 FEC"
    assert deframer is not None


def test_telemetry_parser_routes_ao40_frames():
    from telemetry.parser import TelemetryParser
    from telemetry.parsers.funcube import FUNCUBE_AVAILABLE

    # Sat id FC2 (0b01), frame type 1 (0b000001). gr-satellites' enum starts at
    # WO1=0, so value 1 is "WO2" -> whole-orbit chunk 2. Header byte 0x41.
    frame = bytes([0x41]) + bytes(255)
    result = TelemetryParser().parse(
        frame, protocol_hint="proprietary", parser_hint={"framing": FramingType.AO40_FEC}
    )
    assert result["success"]
    assert result["protocol"] == "ao40"
    if FUNCUBE_AVAILABLE:
        assert result["parser"] == "ao40+FuncubeParser"
        assert result["telemetry"]["satellite_id"] == "FC2"
        assert result["telemetry"]["payload_type"] == "whole_orbit"
        assert result["telemetry"]["frame_type"] == "WO2"
        assert result["telemetry"]["whole_orbit_chunk"] == 2
