# Copyright (c) 2026 Efstratios Goudelis
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Fetch TinyGS radio configurations and adapt them for orbital sync."""

import asyncio
import base64
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import requests

from tlesync.utils import uuid_to_short_transmitter_id

TINYGS_SUPPORTED_URL = "https://api.tinygs.com/v3/satellites/?status=Supported"
TINYGS_SOURCE = "tinygs"
_TIMESTAMP_KEY = b"TinyGS-WebApp-2025-SecureKey"


def _client_timestamp() -> str:
    """Generate the timestamp header used by TinyGS's public web app."""
    timestamp = str(int(time.time() * 1000)).encode("ascii")
    encoded = bytes(
        value ^ _TIMESTAMP_KEY[index % len(_TIMESTAMP_KEY)] for index, value in enumerate(timestamp)
    )
    return base64.b64encode(encoded).decode("ascii")


def _frequency_hz(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        frequency = Decimal(str(value)) * 1_000_000
        if not frequency.is_finite() or frequency <= 0:
            return None
        frequency_hz = int(frequency.to_integral_value(rounding=ROUND_HALF_UP))
        return frequency_hz if 0 < frequency_hz <= 2**63 - 1 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def parse_tinygs_transmitters(satellites: Any) -> list[dict[str, Any]]:
    """Turn receiver configurations into distinct source-tagged downlinks."""
    if not isinstance(satellites, list) or not satellites:
        raise ValueError("TinyGS returned no satellites")

    transmitters: list[dict[str, Any]] = []
    seen: set[tuple[int, str, int]] = set()
    for satellite in satellites:
        if not isinstance(satellite, dict):
            continue
        configurations = satellite.get("configurations")
        if not isinstance(configurations, list):
            continue

        for configuration in configurations:
            if not isinstance(configuration, dict):
                continue
            raw_norad = configuration.get("NORAD")
            if isinstance(raw_norad, bool) or not isinstance(raw_norad, (int, str)):
                continue
            if isinstance(raw_norad, str) and not raw_norad.isdecimal():
                continue
            try:
                norad = int(raw_norad)
            except (TypeError, ValueError):
                continue
            # TinyGS uses 99999 for satellites without a catalog identity.
            if norad <= 0 or norad == 99999:
                continue

            frequency_hz = _frequency_hz(configuration.get("freq"))
            mode = configuration.get("mode")
            if frequency_hz is None or not isinstance(mode, str) or not mode.strip():
                continue
            mode = mode.strip()
            key = (norad, mode, frequency_hz)
            # Several TinyGS receiver profiles can describe the same downlink.
            if key in seen:
                continue
            seen.add(key)

            source_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"tinygs:{norad}:{mode}:{frequency_hz}"))
            name = satellite.get("displayName") or satellite.get("name") or f"NORAD {norad}"
            transmitters.append(
                {
                    "uuid": uuid_to_short_transmitter_id(source_id),
                    "source_transmitter_id": source_id,
                    "source": TINYGS_SOURCE,
                    "description": f"{str(name).strip()} {mode}",
                    "norad_cat_id": norad,
                    "downlink_low": frequency_hz,
                    "mode": mode,
                    "type": "Transmitter",
                    # Supported describes a TinyGS receiver profile, not spacecraft health.
                    "alive": None,
                    "status": "unknown",
                    "citation": TINYGS_SUPPORTED_URL,
                }
            )

    if not transmitters:
        raise ValueError("TinyGS returned no usable transmitter configurations")
    return transmitters


def _fetch_tinygs_transmitters() -> list[dict[str, Any]]:
    response = requests.get(
        TINYGS_SUPPORTED_URL,
        headers={"x-client-timestamp": _client_timestamp()},
        timeout=15,
    )
    response.raise_for_status()
    return parse_tinygs_transmitters(response.json())


async def fetch_tinygs_transmitters(executor: ThreadPoolExecutor) -> list[dict[str, Any]]:
    """Fetch TinyGS without blocking orbital sync's event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, _fetch_tinygs_transmitters)
