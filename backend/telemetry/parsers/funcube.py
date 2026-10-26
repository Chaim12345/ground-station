#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FUNcube (AO-40 FEC) telemetry parser.

FUNcube-family satellites (AO-73/FUNcube-1, UKube-1/FUNcube-2, JY1-Sat,
Nayif-1, ...) send 256-byte frames after AO-40 FEC decoding:
  header (sat id + frame type) | 54-byte real-time telemetry | 200-byte payload
where the payload is Whole-Orbit data, High-Resolution data or Fitter messages.

Structure definitions come from gr-satellites (same GNU Radio install the
decoders use). If gr-satellites is unavailable the parser reports that and
the caller falls back to a raw hex view.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger("telemetry.parsers.funcube")

FUNCUBE_FRAME_SIZE = 256

try:
    from satellites.telemetry.funcube import Frame as _FuncubeFrame

    FUNCUBE_AVAILABLE = True
except Exception as e:  # gr-satellites missing or incompatible build
    _FuncubeFrame = None
    FUNCUBE_AVAILABLE = False
    logger.info("FUNcube telemetry structures unavailable: %s", e)


def _to_plain(value: Any) -> Any:
    """Convert construct containers/enums into JSON-serializable values."""
    if isinstance(value, dict):
        return {str(k): _to_plain(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, (list, tuple)):
        return [_to_plain(v) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


class FuncubeParser:
    """Parse one FUNcube 256-byte frame into structured telemetry."""

    def parse(self, payload: bytes) -> Dict[str, Any]:
        if len(payload) != FUNCUBE_FRAME_SIZE:
            raise ValueError(
                f"FUNcube frame must be {FUNCUBE_FRAME_SIZE} bytes, got {len(payload)}"
            )
        if _FuncubeFrame is None:
            raise RuntimeError("gr-satellites FUNcube structures not available")

        frame = _FuncubeFrame.parse(payload)
        header = frame.header
        frametype = str(header.frametype)
        result: Dict[str, Any] = {
            "format": "funcube",
            "satellite_id": str(header.satid),
            "frame_type": frametype,
            "realtime": _to_plain(frame.realtime),
        }
        if frame.extheader is not None:
            result["extended_header"] = int(frame.extheader)

        kind = frametype[:2]
        if kind == "FM":
            # Fitter messages are ASCII text broadcast to schools.
            text = bytes(frame.payload).rstrip(b"\x00").decode("ascii", errors="replace")
            result["payload_type"] = "fitter_message"
            result["fitter_message"] = text
        elif kind == "HR":
            result["payload_type"] = "high_resolution"
            result["payload"] = _to_plain(frame.payload)
        elif kind == "WO":
            # Whole-orbit data spans 12 consecutive frames; reassembly needs
            # session state, so expose the chunk index and raw bytes.
            result["payload_type"] = "whole_orbit"
            result["whole_orbit_chunk"] = int(frametype[2:])
            result["payload_hex"] = bytes(frame.payload).hex()
        else:
            result["payload_type"] = "unknown"
        return result
