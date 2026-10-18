import importlib
import queue
import threading
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    "module_name,function_name,config,expected_context,native_error",
    [
        (
            "workers.soapysdrlocalworker",
            "soapysdr_local_worker_process",
            {"sdr_id": "local", "client_id": "viewer", "driver": "rtlsdr", "serial_number": "ABC"},
            "driver=rtlsdr, serial=ABC",
            RuntimeError("No matching devices found"),
        ),
        (
            "workers.soapysdrremoteworker",
            "soapysdr_remote_worker_process",
            {
                "sdr_id": "remote",
                "client_id": "viewer",
                "host": "radio.local",
                "port": 55132,
                "driver": "rtlsdr",
                "serial_number": "ABC",
            },
            "endpoint=radio.local:55132, driver=rtlsdr, serial=ABC",
            ConnectionRefusedError("Connection refused"),
        ),
    ],
)
def test_soapy_worker_open_failure_preserves_native_text_and_device_context(
    monkeypatch, module_name, function_name, config, expected_context, native_error
):
    pytest.importorskip("SoapySDR")
    worker = importlib.import_module(module_name)

    def fail_open(_args):
        raise native_error

    monkeypatch.setattr(worker, "SoapySDR", SimpleNamespace(Device=fail_open))
    monkeypatch.setattr(worker.time, "sleep", lambda _seconds: None)
    config_queue = queue.Queue()
    config_queue.put(config)
    data_queue = queue.Queue()

    getattr(worker, function_name)(config_queue, data_queue, threading.Event())

    error = data_queue.get_nowait()
    assert error["type"] == "error"
    assert expected_context in error["message"]
    assert f"{type(native_error).__name__}: {native_error}" in error["message"]


@pytest.mark.parametrize(
    "module_name,function_name,details,expected_context",
    [
        (
            "hardware.soapysdrlocalprobe",
            "probe_local_soapy_sdr",
            {"driver": "rtlsdr", "serial": "ABC"},
            "driver=rtlsdr, serial=ABC",
        ),
        (
            "hardware.soapysdrremoteprobe",
            "probe_remote_soapy_sdr",
            {"host": "radio.local", "port": 55132, "driver": "rtlsdr", "serial": "ABC"},
            "endpoint=radio.local:55132, driver=rtlsdr, serial=ABC",
        ),
    ],
)
def test_soapy_probe_error_returns_device_context(
    monkeypatch, module_name, function_name, details, expected_context
):
    pytest.importorskip("SoapySDR")
    probe = importlib.import_module(module_name)

    def fail_open(_args):
        raise RuntimeError("No matching devices found")

    monkeypatch.setattr(probe, "SoapySDR", SimpleNamespace(Device=fail_open))

    reply = getattr(probe, function_name)(details)

    assert reply["success"] is False
    assert expected_context in reply["error"]
    assert "RuntimeError: No matching devices found" in reply["error"]
