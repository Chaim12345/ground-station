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
Tests for the SpyServer protocol client, remote probe and directory parsing.

A threaded mock SpyServer implements the protocol end used by the client so
the tests exercise the real wire format without any network beyond localhost.
"""

import json
import socket
import struct
import threading

import numpy as np
import pytest

from hardware import spyserverprobe
from workers.spyserverclient import (
    DEFAULT_PORT,
    PROTOCOL_VERSION,
    SETTING_IQ_DECIMATION,
    SETTING_IQ_FORMAT,
    SETTING_IQ_FREQUENCY,
    SETTING_STREAMING_ENABLED,
    SETTING_STREAMING_MODE,
    STREAM_MODE_IQ_ONLY,
    SpyServerClient,
)

# Airspy HF+ shaped device info: 912 kS/s max, two decimation stages
MOCK_DEVICE_INFO = {
    "device_type": 2,
    "device_serial": 1234,
    "maximum_sample_rate": 912000,
    "maximum_bandwidth": 768000,
    "decimation_stage_count": 2,
    "gain_stage_count": 0,
    "maximum_gain_index": 22,
    "minimum_frequency": 0,
    "maximum_frequency": 30000000,
    "resolution": 16,
    "minimum_iq_decimation": 1,
    "forced_iq_format": 0,
}

MSG_DEVICE_INFO = 0
MSG_UINT8_IQ = 100


class MockSpyServer(threading.Thread):
    """Minimal SpyServer: handshake, settings recording, uint8 IQ stream."""

    def __init__(self, samples_between=256):
        super().__init__(daemon=True)
        self.settings = {}
        self.setting_order = []
        self.drop_every = 0  # >0: skip a sequence number every N IQ messages
        self.streaming_enabled = threading.Event()
        self.stream_active = threading.Event()
        self.received_hello = None
        self.samples_between = samples_between
        self._seq = 0
        self._stop_requested = threading.Event()

    def run(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        self.port = server.getsockname()[1]
        self.ready.set()

        conn, _ = server.accept()
        self._serve(conn)
        server.close()

    def _serve(self, conn):
        try:
            while not self._stop_requested.is_set():
                command, body = self._read_command(conn)
                if command == 0:  # HELLO
                    self.received_hello = struct.unpack("<I", body[:4])[0]
                    self._send(conn, 0, 0, self._device_info_bytes())
                    self._send(conn, 1, 0, bytes(44))  # CLIENT_SYNC
                elif command == 2:  # SET_SETTING
                    setting, value = struct.unpack("<II", body[:8])
                    self.settings[setting] = value
                    self.setting_order.append(setting)
                    if setting == SETTING_IQ_FREQUENCY:
                        # Report the new tuning like real servers do.
                        sync = struct.pack(
                            "<11I", 1, 10, value, value, value, 0, 30_000_000, 0, 30_000_000, 1, 2
                        )
                        self._send(conn, 1, 0, sync)
                    if setting == SETTING_STREAMING_ENABLED and value:
                        self.streaming_enabled.set()
                elif command == 3:  # PING
                    self._send(conn, 2, 0, b"")
                if self.streaming_enabled.is_set() and not self.stream_active.is_set():
                    self.stream_active.set()
                    self._stream(conn)
        except OSError:
            pass
        finally:
            conn.close()

    def _stream(self, conn):
        while self.streaming_enabled.is_set() and not self._stop_requested.is_set():
            # Interleaved uint8 IQ ramp centred on 128
            n = self.samples_between
            raw = np.empty(n * 2, dtype=np.uint8)
            raw[0::2] = (np.arange(n) * 3 % 255)
            raw[1::2] = (np.arange(n) * 7 % 255)
            self._send(conn, MSG_UINT8_IQ | (0 << 16), 1, raw.tobytes())
            self._iq_count = getattr(self, "_iq_count", 0) + 1
            if self.drop_every and self._iq_count % self.drop_every == 0:
                self._seq += 2  # server dropped two messages for this client

    def _device_info_bytes(self):
        return struct.pack("<12I", *MOCK_DEVICE_INFO.values())

    def _send(self, conn, msg_type, stream_type, body):
        conn.sendall(
            struct.pack(
                "<IIIII", PROTOCOL_VERSION, msg_type, stream_type, self._seq, len(body)
            )
            + body
        )
        self._seq += 1

    @staticmethod
    def _read_exact(conn, count):
        buf = bytearray()
        while len(buf) < count:
            chunk = conn.recv(count - len(buf))
            if not chunk:
                raise OSError("closed")
            buf.extend(chunk)
        return bytes(buf)

    def _read_command(self, conn):
        header = self._read_exact(conn, 8)
        command, body_size = struct.unpack("<II", header)
        body = self._read_exact(conn, body_size) if body_size else b""
        return command, body

    def stop(self):
        self._stop_requested.set()
        self.streaming_enabled.clear()


@pytest.fixture
def mock_server():
    server = MockSpyServer()
    server.ready = threading.Event()
    server.start()
    assert server.ready.wait(5), "mock server did not start"
    yield server
    server.stop()


def wait_for_setting(server: MockSpyServer, setting: int, timeout: float = 5.0):
    """Wait until the mock server has recorded a setting (async wire race)."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if setting in server.settings:
            return server.settings[setting]
        time.sleep(0.01)
    pytest.fail(f"Mock server never recorded setting {setting}")


