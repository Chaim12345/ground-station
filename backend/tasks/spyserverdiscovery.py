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
SpyServer public receiver discovery background task.

Fetches the opt-in Airspy SpyServer directory, probes the listed servers with
the protocol handshake and reports reachable receivers to the main process via
the same progress-queue mechanism the SoapySDR discovery task uses.

This task intentionally runs in a worker process: probing hundreds of servers
can take a while and must not block the backend event loop.
"""

import logging
import multiprocessing
import signal
import time
from multiprocessing import Queue
from typing import Dict, Optional

from hardware.spyserverprobe import (
    DEFAULT_PORT,
    build_sdr_record,
    fetch_public_directory,
    probe_server,
)

try:
    import setproctitle

    HAS_SETPROCTITLE = True
except ImportError:
    HAS_SETPROCTITLE = False

logger = logging.getLogger("spyserver-discovery-task")


class GracefulKiller:
    """Handle SIGTERM gracefully within the process."""

    def __init__(self):
        self.kill_now = False
        signal.signal(signal.SIGTERM, self.exit_gracefully)
        signal.signal(signal.SIGINT, self.exit_gracefully)

    def exit_gracefully(self, *args):
        self.kill_now = True


def _emit(_progress_queue: Optional[Queue], message: str, progress: Optional[int] = None):
    """Send a progress output line to the main process if a queue exists."""
    if not _progress_queue:
        return
    payload: Dict = {"type": "output", "output": message, "stream": "stdout"}
    if progress is not None:
        payload["progress"] = progress
    _progress_queue.put(payload)


def spyserver_discovery_task(
    limit: int = 0, probe_timeout: float = 8.0, _progress_queue: Optional[Queue] = None
):
    """
    Discover publicly shared SpyServer receivers.

    Args:
        limit: Maximum directory entries to probe (0 = all). Probing is
            throttled naturally by connect timeouts, so a limit is useful for
            quick first looks.
        probe_timeout: Per-server connect/handshake timeout in seconds.
        _progress_queue: Queue for sending progress updates (injected by manager)

    Returns:
        Dict with discovery results (probeable servers only).
    """
    if HAS_SETPROCTITLE:
        setproctitle.setproctitle("Ground Station - SpyServer-Discovery")
    multiprocessing.current_process().name = "Ground Station - SpyServer-Discovery"

    killer = GracefulKiller()

    try:
        _emit(_progress_queue, "Fetching public SpyServer directory...", progress=5)

        # The directory fetch runs unattended (scheduled); retry once on a
        # transient DNS/HTTP failure before giving up this cycle.
        entries = None
        for fetch_attempt in range(1, 3):
            try:
                entries = fetch_public_directory()
                break
            except Exception as fetch_error:
                logger.warning(f"Directory fetch attempt {fetch_attempt} failed: {fetch_error}")
                if fetch_attempt < 2 and not killer.kill_now:
                    time.sleep(5.0)
                    if not killer.kill_now:
                        continue
        if entries is None:
            error_msg = "Could not fetch the public SpyServer directory"
            logger.error(error_msg)
            _emit(_progress_queue, error_msg, progress=100)
            return {
                "status": "failed",
                "mode": "single",
                "error": error_msg,
                "directory_count": 0,
                "probed_count": 0,
                "server_count": 0,
                "servers": {},
            }
        online = [e for e in entries if e.get("online")]
        _emit(
            _progress_queue,
            f"Directory lists {len(entries)} receivers, {len(online)} marked online",
            progress=15,
        )

        candidates = online if limit <= 0 else online[:limit]
        _emit(_progress_queue, f"Probing {len(candidates)} server(s) with protocol handshake...", progress=20)

        # Probe in parallel: with hundreds of listings, sequential connect
        # timeouts would take tens of minutes; a thread pool finishes in ~1.
        from concurrent.futures import ThreadPoolExecutor

        def _probe_entry(entry):
            host, port = entry["host"], int(entry["port"])
            probe = probe_server(host, port, timeout=probe_timeout)
            return entry, probe

        results = []
        probed = 0
        with ThreadPoolExecutor(max_workers=64) as pool:
            for entry, probe in pool.map(_probe_entry, candidates):
                if killer.kill_now:
                    _emit(_progress_queue, "Discovery interrupted by user")
                    break

                host, port = entry["host"], int(entry["port"])
                probed += 1
                merged = {**entry, **{k: v for k, v in probe.items() if k != "host" and k != "port"}}
                merged["port"] = port

                if probe.get("success"):
                    device = probe["device_info"]
                    results.append(merged)
                    _emit(
                        _progress_queue,
                        (
                            f"  • {host}:{port} - {device['device_type_name']} "
                            f"(rate {device['maximum_sample_rate']} S/s, "
                            f"{device['minimum_frequency'] / 1e6:.1f}-{device['maximum_frequency'] / 1e6:.1f} MHz)"
                        ),
                    )

                # Report incremental progress; probing is the bulk of runtime.
                if _progress_queue:
                    progress = 20 + int(70 * probed / max(len(candidates), 1))
                    _progress_queue.put(
                        {"type": "output", "output": "", "stream": "stdout", "progress": progress}
                    )

        # Serialize final results for the main process. The shape mirrors the
        # SoapySDR discovery payload (name/ip/port/sdrs) so the shared
        # discovered-servers UI renders these entries natively; the extra
        # fields (device info, sdr_record) carry the SpyServer specifics.
        serialized = []
        for server in results:
            device = server.get("device_info", {})
            sdr_record = build_sdr_record(server)
            device_label = device.get("device_type_name") or server.get("device_type") or "SpyServer"
            location = (
                f"@ {server['latitude']:.3f},{server['longitude']:.3f}"
                if server.get("latitude") is not None
                else ""
            )
            owner = server.get("owner_name") or ""
            display_name = f"{device_label} {location}".strip()
            if owner and owner.lower() != "anonymous":
                display_name = f"{display_name} ({owner})"
            serialized.append(
                {
                    # Soapy-compatible fields for the shared UI list
                    "name": display_name or "SpyServer",
                    "ip": server["host"],
                    "port": server["port"],
                    "status": "online",
                    "sdrs": [
                        {
                            "label": device_label,
                            "driver": "spyserver",
                            "sdr_record": sdr_record,
                        }
                    ],
                    # SpyServer-specific fields
                    "host": server["host"],
                    "device_type": device_label,
                    "device_serial": device.get("device_serial"),
                    "maximum_sample_rate": device.get("maximum_sample_rate"),
                    "maximum_bandwidth": device.get("maximum_bandwidth"),
                    "minimum_frequency": device.get("minimum_frequency"),
                    "maximum_frequency": device.get("maximum_frequency"),
                    "resolution": device.get("resolution"),
                    "owner_name": server.get("owner_name"),
                    "antenna_type": server.get("antenna_type"),
                    "general_description": server.get("general_description"),
                    "latitude": server.get("latitude"),
                    "longitude": server.get("longitude"),
                    "max_clients": server.get("max_clients"),
                    "current_clients": server.get("current_clients"),
                    "latency_seconds": server.get("latency_seconds"),
                    "sdr_record": sdr_record,
                }
            )

        _emit(
            _progress_queue,
            f"Discovery complete: {len(serialized)} reachable SpyServer(s)",
            progress=100,
        )
        if _progress_queue:
            _progress_queue.put(
                {
                    "type": "discovery_update",
                    "servers": {
                        f"{s['host']}:{s['port']}": s for s in serialized
                    },
                    "server_count": len(serialized),
                    "active_count": len(serialized),
                    "sdr_count": len(serialized),
                }
            )

        return {
            "status": "completed" if not killer.kill_now else "interrupted",
            "mode": "single",
            "directory_count": len(entries),
            "probed_count": len(candidates),
            "server_count": len(serialized),
            "servers": {f"{s['host']}:{s['port']}": s for s in serialized},
        }

    except Exception as e:
        error_msg = f"Error during SpyServer discovery: {e}"
        logger.exception(error_msg)
        if _progress_queue:
            _progress_queue.put({"type": "error", "error": error_msg, "stream": "stderr"})
        raise


def spyserver_quick_check_task(
    host: str, port: Optional[int] = None, _progress_queue: Optional[Queue] = None
):
    """
    Probe a single SpyServer endpoint (manual host:port) without touching the
    public directory. Useful to test a server before adding it as an SDR.

    Returns the probe result plus a ready-to-add SDR record on success.
    """
    if HAS_SETPROCTITLE:
        setproctitle.setproctitle("Ground Station - SpyServer-Check")
    multiprocessing.current_process().name = "Ground Station - SpyServer-Check"

    port = int(port or DEFAULT_PORT)
    try:
        _emit(_progress_queue, f"Probing SpyServer at {host}:{port}...", progress=10)
        probe = probe_server(host, port)
        if not probe.get("success"):
            _emit(_progress_queue, f"Probe failed: {probe.get('error')}", progress=100)
            return {"status": "failed", "error": probe.get("error"), "host": host, "port": port}

        entry = {
            "host": host,
            "port": port,
            "device_info": probe["device_info"],
            "device_type": probe["device_info"]["device_type_name"],
            "client_sync": probe.get("client_sync", {}),
        }
        if _progress_queue:
            _progress_queue.put(
                {
                    "type": "discovery_update",
                    "servers": {f"{host}:{port}": {**entry, "sdr_record": build_sdr_record(entry)}},
                    "server_count": 1,
                    "active_count": 1,
                    "sdr_count": 1,
                }
            )
        _emit(_progress_queue, f"OK: {probe['device_info']['device_type_name']}", progress=100)
        return {
            "status": "completed",
            "probe": probe,
            "sdr_record": build_sdr_record(entry),
        }

    except Exception as e:
        error_msg = f"Error probing SpyServer {host}:{port}: {e}"
        logger.exception(error_msg)
        if _progress_queue:
            _progress_queue.put({"type": "error", "error": error_msg, "stream": "stderr"})
        raise
