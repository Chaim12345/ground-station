from types import SimpleNamespace

import pytest

from hardware import airspynative


def test_serial_open_error_includes_native_name_and_requested_serial(monkeypatch):
    lib = SimpleNamespace(
        airspy_open_sn=lambda _handle, _serial: -5,
        airspy_error_name=lambda _code: b"AIRSPY_ERROR_NOT_FOUND",
    )
    monkeypatch.setattr(airspynative, "_get_airspy_lib", lambda: lib)

    with pytest.raises(
        airspynative.AirspyNativeError,
        match=r"serial 0000000000001234 failed: AIRSPY_ERROR_NOT_FOUND \(code -5\)",
    ):
        airspynative.AirspyDevice.open("1234")
