# Copyright (c) 2026 Efstratios Goudelis
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Fetch TinyGS radio configurations and adapt them for orbital sync."""

import asyncio
import base64
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import requests

from demodulators.loraconfig import normalize_lora
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


def _lora_config(configuration: dict[str, Any]) -> dict[str, Any] | None:
    """Convert TinyGS LoRa fields into the decoder's units and validate them."""
    values: dict[str, Any] = {}
    for source_key, target_key in (
        ("sf", "sf"),
        ("cr", "cr"),
        ("sw", "sync_word"),
        ("pl", "preamble_len"),
        ("crc", "has_crc"),
        ("iq", "invert_iq"),
    ):
        if configuration.get(source_key) is not None:
            values[target_key] = configuration[source_key]

    if configuration.get("bw") is not None:
        try:
            bandwidth_hz = Decimal(str(configuration["bw"])) * 1000
            if not bandwidth_hz.is_finite() or bandwidth_hz != bandwidth_hz.to_integral_value():
                return None
            values["bw"] = int(bandwidth_hz)
        except (InvalidOperation, ValueError, TypeError):
            return None

    if configuration.get("fldro") is not None:
        # TinyGS only forces LDRO on; zero leaves symbol-duration detection automatic.
        if configuration["fldro"] not in (0, 1, False, True):
            return None
        values["ldro_mode"] = 1 if configuration["fldro"] else 2

    if not values:
        return None
    try:
        normalized = normalize_lora(values)
    except (TypeError, ValueError, OverflowError):
        return None
    # Keep only source-supplied fields so the suggestion can distinguish defaults.
    return {key: normalized[key] for key in values}


def parse_tinygs_transmitters(satellites: Any) -> list[dict[str, Any]]:
    """Turn each distinct receiver profile into a source-tagged transmitter."""
    if not isinstance(satellites, list) or not satellites:
        raise ValueError("TinyGS returned no satellites")

    transmitters: list[dict[str, Any]] = []
    grouped: dict[tuple[int, str, int], dict[str, Any]] = {}
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
            name = satellite.get("displayName") or satellite.get("name") or f"NORAD {norad}"
            group = grouped.setdefault(key, {"name": str(name).strip(), "profiles": {}})
            profile = _lora_config(configuration) if mode.lower() == "lora" else None
            fingerprint = json.dumps(profile, sort_keys=True, separators=(",", ":"))
            group["profiles"][fingerprint] = profile

    for (norad, mode, frequency_hz), group in grouped.items():
        base_source_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"tinygs:{norad}:{mode}:{frequency_hz}")
        )
        for fingerprint, profile in sorted(group["profiles"].items()):
            # A profile's ID must not depend on how many sibling profiles exist.
            source_id = (
                str(uuid.uuid5(uuid.NAMESPACE_URL, f"{base_source_id}:{fingerprint}"))
                if profile is not None
                else base_source_id
            )
            transmitters.append(
                {
                    "uuid": uuid_to_short_transmitter_id(source_id),
                    "source_transmitter_id": source_id,
                    "source": TINYGS_SOURCE,
                    "description": f"{group['name']} {mode}",
                    "norad_cat_id": norad,
                    "downlink_low": frequency_hz,
                    "mode": mode,
                    "lora_config": profile,
                    "type": "Transmitter",
                    # Treat TinyGS-supported receiver profiles as active downlinks.
                    "alive": True,
                    "status": "active",
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
