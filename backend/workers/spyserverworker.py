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
SDR worker process for remote SpyServer devices.

SpyServer is Airspy's remote streaming server; public receivers are shared
through the Airspy directory (https://airspy.com/directory/). This worker
connects to one SpyServer host, pulls complex64 IQ blocks from the protocol
client and publishes them to the shared FFT/demodulator queues, following the
same contract as the RTL-SDR worker.

Fair-use note: these are receivers shared by their owners. The worker keeps
the requested sample rate modest (the decimation picker never asks for more
than the server's native rate) and disconnects cleanly on stop.
"""

import logging
import time
from typing import Any, Dict

import numpy as np
import psutil

from common.errors import format_device_error
from common.iqsamples import require_complex64
from workers.spyserverclient import SpyServerClient

# Configure logging for the worker process
logger = logging.getLogger("spyserver-worker")

# Target blocks per second for constant rate streaming (matches other workers)
TARGET_BLOCKS_PER_SEC = 15


def calculate_samples_per_scan(sample_rate, fft_size):
    """Calculate number of samples per scan for constant block rate streaming."""
    if fft_size is None:
        fft_size = 8192

    num_samples = int(sample_rate / TARGET_BLOCKS_PER_SEC)
    num_samples = 2 ** int(np.ceil(np.log2(num_samples)))
    num_samples = max(num_samples, fft_size)
    num_samples = min(num_samples, 1048576)

    return num_samples


def remove_dc_offset(samples):
    """Remove DC offset by subtracting the mean."""
    mean_i = np.mean(np.real(samples))
    mean_q = np.mean(np.imag(samples))
    return samples - (mean_i + 1j * mean_q)


def _format_worker_error(stage: str, error: Exception, config: Dict[str, Any]) -> str:
    """Add the SpyServer endpoint to the native error."""
    context: Dict[str, Any] = {}
    if isinstance(config, dict) and config:
        host = config.get("host", "?")
        port = config.get("port", 5555)
        context["endpoint"] = f"{host}:{port}"
    return str(format_device_error("SpyServer", stage, error, **context))


def spyserver_worker_process(
    config_queue, data_queue, stop_event, iq_queue_fft=None, iq_queue_demod=None
):
    """
    Worker process for remote SpyServer devices.

    Args:
        config_queue: Queue for receiving configuration from the main process
        data_queue: Queue for sending processed data back to the main process
        stop_event: Event to signal the process to stop
        iq_queue_fft: Queue for streaming raw IQ samples to FFT processor
        iq_queue_demod: Queue for streaming raw IQ samples to demodulators
    """
    sdr = None
    sdr_id = None
    client_id = None
    config: Dict[str, Any] = {}
    stage = "reading initial configuration"

    logger.info(f"SpyServer worker process started for SDR {sdr_id} for client {client_id}")

    try:
        config = config_queue.get()
        logger.info(f"Initial configuration: {config}")
        new_config = config
        old_config = config

        sdr_id = config.get("sdr_id")
        client_id = config.get("client_id")
        fft_size = config.get("fft_size", 16384)
        fft_window = config.get("fft_window", "hanning")
        fft_averaging = config.get("fft_averaging", 6)
        fft_overlap_percent = int(config.get("fft_overlap_percent", 0) or 0)
        fft_overlap_depth = int(config.get("fft_overlap_depth", 16) or 16)

        has_iq_consumers = iq_queue_fft is not None or iq_queue_demod is not None

        hostname = config.get("host", "")
        port = int(config.get("port", 5555) or 5555)

        # Connect to the SpyServer
        stage = "connecting to spyserver"
        logger.info(f"Connecting to SpyServer at {hostname}:{port}...")
        sdr = SpyServerClient(hostname=hostname, port=port)
        sdr.connect()

        # Configure the receiver
        stage = "configuring receiver"
        logical_center_freq = config.get("center_freq", 100e6)
        sample_rate = config.get("sample_rate", 2.048e6)

        # Frequency contract note: SpyServer tunes the IQ stream center
        # directly, so RF center and logical center are the same here and
        # offset_freq has no hardware meaning (it is only logged).
        offset_freq = config.get("offset_freq", 0) or 0

        # Rate the user asked for (what we request again after a reconnect);
        # sample_rate below is the effective (decimated) server rate.
        requested_sample_rate = sample_rate

        # Sample rate first: decimation changes the stream, then tune it.
        sdr.sample_rate = sample_rate
        sdr.center_freq = logical_center_freq
        tuner_agc = config.get("tuner_agc", False)
        if not tuner_agc:
            sdr.gain = config.get("gain", 20)
        sdr.set_agc_mode(bool(tuner_agc))
        sdr.start_stream()
        # Prefer the effective (decimated) rate the server actually delivers.
        sample_rate = sdr.sample_rate or sample_rate

        logger.info(
            "SpyServer configured: "
            f"device={sdr.device_type_name}, sample_rate={sample_rate}, "
            f"logical_center_freq={logical_center_freq}, gain={config.get('gain')}, "
            f"agc={tuner_agc}, offset_freq(ignored)={offset_freq}"
        )

        num_samples = calculate_samples_per_scan(sample_rate, fft_size)

        data_queue.put(
            {
                "type": "streamingstart",
                "client_id": client_id,
                "message": None,
                "timestamp": time.time(),
            }
        )
        stage = "streaming samples"

        stats: Dict[str, Any] = {
            "samples_read": 0,
            "iq_chunks_out": 0,
            "read_errors": 0,
            "queue_drops": 0,
            "last_activity": None,
            "errors": 0,
            "cpu_percent": 0.0,
            "memory_mb": 0.0,
            "memory_percent": 0.0,
        }
        last_stats_send = time.time()
        stats_send_interval = 1.0
        stream_chunk_id = 0
        stream_sample_index = 0

        process = psutil.Process()
        last_cpu_check = time.time()
        cpu_check_interval = 0.5

        while not stop_event.is_set():
            current_time = time.time()
            if current_time - last_cpu_check >= cpu_check_interval:
                try:
                    stats["cpu_percent"] = process.cpu_percent()
                    mem_info = process.memory_info()
                    stats["memory_mb"] = mem_info.rss / (1024 * 1024)
                    stats["memory_percent"] = process.memory_percent()
                    last_cpu_check = current_time
                except Exception as e:
                    logger.debug(f"Error updating CPU/memory usage: {e}")

            if current_time - last_stats_send >= stats_send_interval:
                data_queue.put(
                    {
                        "type": "stats",
                        "client_id": client_id,
                        "sdr_id": sdr_id,
                        "stats": stats.copy(),
                        "timestamp": current_time,
                    }
                )
                last_stats_send = current_time

            # Handle live configuration updates without blocking the stream
            try:
                if not config_queue.empty():
                    new_config = config_queue.get_nowait()

                    if "sample_rate" in new_config and new_config["sample_rate"] != sample_rate:
                        requested_sample_rate = new_config["sample_rate"]
                        sdr.sample_rate = new_config["sample_rate"]
                        sample_rate = sdr.sample_rate or new_config["sample_rate"]
                        num_samples = calculate_samples_per_scan(sample_rate, fft_size)
                        logger.info(f"Updated sample rate: {sample_rate}")

                    if "center_freq" in new_config and logical_center_freq != new_config["center_freq"]:
                        logical_center_freq = new_config["center_freq"]
                        # SpyServer tunes the IQ stream center directly.
                        sdr.center_freq = logical_center_freq
                        logger.info(
                            f"Updated center frequency: logical={logical_center_freq}"
                        )

                    if "fft_size" in new_config and old_config.get("fft_size", 0) != new_config["fft_size"]:
                        fft_size = new_config["fft_size"]
                        num_samples = calculate_samples_per_scan(sample_rate, fft_size)
                        logger.info(f"Updated FFT size: {fft_size}")

                    if "fft_window" in new_config and old_config.get("fft_window") != new_config["fft_window"]:
                        fft_window = new_config["fft_window"]
                        logger.info(f"Updated FFT window: {fft_window}")

                    if "fft_averaging" in new_config and old_config.get("fft_averaging") != new_config["fft_averaging"]:
                        fft_averaging = new_config["fft_averaging"]
                        logger.info(f"Updated FFT averaging: {fft_averaging}")

                    if "fft_overlap_percent" in new_config and old_config.get(
                        "fft_overlap_percent", fft_overlap_percent
                    ) != new_config["fft_overlap_percent"]:
                        fft_overlap_percent = int(new_config["fft_overlap_percent"] or 0)
                        logger.info(f"Updated FFT overlap percent: {fft_overlap_percent}%")

                    if "fft_overlap_depth" in new_config and old_config.get(
                        "fft_overlap_depth", fft_overlap_depth
                    ) != new_config["fft_overlap_depth"]:
                        fft_overlap_depth = int(new_config["fft_overlap_depth"] or 16)
                        logger.info(f"Updated FFT overlap depth: {fft_overlap_depth}")

                    if "tuner_agc" in new_config and old_config.get("tuner_agc") != new_config["tuner_agc"]:
                        tuner_agc = new_config["tuner_agc"]
                        sdr.set_agc_mode(bool(tuner_agc))
                        if not tuner_agc and "gain" in new_config:
                            sdr.gain = new_config["gain"]
                        logger.info(f"Updated server AGC: {tuner_agc}")

                    if "gain" in new_config and not tuner_agc and old_config.get("gain") != new_config["gain"]:
                        sdr.gain = new_config["gain"]
                        logger.info(f"Updated gain: {new_config['gain']}")

                    old_config = new_config

            except Exception as e:
                error_msg = _format_worker_error("updating configuration", e, config)
                logger.exception(error_msg)
                if data_queue:
                    data_queue.put(
                        {
                            "type": "error",
                            "client_id": client_id,
                            "message": error_msg,
                            "timestamp": time.time(),
                        }
                    )

            try:
                # Pull one block of IQ from the SpyServer stream
                samples = sdr.read_samples(num_samples)
                stats["samples_read"] += len(samples)
                stats["last_activity"] = time.time()

                # Enforce pipeline contract: workers publish complex64 IQ samples.
                samples = require_complex64(samples, source="spyserver-worker")

                samples = remove_dc_offset(samples)
                chunk_sample_count = len(samples)
                chunk_id = stream_chunk_id
                chunk_start_sample = stream_sample_index
                stream_chunk_id += 1
                stream_sample_index += chunk_sample_count

                if has_iq_consumers:
                    timestamp = time.time()

                    if iq_queue_fft is not None:
                        try:
                            if not iq_queue_fft.full():
                                iq_message = {
                                    "samples": samples.copy(),
                                    # `center_freq` is intentionally logical (not
                                    # hardware RF); SpyServer IQ stream center
                                    # == logical center.
                                    "center_freq": logical_center_freq,
                                    "logical_center_freq_hz": logical_center_freq,
                                    "rf_center_freq_hz": logical_center_freq,
                                    "dsp_shift_hz": 0.0,
                                    "offset_freq_hz": offset_freq,
                                    "sample_rate": sample_rate,
                                    "timestamp": timestamp,
                                    "stream_chunk_id": chunk_id,
                                    "stream_start_sample": chunk_start_sample,
                                    "stream_sample_count": chunk_sample_count,
                                    "config": {
                                        "fft_size": fft_size,
                                        "fft_window": fft_window,
                                        "fft_averaging": fft_averaging,
                                        "fft_overlap_percent": fft_overlap_percent,
                                        "fft_overlap_depth": fft_overlap_depth,
                                    },
                                }
                                iq_queue_fft.put_nowait(iq_message)
                                stats["iq_chunks_out"] += 1
                            else:
                                stats["queue_drops"] += 1
                        except Exception:
                            stats["queue_drops"] += 1

                    if iq_queue_demod is not None:
                        try:
                            if not iq_queue_demod.full():
                                demod_message = {
                                    "samples": samples.copy(),
                                    "center_freq": logical_center_freq,
                                    "logical_center_freq_hz": logical_center_freq,
                                    "rf_center_freq_hz": logical_center_freq,
                                    "dsp_shift_hz": 0.0,
                                    "offset_freq_hz": offset_freq,
                                    "sample_rate": sample_rate,
                                    "timestamp": timestamp,
                                    "stream_chunk_id": chunk_id,
                                    "stream_start_sample": chunk_start_sample,
                                    "stream_sample_count": chunk_sample_count,
                                }
                                iq_queue_demod.put_nowait(demod_message)
                                stats["iq_chunks_out"] += 1
                            else:
                                stats["queue_drops"] += 1
                        except Exception:
                            stats["queue_drops"] += 1

            except Exception as e:
                error_msg = _format_worker_error("reading samples", e, config)
                logger.warning(error_msg)
                stats["errors"] += 1

                # Public SpyServers are shared and flaky: servers drop clients
                # on overload or connection hiccups mid-stream. Try to
                # reconnect instead of dying; give up after several consecutive
                # failures so the supervisor can restart on the next action.
                reconnected = False
                for attempt in range(1, 4):
                    if stop_event.is_set():
                        break
                    wait_s = 3.0 * attempt
                    logger.info(
                        "SpyServer reconnect attempt %d/3 in %.0fs (endpoint=%s:%s)",
                        attempt, wait_s, hostname, port,
                    )
                    if stop_event.wait(wait_s):
                        break
                    try:
                        try:
                            sdr.close()
                        except Exception:  # noqa: BLE001 - already broken
                            pass
                        sdr = SpyServerClient(hostname=hostname, port=port)
                        sdr.connect()
                        # Re-apply the stream configuration the user asked for.
                        sdr.sample_rate = requested_sample_rate
                        sdr.center_freq = logical_center_freq
                        if not tuner_agc:
                            sdr.gain = config.get("gain", 20)
                        sdr.set_agc_mode(bool(tuner_agc))
                        sdr.start_stream()
                        sample_rate = sdr.sample_rate or requested_sample_rate
                        num_samples = calculate_samples_per_scan(sample_rate, fft_size)
                        logger.info(
                            "SpyServer reconnected: device=%s, sample_rate=%s",
                            sdr.device_type_name, sample_rate,
                        )
                        reconnected = True
                        break
                    except Exception as retry_error:
                        logger.warning(
                            "SpyServer reconnect attempt %d failed: %s", attempt, retry_error
                        )

                if not reconnected:
                    data_queue.put(
                        {
                            "type": "error",
                            "client_id": client_id,
                            "message": error_msg,
                            "timestamp": time.time(),
                        }
                    )
                    # A dead SpyServer connection that survives all reconnect
                    # attempts does not recover by itself; exit so the
                    # supervisor can restart the worker on the next user
                    # action instead of streaming silence forever.
                    break

    except Exception as e:
        error_msg = _format_worker_error(stage, e, config)
        logger.exception(error_msg)
        data_queue.put(
            {
                "type": "error",
                "client_id": client_id,
                "message": error_msg,
                "timestamp": time.time(),
            }
        )
    finally:
        if sdr is not None:
            try:
                sdr.close()
            except Exception:  # noqa: BLE001 - shutdown must not raise
                pass
        logger.info(f"SpyServer worker stopped for SDR {sdr_id}")