def test_connect_receives_device_info(mock_server):
    client = SpyServerClient("127.0.0.1", mock_server.port)
    assert client.connect() is True
    assert mock_server.received_hello == PROTOCOL_VERSION
    assert client.device_info["maximum_sample_rate"] == 912000
    assert client.device_type_name == "Airspy HF+"
    client.close()


def test_start_stream_sends_expected_settings(mock_server):
    client = SpyServerClient("127.0.0.1", mock_server.port)
    client.connect()
    client.center_freq = 10_000_000
    client.sample_rate = 500_000  # one decimation stage: 912k -> 456k <= request
    client.start_stream()

    assert wait_for_setting(mock_server, SETTING_STREAMING_MODE) == STREAM_MODE_IQ_ONLY
    assert wait_for_setting(mock_server, SETTING_IQ_FREQUENCY) == 10_000_000
    assert wait_for_setting(mock_server, SETTING_IQ_DECIMATION) == 1
    assert wait_for_setting(mock_server, SETTING_IQ_FORMAT) == 2  # INT16 for 16-bit device
    assert wait_for_setting(mock_server, SETTING_STREAMING_ENABLED) == 1
    client.close()


def test_stream_settings_sent_in_order_servers_require(mock_server):
    """Real servers only retune hardware with mode -> fmt -> freq -> dec -> on."""
    client = SpyServerClient("127.0.0.1", mock_server.port)
    client.connect()
    client.sample_rate = 200_000  # configured before streaming: deferred
    client.center_freq = 10_000_000
    assert mock_server.setting_order == []  # nothing sent before start_stream
    client.start_stream()
    wait_for_setting(mock_server, SETTING_STREAMING_ENABLED)
    assert mock_server.setting_order == [
        SETTING_STREAMING_MODE,
        SETTING_IQ_FORMAT,
        SETTING_IQ_FREQUENCY,
        SETTING_IQ_DECIMATION,
        SETTING_STREAMING_ENABLED,
    ]
    report = client.wait_for_tuning(timeout=5)
    assert report["ok"] is True
    assert report["iq_center"] == 10_000_000
    client.close()


def test_retune_while_streaming_is_sent_immediately():
    client = SpyServerClient("127.0.0.1")
    client.device_info = dict(MOCK_DEVICE_INFO)
    sent = []
    client.set_setting = lambda setting, value: sent.append((setting, value))
    client.center_freq = 10_000_000
    assert sent == []  # deferred until start_stream
    client._streaming = True
    client.center_freq = 12_000_000
    assert sent == [(SETTING_IQ_FREQUENCY, 12_000_000)]


def test_sequence_gaps_are_zero_filled_and_counted():
    client = SpyServerClient("127.0.0.1")
    assert client._account_sequence(10, 100) == 0
    assert client._account_sequence(11, 100) == 0
    # two messages missing -> two messages worth of zero samples
    assert client._account_sequence(14, 100) == 200
    assert client.stream_stats["gaps"] == 1
    assert client.stream_stats["missing_messages"] == 2
    assert client.stream_stats["zero_filled_samples"] == 200
    # huge jump = counter reset, never zero-filled
    assert client._account_sequence(1_000_000, 100) == 0
    # 32-bit wrap-around is a normal step
    client._last_iq_seq = 0xFFFFFFFF
    assert client._account_sequence(0, 100) == 0


