import queue
import threading
from types import SimpleNamespace

from hardware import uhdprobe
from workers import uhdworker


def test_worker_reports_discovered_serials_when_requested_one_is_missing(monkeypatch):
    found = [SimpleNamespace(to_string=lambda: "type=b200,serial=ABCD,addr=192.0.2.1")]
    monkeypatch.setattr(uhdworker, "uhd", SimpleNamespace(find=lambda _args: found))
    monkeypatch.setattr(uhdworker.time, "sleep", lambda _seconds: None)
    config_queue = queue.Queue()
    config_queue.put({"sdr_id": "usrp", "client_id": "viewer", "serial_number": "ABC"})
    data_queue = queue.Queue()

    uhdworker.uhd_worker_process(config_queue, data_queue, threading.Event())

    error = data_queue.get_nowait()
    assert error["type"] == "error"
    assert "UHD selecting device failed (serial=ABC)" in error["message"]
    assert "1 discovered device(s); available serials: ABCD" in error["message"]


def test_probe_reports_discovered_serials_when_requested_one_is_missing(monkeypatch):
    found = [SimpleNamespace(to_string=lambda: "type=b200,serial=ABCD,addr=192.0.2.1")]
    monkeypatch.setattr(uhdprobe, "uhd", SimpleNamespace(find=lambda _args: found))

    reply = uhdprobe.probe_local_uhd_usrp({"serial": "ABC"})

    assert reply["success"] is False
    assert "UHD selecting device failed (serial=ABC)" in reply["error"]
    assert "1 discovered device(s); available serials: ABCD" in reply["error"]


def test_worker_open_failure_keeps_native_exception(monkeypatch):
    def fail_open(_args):
        raise RuntimeError("No devices found for ----->\nDevice Address:")

    monkeypatch.setattr(
        uhdworker, "uhd", SimpleNamespace(usrp=SimpleNamespace(MultiUSRP=fail_open))
    )
    monkeypatch.setattr(uhdworker.time, "sleep", lambda _seconds: None)
    config_queue = queue.Queue()
    config_queue.put({"sdr_id": "usrp", "client_id": "viewer"})
    data_queue = queue.Queue()

    uhdworker.uhd_worker_process(config_queue, data_queue, threading.Event())

    error = data_queue.get_nowait()
    assert "UHD opening device failed" in error["message"]
    assert "RuntimeError: No devices found for ----->\nDevice Address:" in error["message"]
