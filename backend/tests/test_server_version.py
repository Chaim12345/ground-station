import logging
from datetime import datetime

import pytest

from handlers.entities import systeminfo
from server import version
from server.version import get_full_version_info, get_system_info


def test_get_full_version_info_includes_server_time_fields():
    info = get_full_version_info()

    assert "serverTimeEpochMs" in info
    assert "serverTimeIsoUtc" in info
    assert isinstance(info["serverTimeEpochMs"], int)
    assert isinstance(info["serverTimeIsoUtc"], str)

    parsed_iso = datetime.fromisoformat(info["serverTimeIsoUtc"])
    parsed_iso_epoch_ms = int(parsed_iso.timestamp() * 1000)

    # Allow small drift between independently generated values.
    assert abs(parsed_iso_epoch_ms - info["serverTimeEpochMs"]) < 5000


def test_get_system_info_includes_footer_host_details():
    info = get_system_info(nonblocking_cpu=True)

    assert isinstance(info["hostname"], str)
    assert isinstance(info["uptime_seconds"], int)
    assert info["uptime_seconds"] >= 0
    assert "pretty_name" in info["os"]


@pytest.mark.unit
async def test_update_check_returns_and_caches_release_status(monkeypatch):
    calls = 0

    async def fetch_release():
        nonlocal calls
        calls += 1
        return {
            "tag_name": "v1.3.0",
            "html_url": "https://github.com/sgoudelis/ground-station/releases/tag/v1.3.0",
            "published_at": "2026-10-07T10:00:00Z",
        }

    monkeypatch.setattr(version, "_fetch_latest_release", fetch_release)
    monkeypatch.setattr(version, "get_version_base", lambda: "1.2.0")
    monkeypatch.setattr(
        version,
        "_update_check_cache",
        {"timestamp": 0.0, "data": None, "error": None},
    )

    result = await version.get_update_check()
    cached_result = await version.get_update_check()

    assert result["currentVersion"] == "1.2.0"
    assert result["latestVersion"] == "1.3.0"
    assert result["isUpdateAvailable"] is True
    assert result["checkedAt"]
    assert cached_result == result
    assert calls == 1


@pytest.mark.unit
async def test_update_check_surfaces_and_briefly_caches_failures(monkeypatch):
    calls = 0

    async def fetch_release():
        nonlocal calls
        calls += 1
        raise RuntimeError("GitHub unavailable")

    monkeypatch.setattr(version, "_fetch_latest_release", fetch_release)
    monkeypatch.setattr(
        version,
        "_update_check_cache",
        {"timestamp": 0.0, "data": None, "error": None},
    )

    with pytest.raises(version.UpdateCheckError, match="GitHub unavailable"):
        await version.get_update_check()
    with pytest.raises(version.UpdateCheckError, match="GitHub unavailable"):
        await version.get_update_check()

    assert calls == 1

    with pytest.raises(version.UpdateCheckError, match="GitHub unavailable"):
        await version.get_update_check(force_refresh=True)

    assert calls == 2


@pytest.mark.unit
async def test_socket_update_check_respects_refresh_and_returns_errors(monkeypatch):
    refresh_values = []

    async def fake_update_check(*, force_refresh=False):
        refresh_values.append(force_refresh)
        return {"latestVersion": "1.3.0"}

    monkeypatch.setattr(systeminfo, "get_update_check", fake_update_check)
    logger = logging.getLogger(__name__)

    cached = await systeminfo.fetch_update_check(None, None, logger, "sid")
    refreshed = await systeminfo.fetch_update_check(None, {"refresh": True}, logger, "sid")

    assert cached == {"success": True, "data": {"latestVersion": "1.3.0"}}
    assert refreshed == cached
    assert refresh_values == [False, True]

    async def failed_update_check(*, force_refresh=False):
        raise version.UpdateCheckError("GitHub unavailable")

    monkeypatch.setattr(systeminfo, "get_update_check", failed_update_check)
    failed = await systeminfo.fetch_update_check(None, None, logger, "sid")
    assert failed == {"success": False, "error": "GitHub unavailable"}


@pytest.mark.unit
async def test_socket_version_info_includes_clock_sample():
    reply = await systeminfo.fetch_version_info(None, None, None, "sid")

    assert reply["success"] is True
    assert reply["data"]["version"]
    assert isinstance(reply["data"]["serverTimeEpochMs"], int)