def test_dropped_server_messages_keep_stream_timing(mock_server):
    mock_server.drop_every = 5
    client = SpyServerClient("127.0.0.1", mock_server.port)
    client.connect()
    client.start_stream()
    samples = client.read_samples(256 * 40, timeout=10)
    stats = client.stream_stats
    assert stats["gaps"] >= 1
    assert stats["zero_filled_samples"] == stats["missing_messages"] * 256
    # zero-filled blocks are present in the stream
    assert np.count_nonzero(samples == 0) >= 256
    client.close()


def test_client_sync_parses_short_bodies():
    sync = SpyServerClient._parse_client_sync(struct.pack("<9I", 1, 45, 1, 2, 3, 4, 5, 6, 7))
    assert sync["can_control"] == 1 and sync["iq_center_frequency"] == 2
    assert "iq_decimation" not in sync


def test_read_samples_returns_complex64(mock_server):
    client = SpyServerClient("127.0.0.1", mock_server.port)
    client.connect()
    client.start_stream()

    samples = client.read_samples(600, timeout=10)
    assert samples.dtype == np.complex64
    assert len(samples) == 600
    # uint8 IQ decodes per-component to +/-1.0, so magnitude stays <= sqrt(2)
    assert np.all(np.abs(samples.real) <= 1.0)
    assert np.all(np.abs(samples.imag) <= 1.0)
    client.close()


def test_center_freq_out_of_range_rejected(mock_server):
    client = SpyServerClient("127.0.0.1", mock_server.port)
    client.connect()
    with pytest.raises(Exception):
        client.center_freq = 40_000_000  # above the mock's 30 MHz maximum
    client.close()


def test_decimation_picker_prefers_full_rate():
    client = SpyServerClient("127.0.0.1")
    client.device_info = dict(MOCK_DEVICE_INFO)  # minimum_iq_decimation=1
    # Server enforces a decimation floor of 1, so full rate is never picked
    assert client._choose_decimation(912_000) == 1
    assert client._choose_decimation(500_000) == 1
    assert client._choose_decimation(200_000) == 2
    assert client._choose_decimation(2_000_000) == 1  # never above native rate
    # No floor advertised -> full rate is allowed again
    client.device_info["minimum_iq_decimation"] = 0
    assert client._choose_decimation(912_000) == 0
    assert client._choose_decimation(500_000) == 1


def test_probe_server_against_mock(mock_server):
    result = spyserverprobe.probe_server("127.0.0.1", mock_server.port, timeout=5)
    assert result["success"] is True
    info = result["device_info"]
    assert info["device_type_name"] == "Airspy HF+"
    assert info["maximum_sample_rate"] == 912000
    assert info["minimum_frequency"] == 0
    assert info["maximum_frequency"] == 30000000


def test_probe_server_unreachable():
    # Port 1 on localhost is never our mock; expect a clean failure dict
    result = spyserverprobe.probe_server("127.0.0.1", 1, timeout=1)
    assert result["success"] is False
    assert result["error"]


def test_directory_fetch_and_sdr_record(monkeypatch):
    directory_payload = {
        "servers": [
            {
                "streamingHost": "203.0.113.7",
                "streamingPort": 5555,
                "online": True,
                "deviceType": "AirspyHF",
                "ownerName": "Test Owner",
                "antennaType": "Vertical",
                "generalDescription": "test",
                "antennaLocation": {"lat": 52.0, "long": 4.9},
                "maxClients": 3,
                "currentClientCount": 1,
                "maximumIQSampleRate": 912000,
                "currentCenterFrequency": 14100000,
                "serverVersion": "2.0.1922",
                "operatingSystem": "Linux",
                "lastSeen": 2,
            },
            {"streamingHost": "", "online": True},  # skipped: no host
        ]
    }

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return directory_payload

    monkeypatch.setattr(spyserverprobe.requests, "get", lambda *a, **k: FakeResponse())

    entries = spyserverprobe.fetch_public_directory()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["host"] == "203.0.113.7"
    assert entry["port"] == 5555
    assert entry["device_type"] == "Airspy HF+"

    # Merge the probe result in, exactly what the discovery task does before
    # building an SDR record.
    entry["device_info"] = {
        "device_type_name": "Airspy HF+",
        "minimum_frequency": 0,
        "maximum_frequency": 30000000,
    }
    record = spyserverprobe.build_sdr_record(entry, name="My remote HF")
    # SDRs table stores frequencies in MHz
    assert record["type"] == "spyserver"
    assert record["host"] == "203.0.113.7"
    assert record["port"] == 5555
    assert record["frequency_min"] == 0
    assert record["frequency_max"] == 30
    assert record["name"] == "My remote HF"
    assert json.dumps(record)  # payload is JSON-serializable for the API
