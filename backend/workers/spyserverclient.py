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
SpyServer TCP protocol client.

SpyServer is Airspy's remote SDR streaming server (used by SDR# and publicly
shared receivers listed in the Airspy directory). This module implements the
client side of its binary TCP protocol, clean-room, based on the wire format
documented by the SatDump spyserver_support plugin (GPL-3.0, same license
family as this project).

Protocol summary (all integers little-endian):
- Commands (client -> server): 8-byte header (uint32 command, uint32 body size)
  followed by the body.
  - CMD_HELLO (0): body = uint32 protocol version + client name bytes.
  - CMD_SET_SETTING (2): body = uint32 setting id, uint32 value.
  - CMD_PING (3): empty body.
- Messages (server -> client): 20-byte header (uint32 protocol id, message
  type, stream type, sequence, body size) followed by the body.
  - MSG_DEVICE_INFO (0): 12 x uint32 describing the attached device.
  - MSG_CLIENT_SYNC (1): client capabilities and current device state.
  - MSG_PONG (2): reply to CMD_PING.
  - IQ stream messages carry the message type in the low 16 bits of the
    message type field and a gain flag (dB, 16-bit) in the high 16 bits.
    Payload is interleaved I/Q samples in the negotiated format.
"""

import logging
import socket
import struct
import threading
import time
from typing import Any, Dict, Optional

import numpy as np

# Version negotiated at handshake: (major << 24) | (minor << 16) | build.
PROTOCOL_VERSION = (2 << 24) | (0 << 16) | 1700

MAX_MESSAGE_BODY_SIZE = 1 << 20

# Client -> server commands
CMD_HELLO = 0
CMD_SET_SETTING = 2
CMD_PING = 3

# Settings
SETTING_STREAMING_MODE = 0
SETTING_STREAMING_ENABLED = 1
SETTING_GAIN = 2
SETTING_IQ_FORMAT = 100
SETTING_IQ_FREQUENCY = 101
SETTING_IQ_DECIMATION = 102
SETTING_IQ_DIGITAL_GAIN = 103

# Streaming modes (bit flags)
STREAM_MODE_IQ_ONLY = 1
STREAM_MODE_FFT_IQ = 5

# Stream formats
FORMAT_UINT8 = 1
FORMAT_INT16 = 2
FORMAT_FLOAT = 4

# Server -> client message types
MSG_DEVICE_INFO = 0
MSG_CLIENT_SYNC = 1
MSG_PONG = 2

MSG_UINT8_IQ = 100
MSG_INT16_IQ = 101
MSG_FLOAT_IQ = 103

# Device types reported in DEVICE_INFO
DEVICE_INVALID = 0
DEVICE_AIRSPY_ONE = 1
DEVICE_AIRSPY_HF = 2
DEVICE_RTLSDR = 3

DEVICE_TYPE_NAMES = {
    DEVICE_INVALID: "Unknown",
    DEVICE_AIRSPY_ONE: "Airspy One/Mini/R2",
    DEVICE_AIRSPY_HF: "Airspy HF+",
    DEVICE_RTLSDR: "RTL-SDR",
}

DEFAULT_PORT = 5555
CONNECT_TIMEOUT = 8.0
READ_TIMEOUT = 15.0


class SpyServerError(Exception):
    """Raised on protocol or connection failures."""


class SpyServerClient:
    """
    Client for an Airspy SpyServer streaming IQ samples as complex64.

    The server pushes IQ messages continuously once streaming is enabled, so a
    background reader thread decodes them into an internal buffer while
    read_samples() pulls blocks out in a blocking, pull-style API that mirrors
    RtlSdrTcpClient. This keeps the SDR worker loop (which is shared across
    device families) unchanged.
    """

    def __init__(self, hostname: str, port: int = DEFAULT_PORT, verbose: bool = False):
        self.server_host = hostname
        self.server_port = port
        self._sock: Optional[socket.socket] = None
        self._connected = False
        self._closed = False

        self._logger = logging.getLogger("spyserver-client")
        self._logger.setLevel(logging.DEBUG if verbose else logging.INFO)

        # Device capabilities reported by the server (12 uint32 fields)
        self.device_info: Dict[str, Any] = {}
        # Current client sync state (gain, tunings, allowed ranges)
        self.client_sync: Dict[str, Any] = {}

        # Negotiated stream state
        self._iq_format = FORMAT_UINT8
        self._effective_sample_rate: Optional[float] = None
        self._center_freq: Optional[float] = None
        self._gain_index: Optional[int] = None
        self._agc_enabled: Optional[bool] = None
        self._decimation: Optional[int] = None
        self._streaming = False

        # Stream integrity: SpyServer numbers every message. A jump in the
        # sequence means the server dropped IQ blocks for this client (slow
        # link / overloaded server). Gaps are zero-filled so the recording
        # keeps wall-clock timing (Doppler, frame timing) and are counted.
        self.stream_stats: Dict[str, int] = {
            "iq_messages": 0,
            "missing_messages": 0,
            "gaps": 0,
            "zero_filled_samples": 0,
            "samples": 0,
        }
        self._last_iq_seq: Optional[int] = None
        self._last_iq_len = 0
        self._stream_t0: Optional[float] = None
        self._sync_count = 0

        # Sample buffer shared between the reader thread and read_samples()
        self._buffer = bytearray()
        self._buffer_lock = threading.Lock()
        self._buffer_cond = threading.Condition(self._buffer_lock)
        self._reader_thread: Optional[threading.Thread] = None
        self._reader_error: Optional[BaseException] = None

    # ------------------------------------------------------------------
    # Connection / handshake
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Connect, negotiate the protocol and wait for DEVICE_INFO."""
        if self._connected and self._sock:
            self._logger.info("Already connected.")
            return True

        self._logger.info(
            f"Connecting to SpyServer at {self.server_host}:{self.server_port}..."
        )
        try:
            self._sock = socket.create_connection(
                (self.server_host, self.server_port), timeout=CONNECT_TIMEOUT
            )
            self._sock.settimeout(CONNECT_TIMEOUT)
        except OSError as e:
            self._cleanup()
            raise SpyServerError(f"Could not connect to {self.server_host}:{self.server_port}: {e}")

        # HELLO: protocol version + application name. SDR# sends its own name;
        # servers accept any client string.
        self._send_command(CMD_HELLO, struct.pack("<I", PROTOCOL_VERSION) + b"GroundStation")

        # Wait for DEVICE_INFO (message type 0). CLIENT_SYNC usually follows.
        deadline = time.monotonic() + CONNECT_TIMEOUT
        while time.monotonic() < deadline:
            proto_id, msg_type, stream_type, _seq, body = self._read_message()
            # Accept any 2.x protocol build (servers report their own build,
            # e.g. 0x02000782 = 2.0.1922); the wire format is compatible.
            if proto_id >> 24 != PROTOCOL_VERSION >> 24:
                self._cleanup()
                raise SpyServerError(f"Unsupported SpyServer protocol id 0x{proto_id:08x}")

            mtype = msg_type & 0xFFFF
            if mtype == MSG_DEVICE_INFO:
                self.device_info = self._parse_device_info(body)
                self._logger.info(f"SpyServer DEVICE_INFO: {self.device_info}")
            elif mtype == MSG_CLIENT_SYNC:
                self.client_sync = self._parse_client_sync(body)
                # CLIENT_SYNC arrival means the handshake is complete even if
                # a server sends it before DEVICE_INFO.
                if self.device_info:
                    break

        if not self.device_info:
            self._cleanup()
            raise SpyServerError("SpyServer did not report DEVICE_INFO after handshake")

        self._connected = True
        self._logger.info(
            "SpyServer connected: "
            f"device={DEVICE_TYPE_NAMES.get(self.device_info.get('device_type'))} "
            f"max_sample_rate={self.device_info.get('maximum_sample_rate')} "
            f"freq={self.device_info.get('minimum_frequency')}-{self.device_info.get('maximum_frequency')}"
        )

        # Start the reader thread that decodes IQ messages into the buffer.
        self._reader_thread = threading.Thread(
            target=self._reader_loop, name="spyserver-reader", daemon=True
        )
        self._reader_thread.start()
        return True

    def _cleanup(self):
        self._closed = True
        self._connected = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    # ------------------------------------------------------------------
    # Wire helpers
    # ------------------------------------------------------------------

    def _send_command(self, command: int, body: bytes = b"") -> None:
        if not self._sock:
            raise SpyServerError("Not connected")
        try:
            self._sock.sendall(struct.pack("<II", command, len(body)) + body)
        except OSError as e:
            self._logger.error(f"Failed sending command {command}: {e}")
            self._cleanup()
            raise SpyServerError(f"Socket error while sending command {command}: {e}")

    def set_setting(self, setting: int, value: int) -> None:
        self._send_command(CMD_SET_SETTING, struct.pack("<II", setting, value))

    def _read_exact(self, count: int) -> bytes:
        assert self._sock is not None
        buf = bytearray()
        while len(buf) < count:
            try:
                chunk = self._sock.recv(count - len(buf))
            except socket.timeout as e:
                raise SpyServerError(f"Timed out reading from SpyServer ({len(buf)}/{count} bytes)")
            if not chunk:
                raise SpyServerError("SpyServer closed the connection")
            buf.extend(chunk)
        return bytes(buf)

    def _read_message(self):
        """Read one full message (header + body). Returns the parsed header."""
        header = self._read_exact(20)
        proto_id, msg_type, stream_type, seq, body_size = struct.unpack("<IIIII", header)
        if body_size > MAX_MESSAGE_BODY_SIZE:
            raise SpyServerError(f"Illegal message body size {body_size}")
        body = self._read_exact(body_size) if body_size else b""
        return proto_id, msg_type, stream_type, seq, body

    @staticmethod
    def _parse_device_info(body: bytes) -> Dict[str, Any]:
        if len(body) < 48:
            raise SpyServerError(f"DEVICE_INFO body too short ({len(body)} bytes)")
        fields = struct.unpack("<12I", body[:48])
        return {
            "device_type": fields[0],
            "device_serial": fields[1],
            "maximum_sample_rate": fields[2],
            "maximum_bandwidth": fields[3],
            "decimation_stage_count": fields[4],
            "gain_stage_count": fields[5],
            "maximum_gain_index": fields[6],
            "minimum_frequency": fields[7],
            "maximum_frequency": fields[8],
            "resolution": fields[9],
            "minimum_iq_decimation": fields[10],
            "forced_iq_format": fields[11],
        }

    @staticmethod
    def _parse_client_sync(body: bytes) -> Dict[str, Any]:
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
        # Server builds send 9, 10 or 11 fields; parse whatever is present.
        count = min(len(body) // 4, len(names))
        fields = struct.unpack(f"<{count}I", body[: count * 4]) if count else ()
        return dict(zip(names, fields))

    # ------------------------------------------------------------------
    # Reader thread: decode stream messages into the sample buffer
    # ------------------------------------------------------------------

    def _reader_loop(self):
        try:
            self._sock.settimeout(READ_TIMEOUT)
            while not self._closed:
                proto_id, msg_type, stream_type, _seq, body = self._read_message()
                mtype = msg_type & 0xFFFF
                # High 16 bits of the message type carry the digital gain in dB.
                mflags = (msg_type >> 16) & 0xFFFF

                if mtype == MSG_UINT8_IQ:
                    samples = self._decode_uint8_iq(body, mflags)
                elif mtype == MSG_INT16_IQ:
                    samples = self._decode_int16_iq(body, mflags)
                elif mtype == MSG_FLOAT_IQ:
                    samples = self._decode_float_iq(body, mflags)
                elif mtype in (MSG_DEVICE_INFO, MSG_CLIENT_SYNC):
                    # Refresh state occasionally (gain changes from the server
                    # side or AGC adjustments show up here).
                    if mtype == MSG_DEVICE_INFO:
                        self.device_info = self._parse_device_info(body)
                    else:
                        with self._buffer_cond:
                            self.client_sync = self._parse_client_sync(body)
                            self._sync_count += 1
                            self._buffer_cond.notify_all()
                    continue
                elif mtype == MSG_PONG:
                    continue
                else:
                    # FFT/audio streams are not requested in IQ-only mode;
                    # ignore anything unexpected instead of failing the stream.
                    continue

                if samples is not None and len(samples):
                    fill = self._account_sequence(_seq, len(samples))
                    with self._buffer_cond:
                        if fill:
                            self._buffer.extend(bytes(fill * 8))
                        self._buffer.extend(samples.tobytes())
                        self._buffer_cond.notify_all()
        except (SpyServerError, OSError) as e:
            if not self._closed:
                self._logger.error(f"SpyServer reader stopped: {e}")
                self._reader_error = e
                with self._buffer_cond:
                    self._buffer_cond.notify_all()
        except Exception as e:  # noqa: BLE001 - reader must never die silently
            self._logger.exception("Unexpected SpyServer reader error")
            self._reader_error = e
            with self._buffer_cond:
                self._buffer_cond.notify_all()

    # Never zero-fill more than this many messages for one gap: a jump this
    # large is a server restart / counter reset, not dropped blocks.
    MAX_FILL_MESSAGES = 256

    def _account_sequence(self, seq: int, n_samples: int) -> int:
        """Update stream stats; return how many zero samples to insert."""
        stats = self.stream_stats
        stats["iq_messages"] += 1
        fill = 0
        now = time.monotonic()
        if self._stream_t0 is None:
            self._stream_t0 = now
        if self._last_iq_seq is not None:
            missing = (seq - self._last_iq_seq - 1) & 0xFFFFFFFF
            if 0 < missing <= self.MAX_FILL_MESSAGES:
                stats["gaps"] += 1
                stats["missing_messages"] += missing
                fill = missing * (self._last_iq_len or n_samples)
                # Sequence numbers are not a perfect sample clock (servers also
                # number non-IQ messages, block sizes vary): never let output
                # run ahead of wall-clock time at the nominal rate.
                rate = self._effective_sample_rate or 0.0
                if rate > 0:
                    budget = int((now - self._stream_t0) * rate * 1.02) - (
                        stats["samples"] + stats["zero_filled_samples"] + n_samples
                    )
                    fill = max(0, min(fill, budget))
                stats["zero_filled_samples"] += fill
        stats["samples"] += n_samples
        self._last_iq_seq = seq
        self._last_iq_len = n_samples
        return fill

    @staticmethod
    def _decode_uint8_iq(body: bytes, gain_db: int) -> np.ndarray:
        count = len(body) // 2
        raw = np.frombuffer(body, dtype=np.uint8, count=count * 2)
        # Scale matches SatDump: 1 / (gain * 128), I/Q are unsigned with 128 offset.
        scale = 1.0 / ((10.0 ** (gain_db / 20.0)) * 128.0)
        iq = (raw.astype(np.float32) - 128.0) * scale
        return (iq[0::2] + 1j * iq[1::2]).astype(np.complex64)

    @staticmethod
    def _decode_int16_iq(body: bytes, gain_db: int) -> np.ndarray:
        count = len(body) // 4
        raw = np.frombuffer(body, dtype="<i2", count=count * 2)
        scale = 1.0 / (32768.0 * (10.0 ** (gain_db / 20.0)))
        iq = raw.astype(np.float32) * scale
        return (iq[0::2] + 1j * iq[1::2]).astype(np.complex64)

    @staticmethod
    def _decode_float_iq(body: bytes, gain_db: int) -> np.ndarray:
        count = len(body) // 8
        raw = np.frombuffer(body, dtype="<f4", count=count * 2)
        gain = 10.0 ** (gain_db / 20.0)
        iq = raw.astype(np.float32) * gain
        return (iq[0::2] + 1j * iq[1::2]).astype(np.complex64)

    # ------------------------------------------------------------------
    # Stream configuration
    # ------------------------------------------------------------------

    def _select_iq_format(self) -> int:
        """Pick the best IQ format the device supports.

        The server advertises forced_iq_format (non-zero means the server only
        delivers that format); otherwise use the device's native width: 8-bit
        for RTL-SDR, 16-bit for Airspy devices.
        """
        forced = self.device_info.get("forced_iq_format", 0)
        if forced in (FORMAT_UINT8, FORMAT_INT16, FORMAT_FLOAT):
            return forced
        if self.device_info.get("device_type") == DEVICE_RTLSDR:
            return FORMAT_UINT8
        return FORMAT_INT16

    def _choose_decimation(self, requested_rate: float) -> int:
        """Return the decimation stage whose rate is closest below the request.

        SpyServer decimation is log2: stage d streams at maximum_sample_rate /
        2^d. We pick the fastest rate that does not exceed the requested rate
        (a public server should not be asked for more bandwidth than the user
        asked for); if nothing fits, use the native rate.
        """
        max_rate = self.device_info.get("maximum_sample_rate", 0)
        stages = self.device_info.get("decimation_stage_count", 0)
        # Servers may enforce a minimum decimation (e.g. HF+ builds use 2);
        # requesting less decimation than that is rejected or ignored.
        min_decimation = int(self.device_info.get("minimum_iq_decimation", 0) or 0)
        if not max_rate:
            return 0
        if requested_rate <= 0:
            return 0
        best = None
        for d in range(min_decimation, stages + 1):
            rate = max_rate / (2**d)
            if rate <= requested_rate:
                best = d
                break
        # Nothing at or below the request: fall back to the slowest stream the
        # device offers rather than pushing a public server to full rate.
        return best if best is not None else max(stages, min_decimation)

    def start_stream(self) -> None:
        """Configure and enable the IQ stream.

        Order matters on real servers: mode -> format -> frequency ->
        decimation -> enable (the order SDR#/SatDump use). Sending frequency
        and decimation before the streaming mode makes several public servers
        move only the IQ window label while the hardware stays on its old
        frequency (verified live: device_center stayed at 100 MHz while
        iq_center said 145.935 MHz), so the recording holds the wrong band.
        """
        self._iq_format = self._select_iq_format()
        self.set_setting(SETTING_STREAMING_MODE, STREAM_MODE_IQ_ONLY)
        self.set_setting(SETTING_IQ_FORMAT, self._iq_format)
        if self._center_freq is not None:
            self.set_setting(SETTING_IQ_FREQUENCY, int(self._center_freq))
        if self._decimation is not None:
            self.set_setting(SETTING_IQ_DECIMATION, self._decimation)
        self.set_setting(SETTING_STREAMING_ENABLED, 1)
        self._streaming = True

    def stop_stream(self) -> None:
        self._streaming = False
        self.set_setting(SETTING_STREAMING_ENABLED, 0)

    def wait_for_tuning(self, timeout: float = 5.0) -> Dict[str, Any]:
        """Wait for the server's CLIENT_SYNC after tuning and report it.

        Returns {"ok", "can_control", "device_center", "iq_center",
        "requested", "reason"}. ok=False means the server is not delivering
        the requested frequency (no control and target outside the window
        the current hardware tuning allows), so recording would be useless.
        """
        requested = int(self._center_freq or 0)
        deadline = time.monotonic() + timeout
        with self._buffer_cond:
            start = self._sync_count
            while time.monotonic() < deadline:
                sync = self.client_sync
                if self._sync_count > start and sync.get("iq_center_frequency") == requested:
                    break
                if self._reader_error is not None:
                    break
                self._buffer_cond.wait(timeout=max(0.05, deadline - time.monotonic()))
            sync = dict(self.client_sync)
            confirmed = sync.get("iq_center_frequency") == requested
            if confirmed:
                # Servers flush a backlog on connect and keep streaming the old
                # band until the retune lands. Anything buffered before this
                # CLIENT_SYNC may be the wrong frequency: drop it, and restart
                # sequence tracking so the flush is not counted as a gap.
                self.stream_stats["discarded_pre_tune_samples"] = (
                    self.stream_stats.get("discarded_pre_tune_samples", 0) + len(self._buffer) // 8
                )
                self._buffer.clear()
                self._last_iq_seq = None
                self._stream_t0 = None
                self.stream_stats["samples"] = 0
                self.stream_stats["zero_filled_samples"] = 0
        iq_center = sync.get("iq_center_frequency")
        report = {
            "ok": True,
            "can_control": sync.get("can_control"),
            "device_center": sync.get("device_center_frequency"),
            "iq_center": iq_center,
            "requested": requested,
            "reason": "",
        }
        if not sync:
            report["reason"] = "server sent no CLIENT_SYNC; tuning unverified"
            return report
        if iq_center is not None and requested and abs(int(iq_center) - requested) > 1000:
            report["ok"] = False
            report["reason"] = (
                f"server streams {iq_center} Hz instead of {requested} Hz "
                f"(can_control={sync.get('can_control')}, device at "
                f"{sync.get('device_center_frequency')} Hz)"
            )
        return report

    # ------------------------------------------------------------------
    # Device control (properties mirror RtlSdrTcpClient)
    # ------------------------------------------------------------------

    @property
    def center_freq(self) -> Optional[float]:
        return self._center_freq

    @center_freq.setter
    def center_freq(self, freq_hz: float) -> None:
        freq_int = int(freq_hz)
        dev = self.device_info
        freq_min = dev.get("minimum_iq_center_frequency") or dev.get("minimum_frequency")
        freq_max = dev.get("maximum_iq_center_frequency") or dev.get("maximum_frequency")
        # Frequency 0 is a valid minimum (HF devices); only skip the check when
        # the fields are genuinely absent (None).
        if freq_min is not None and freq_max is not None and not (freq_min <= freq_int <= freq_max):
            raise SpyServerError(
                f"Center frequency {freq_int} Hz outside device range {freq_min}-{freq_max} Hz"
            )
        self._center_freq = float(freq_int)
        # Before streaming, start_stream() sends it in the order servers need.
        if self._streaming:
            self.set_setting(SETTING_IQ_FREQUENCY, freq_int)
        self._logger.info(f"Set IQ center frequency to {freq_int} Hz")

    @property
    def sample_rate(self) -> Optional[float]:
        return self._effective_sample_rate

    @sample_rate.setter
    def sample_rate(self, rate_sps: float) -> None:
        decimation = self._choose_decimation(rate_sps)
        max_rate = self.device_info.get("maximum_sample_rate", 0)
        self._decimation = decimation
        # Before streaming, start_stream() sends it in the order servers need.
        if self._streaming:
            self.set_setting(SETTING_IQ_DECIMATION, decimation)
        self._effective_sample_rate = (
            max_rate / (2**decimation) if max_rate else float(rate_sps)
        )
        self._logger.info(
            f"Set IQ decimation {decimation} (effective sample rate {self._effective_sample_rate})"
        )

    @property
    def gain(self) -> Optional[float]:
        # SpyServer exposes a gain index, not dB. The stored index is returned
        # as-is; see the setter for the mapping caveat.
        return self._gain_index

    @gain.setter
    def gain(self, gain_db: float) -> None:
        """Set device gain.

        SpyServer's SETTING_GAIN takes a device gain index. Airspy index steps
        are ~3 dB near the top of the range, so approximate the requested dB by
        scaling across the advertised index range. RTL-SDR (device type 3) has
        no analog gain index on SpyServer: tuner AGC applies instead.
        """
        dev = self.device_info
        if dev.get("device_type") == DEVICE_RTLSDR:
            self._logger.info("RTL-SDR on SpyServer has no manual gain index; skipping gain set")
            return
        max_index = dev.get("maximum_gain_index", 0)
        if not max_index:
            return
        # UI forms and stored configs pass the gain as a string ("21.1");
        # coerce so numeric comparisons never see str vs float.
        try:
            gain_db = float(gain_db)
        except (TypeError, ValueError):
            self._logger.warning(f"Ignoring non-numeric gain value: {gain_db!r}")
            return
        # Airspy R2 spans roughly 0-22 index (~0-65 dB).
        index = int(round(max_index * min(max(gain_db, 0.0), 65.0) / 65.0))
        index = max(0, min(index, max_index))
        self.set_setting(SETTING_GAIN, index)
        self._gain_index = index
        self._logger.info(f"Set gain index {index}/{max_index} (requested {gain_db} dB)")

    def set_agc_mode(self, enable: bool) -> None:
        """Toggle the server-side hardware AGC by zeroing/manual gain.

        SpyServer has no explicit AGC setting; SDR# models AGC by leaving gain
        at the AGC-chosen index. We approximate: disabling AGC re-applies the
        last manual index; enabling it simply stops touching the gain, letting
        the server keep its own value.
        """
        self._agc_enabled = enable
        if enable:
            self._logger.info("Server-side AGC: gain left under server control")
        elif self._gain_index is not None:
            self.set_setting(SETTING_GAIN, self._gain_index)

    def set_digital_gain_db(self, gain_db: float) -> None:
        """Set the SpyServer digital gain (available in 0.5 dB-ish steps)."""
        # SETTING_IQ_DIGITAL_GAIN expects hundredths of dB in server builds
        # that support it; clamp to a sane window.
        value = int(round(max(min(gain_db, 50.0), 0.0) * 100))
        self.set_setting(SETTING_IQ_DIGITAL_GAIN, value)

    def ping(self) -> None:
        self._send_command(CMD_PING)

    # ------------------------------------------------------------------
    # Sample consumption
    # ------------------------------------------------------------------

    def read_samples(self, num_samples: int = 16384, timeout: float = 10.0) -> np.ndarray:
        """Block until num_samples complex64 samples are buffered.

        Raises SpyServerError if the reader thread hit a connection error or
        the timeout elapsed without enough data (usually means the server has
        no slots left or the link is saturated).
        """
        needed = num_samples * 8  # complex64 -> 8 bytes per sample
        deadline = time.monotonic() + timeout
        with self._buffer_cond:
            while len(self._buffer) < needed:
                if self._reader_error is not None:
                    raise SpyServerError(f"SpyServer stream failed: {self._reader_error}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SpyServerError(
                        f"Timed out waiting for {num_samples} samples from SpyServer"
                    )
                self._buffer_cond.wait(timeout=remaining)
            raw = bytes(self._buffer[:needed])
            del self._buffer[:needed]
        return np.frombuffer(raw, dtype=np.complex64).copy()

    def close(self) -> None:
        self._closed = True
        if self._sock:
            try:
                self.stop_stream()
            except Exception:  # noqa: BLE001 - best effort on shutdown
                pass
        self._cleanup()
        with self._buffer_cond:
            self._buffer_cond.notify_all()

    @property
    def is_connected(self) -> bool:
        return self._connected and self._sock is not None

    @property
    def device_type_name(self) -> str:
        return DEVICE_TYPE_NAMES.get(self.device_info.get("device_type", 0), "Unknown")
