"""WREN-1 On-board Whereabouts Locator (OWL) LoRa beacon (NORAD 60478).

137.925 MHz LoRa SF10 / BW125k / CR4:5, 88-byte packets. Layout from the
public emitters.space note on WREN-1 OWL, cross-checked against frames this
station decoded (MET advances 29-31 s per beacon):

  bytes 0-3   fixed 03 00 00 01   OWL id / packet type
  bytes 4-6   uint24 LE           mission elapsed time, seconds
  byte  7     0x81                packet version
  bytes 8-87  80 bytes            position payload, encrypted by the operator

Only the plaintext header is decoded; the 80-byte body is reported as-is.
"""

from __future__ import annotations

from typing import Any, Dict

WREN1_NORAD = 60478
OWL_PACKET_LENGTH = 88
OWL_HEADER = bytes.fromhex("03000001")


def is_owl_beacon(payload: bytes) -> bool:
    return len(payload) == OWL_PACKET_LENGTH and payload[:4] == OWL_HEADER


def parse_owl_beacon(payload: bytes) -> Dict[str, Any]:
    if not is_owl_beacon(payload):
        raise ValueError("not a WREN-1 OWL beacon (expected 88 bytes starting 03000001)")
    met = int.from_bytes(payload[4:7], "little")
    days, rem = divmod(met, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    return {
        "format": "wren1_owl",
        "packet_type": payload[:4].hex(),
        "mission_elapsed_time_s": met,
        "mission_elapsed_time": f"{days}d {hours:02d}:{minutes:02d}:{seconds:02d}",
        "packet_version": payload[7],
        "encrypted_payload_hex": payload[8:].hex(),
        "encrypted_payload_length": len(payload) - 8,
    }
