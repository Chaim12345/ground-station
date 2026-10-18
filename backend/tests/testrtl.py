import queue
import struct
import threading

import pytest

from workers import rtlsdrworker
from workers.rtlsdrtcpclient import RtlSdrTcpClient


@pytest.mark.parametrize("connection_type", ["usb", "tcp"])
def test_worker_open_failure_keeps_native_error_and_device_context(monkeypatch, connection_type):
    if connection_type == "usb":
        from rtlsdr.rtlsdr import LibUSBError

        native_error = LibUSBError(-6, "Could not open SDR")

        def fail_open(**_kwargs):
            raise native_error

        monkeypatch.setattr(rtlsdrworker.rtlsdr, "RtlSdr", fail_open)
        config = {"connection_type": "usb", "serial_number": "00000001"}
        expected = "RTL-SDR USB opening device failed (serial=00000001)"
        native_text = "LIBUSB_ERROR_BUSY (-6): Resource busy"
    else:
        native_error = ConnectionRefusedError(111, "Connection refused")

        class RefusedClient:
            def __init__(self, **_kwargs):
                pass

            def connect(self):
                raise native_error

        monkeypatch.setattr(rtlsdrworker, "RtlSdrTcpClient", RefusedClient)
        config = {"connection_type": "tcp", "host": "radio.local", "port": 1234}
        expected = "RTL-SDR TCP opening device failed (endpoint=radio.local:1234)"
        native_text = "ConnectionRefusedError: [Errno 111] Connection refused"

    monkeypatch.setattr(rtlsdrworker.time, "sleep", lambda _seconds: None)
    config_queue = queue.Queue()
    config_queue.put({"sdr_id": "rtl", "client_id": "viewer", **config})
    data_queue = queue.Queue()

    rtlsdrworker.rtlsdr_worker_process(config_queue, data_queue, threading.Event())

    error = data_queue.get_nowait()
    assert error["type"] == "error"
    assert expected in error["message"]
    assert native_text in error["message"]


def test_worker_read_failure_keeps_usb_code_and_serial(monkeypatch):
    from rtlsdr.rtlsdr import LibUSBError

    stop_event = threading.Event()

    class FailingDevice:
        def read_samples(self, _count):
            stop_event.set()
            raise LibUSBError(-4, "Could not read samples")

        def close(self):
            pass

    monkeypatch.setattr(rtlsdrworker.rtlsdr, "RtlSdr", lambda **_kwargs: FailingDevice())
    monkeypatch.setattr(rtlsdrworker.time, "sleep", lambda _seconds: None)
    config_queue = queue.Queue()
    config_queue.put(
        {
            "sdr_id": "rtl",
            "client_id": "viewer",
            "connection_type": "usb",
            "serial_number": "00000001",
        }
    )
    data_queue = queue.Queue()

    rtlsdrworker.rtlsdr_worker_process(config_queue, data_queue, stop_event)

    errors = [message for message in list(data_queue.queue) if message["type"] == "error"]
    assert len(errors) == 1
    assert "RTL-SDR USB reading samples failed (serial=00000001)" in errors[0]["message"]
    assert "LIBUSB_ERROR_NO_DEVICE (-4): No such device" in errors[0]["message"]


def test_tcp_client_rejects_wrong_protocol_header(monkeypatch):
    class WrongServer:
        def __init__(self):
            self.closed = False

        def settimeout(self, _timeout):
            pass

        def connect(self, _address):
            pass

        def recv(self, _size):
            return struct.pack("!4sII", b"HTTP", 0, 0)

        def close(self):
            self.closed = True

    socket = WrongServer()
    monkeypatch.setattr("workers.rtlsdrtcpclient.socket.socket", lambda *_args: socket)
    client = RtlSdrTcpClient("radio.local", 1234)

    with pytest.raises(ValueError, match="expected b'RTL0', received b'HTTP'"):
        client.connect()

    assert socket.closed
