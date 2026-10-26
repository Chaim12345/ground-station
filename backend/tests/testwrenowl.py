"""WREN-1 OWL LoRa beacon parser, checked against frames received live."""

import pytest

from telemetry.parser import TelemetryParser
from telemetry.parsers.wrenowl import is_owl_beacon, parse_owl_beacon

# Real 88-byte beacons decoded (CRC ok) from WREN-1 on 2026-10-26.
FRAME_1 = bytes.fromhex(
    "03000001ccfdb081782462629ec0b58d383fbc954900951bc0ff6314be418f020f320c67"
    "f7e1c171708682c710e2ee31ccd77daeaa20fcdd5a1c707e082ad0fa8ec2b1926e14aa21"
    "3386387dde168f10f30328a94f2b1227"
)
FRAME_2 = bytes.fromhex(
    "03000001ebfdb081fce8b8b4becb2bfc539752864b3b8bacc0ff6314be418f020f320c67"
    "f7e1c171708682c710e2ee31ccd77daeaa20fcdd5a1c707e082ad0fa8ec2b1926e14aa21"
    "ade499f198f5b69ae974bc5525699547"
)


def test_header_fields():
    t = parse_owl_beacon(FRAME_1)
    assert t["packet_type"] == "03000001"
    assert t["packet_version"] == 0x81
    assert t["mission_elapsed_time_s"] == 11_599_308
    assert t["mission_elapsed_time"] == "134d 06:01:48"
    assert t["encrypted_payload_length"] == 80


def test_consecutive_beacons_advance_by_beacon_interval():
    dt = parse_owl_beacon(FRAME_2)["mission_elapsed_time_s"] - parse_owl_beacon(FRAME_1)["mission_elapsed_time_s"]
    assert dt == 31


def test_rejects_other_packets():
    assert not is_owl_beacon(FRAME_1[:40])
    assert not is_owl_beacon(b"\x00" * 88)
    with pytest.raises(ValueError):
        parse_owl_beacon(b"\x00" * 88)


def test_router_uses_owl_parser_for_wren1_only():
    parser = TelemetryParser()
    wren = parser.parse(FRAME_1, protocol_hint="lora", sat_hint="60478")
    assert wren["parser"] == "lora+WrenOwlParser"
    assert wren["telemetry"]["mission_elapsed_time_s"] == 11_599_308
    other = parser.parse(FRAME_1, protocol_hint="lora", sat_hint="12345")
    assert other["parser"] == "lora"
