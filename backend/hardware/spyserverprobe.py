# Copyright (c) 2025 Efstratios Goudelis
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""
Discovery of publicly shared SpyServer receivers.

SpyServer owners can opt in to Airspy's public directory
(https://airspy.com/directory/), which publishes a live JSON snapshot of every
registered receiver (host, port, device type, location, bandwidth, client
slots). This module fetches that directory and probes candidate servers with
the SpyServer handshake (HELLO -> DEVICE_INFO) so only reachable, live servers
are reported.

Only servers that their owners explicitly listed in the directory are
contacted; nothing here scans arbitrary hosts.
"""

import logging
import socket
import struct
import time
from typing import Any, Dict, List, Optional

import requests

from workers.spyserverclient import (
    DEFAULT_PORT,
    PROTOCOL_VERSION,
    CMD_HELLO,
    MSG_CLIENT_SYNC,
    MSG_DEVICE_INFO,
    DEVICE_TYPE_NAMES,
)

logger = logging.getLogger("spyserver-probe")

DIRECTORY_URL = "https://airspy.com/directory/status.json"
DIRECTORY_TIMEOUT = 15.0
PROBE_TIMEOUT = 8.0
# Cache of the last discovery result, so the station list survives backend
# restarts and is available immediately on the next launch.
DISCOVERY_CACHE_PATH = "data/configs/spyserver_discovery.json"

# Directory deviceType strings -> stable labels
_DIRECTORY_DEVICE_NAMES = {
    "AirspyOne": "Airspy One/Mini/R2",
    "AirspyHF": "Airspy HF+",
    "RTL-SDR": "RTL-SDR",
}


def save_discovery_cache(servers: Dict[str, Dict[str, Any]]) -> bool:
    """Persist the last discovery result so the station list survives restarts."""
    import json
    import os

    try:
        os.makedirs(os.path.dirname(DISCOVERY_CACHE_PATH), exist_ok=True)
        with open(DISCOVERY_CACHE_PATH, "w", encoding="utf-8") as handle:
            json.dump({"saved_at": time.time(), "servers": servers}, handle)
        return True
    except OSError as e:
        logger.warning(f"Could not save SpyServer discovery cache: {e}")
        return False


def load_discovery_cache() -> Dict[str, Dict[str, Any]]:
    """Load the persisted discovery result (empty dict when absent/corrupt)."""
    import json

    try:
        with open(DISCOVERY_CACHE_PATH, encoding="utf-8") as handle:
            payload = json.load(handle)
        servers = payload.get("servers", {})
        if isinstance(servers, dict):
            return servers
    except (OSError, ValueError):
        pass
    return {}


def fetch_public_directory(url: str = DIRECTORY_URL) -> List[Dict[str, Any]]:
    """Fetch the opt-in public SpyServer directory.

    Returns a list of entries with a normalized shape:
        host, port, online, device_type, location, max_clients, ...
    """
    response = requests.get(url, timeout=DIRECTORY_TIMEOUT)
    response.raise_for_status()
    payload = response.json()

    entries: List[Dict[str, Any]] = []
    for server in payload.get("servers", []):
        host = server.get("streamingHost")
        port = int(server.get("streamingPort") or DEFAULT_PORT)
        if not host:
            continue

        location = server.get("antennaLocation") or {}
        device_raw = server.get("deviceType", "")

        entries.append(
            {
                "source": "airspy_directory",
                "host": host,
                "port": port,
                "online": bool(server.get("online")),
                "device_type": _DIRECTORY_DEVICE_NAMES.get(device_raw, device_raw),
                "owner_name": server.get("ownerName") or "anonymous",
                "antenna_type": server.get("antennaType") or "",
                "general_description": server.get("generalDescription") or "",
                "latitude": location.get("lat"),
                "longitude": location.get("long"),
                "max_clients": int(server.get("maxClients") or 0),
                "current_clients": int(server.get("currentClientCount") or 0),
                "maximum_iq_sample_rate": int(server.get("maximumIQSampleRate") or 0),
                "center_frequency": int(server.get("currentCenterFrequency") or 0),
                "server_version": server.get("serverVersion") or "",
                "operating_system": server.get("operatingSystem") or "",
                "last_seen_seconds": int(server.get("lastSeen") or 0),
            }
        )

    logger.info(f"Fetched {len(entries)} SpyServer directory entries from {url}")
    return entries


def probe_server(host: str, port: int = DEFAULT_PORT, timeout: float = PROBE_TIMEOUT) -> Dict[str, Any]:
    """Probe one SpyServer endpoint with the protocol handshake.

    Connects, sends HELLO, waits for DEVICE_INFO (and CLIENT_SYNC when it
    arrives within the window), then disconnects without starting a stream.
    Returns a dict with 'success' plus the device capabilities, or 'success'
    False plus an 'error' string.
    """
    result: Dict[str, Any] = {
        "host": host,
        "port": port,
        "success": False,
        "probed_at": time.time(),
    }

    sock: Optional[socket.socket] = None
    try:
        start = time.monotonic()
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.settimeout(timeout)

        hello_body = struct.pack("<I", PROTOCOL_VERSION) + b"GroundStationProbe"
        sock.sendall(struct.pack("<II", CMD_HELLO, len(hello_body)) + hello_body)

        deadline = time.monotonic() + timeout
        device_info: Optional[Dict[str, Any]] = None
        client_sync: Optional[Dict[str, Any]] = None

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            # Keep the socket timeout aligned with the deadline so an idle
            # server ends the probe instead of blocking the full timeout.
            sock.settimeout(remaining)
            try:
                header = _read_exact(sock, 20)
                _proto_id, msg_type, _stream_type, _seq, body_size = struct.unpack("<IIIII", header)
                body = _read_exact(sock, body_size) if body_size else b""
            except (socket.timeout, TimeoutError):
                break
            # Accept any 2.x protocol build; servers run newer builds than
            # this client's compile-time version.
            if _proto_id >> 24 != PROTOCOL_VERSION >> 24:
                result["error"] = f"Unsupported protocol id 0x{_proto_id:08x}"
                return result
            mtype = msg_type & 0xFFFF
            if mtype == MSG_DEVICE_INFO:
                fields = struct.unpack("<12I", body[:48]) if len(body) >= 48 else (0,) * 12
                device_info = {
                    "device_type": fields[0],
                    "device_type_name": DEVICE_TYPE_NAMES.get(fields[0], f"Unknown ({fields[0]})"),
                    "device_serial": fields[1],
                    "maximum_sample_rate": fields[2],
                    "maximum_bandwidth": fields[3],
                    "decimation_stage_count": fields[4],
                    "maximum_gain_index": fields[6],
                    "minimum_frequency": fields[7],
                    "maximum_frequency": fields[8],
                    "resolution": fields[9],
                    "forced_iq_format": fields[11],
                }
                # Once we have DEVICE_INFO, do not wait the full timeout for a
                # CLIENT_SYNC that may never come (idle servers send nothing
                # else until a stream is configured).
                deadline = min(deadline, time.monotonic() + 1.5)
            elif mtype == MSG_CLIENT_SYNC:
                client_sync = _parse_sync(body)
                if device_info is not None:
                    break

        if device_info is None:
            result["error"] = "No DEVICE_INFO within probe window"
            return result

        result["success"] = True
        result["device_info"] = device_info
        result["client_sync"] = client_sync or {}
        result["latency_seconds"] = time.monotonic() - start
        return result

    except (OSError, struct.error) as e:
        result["error"] = f"{type(e).__name__}: {e}"
        return result
    finally:
        if sock:
            try:
                sock.close()
            except OSError:
                pass


def _read_exact(sock: socket.socket, count: int) -> bytes:
    buf = bytearray()
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            raise OSError("connection closed during probe")
        buf.extend(chunk)
    return bytes(buf)


def _parse_sync(body: bytes) -> Dict[str, Any]:
    names = [
        "can_control",
        "gain",
        "device_center_frequency",
        "iq_center_frequency",
        "fft_center_frequency",
        "minimum_iq_center_frequency",
        "maximum_iq_center_frequency",
        "minimum_fft_center_frequency",
        "maximum_fft_center_frequency",
        "iq_decimation",
        "iq_format",
    ]
    fields = struct.unpack("<11I", body[:44]) if len(body) >= 44 else ()
    return dict(zip(names, fields))


def build_sdr_record(entry: Dict[str, Any], name: Optional[str] = None) -> Dict[str, Any]:
    """Build an add_sdr() payload from a probed public server.

    Frequencies are stored in MHz in the SDRs table (the configure handler
    multiplies them by 1e6), so convert the Hz values the probe reports.
    """
    device_info = entry.get("device_info") or {}
    device_name = device_info.get("device_type_name") or entry.get("device_type") or "SpyServer"
    location = f"@ {entry.get('latitude')},{entry.get('longitude')}" if entry.get("latitude") is not None else ""

    freq_min_hz = device_info.get("minimum_frequency")
    freq_max_hz = device_info.get("maximum_frequency")

    return {
        "name": name or f"{device_name} {entry['host']}{' ' + location if location else ''}",
        "type": "spyserver",
        "driver": "spyserver",
        "serial": None,
        "host": entry["host"],
        "port": entry["port"],
        # SDRs table stores MHz (configure-sdr multiplies by 1e6). Zero is a
        # valid minimum for HF devices, so only None means "unknown".
        "frequency_min": round(freq_min_hz / 1e6) if freq_min_hz is not None else None,
        "frequency_max": round(freq_max_hz / 1e6) if freq_max_hz is not None else None,
    }
