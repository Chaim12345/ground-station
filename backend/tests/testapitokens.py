"""Personal token management over Socket.IO and scoped public HTTP access."""

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from common import apitokens, auth
from db.models import (
    AuthSessions,
    MonitoredSatellites,
    ObservationStatus,
    Satellites,
    ScheduledObservations,
    UserRole,
    Users,
)
from handlers import socket as sockethandlers
from server import startup


class FakeSio:
    def __init__(self):
        self.handlers = {}
        self.disconnected = []

    def on(self, event):
        def register(handler):
            self.handlers[event] = handler
            return handler

        return register

    async def disconnect(self, sid):
        self.disconnected.append(sid)


@pytest.fixture
def token_store(db_engine, monkeypatch):
    maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(auth, "AsyncSessionLocal", maker)
    monkeypatch.setattr(apitokens, "AsyncSessionLocal", maker)
    monkeypatch.setattr(startup, "AsyncSessionLocal", maker)

    async def setup_not_required():
        return False

    monkeypatch.setattr(auth, "is_setup_required", setup_not_required)
    sockethandlers.SOCKET_TOKENS.clear()
    sockethandlers.SOCKET_AUTH.clear()
    yield maker
    sockethandlers.SOCKET_TOKENS.clear()
    sockethandlers.SOCKET_AUTH.clear()


async def _make_user(maker, username: str) -> tuple[str, str]:
    browser_secret = f"browser-{username}"
    async with maker() as session:
        user = Users(
            username=username,
            username_norm=username,
            password_hash=auth.hash_password("password123"),
            role=UserRole.OPERATOR.value,
            is_active=True,
        )
        session.add(user)
        await session.flush()
        session.add(
            AuthSessions(
                user_id=user.id,
                token_hash=auth._hash_token(browser_secret),
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        await session.commit()
        return str(user.id), browser_secret


def _socket_call(sio, sid, cmd, data=None):
    return sio.handlers["api.call"](sid, {"cmd": cmd, "data": data})


@pytest.mark.asyncio
async def test_token_is_shown_once_scoped_and_revoked(token_store):
    owner_id, browser_secret = await _make_user(token_store, "owner")
    sio = FakeSio()
    sockethandlers.register_socketio_handlers(sio)
    sockethandlers.SOCKET_TOKENS["owner"] = browser_secret

    scopes = await _socket_call(sio, "owner", "tokens.scopes")
    assert scopes["success"] is True
    assert "station:read" in {scope["id"] for scope in scopes["data"]}
    assert (await _socket_call(sio, "owner", "tokens.list"))["data"] == []

    wrong_password = await _socket_call(
        sio,
        "owner",
        "tokens.create",
        {"name": "Bad", "password": "wrong", "scopes": ["station:read"]},
    )
    assert wrong_password["error"] == "Incorrect password."
    created = await _socket_call(
        sio,
        "owner",
        "tokens.create",
        {"name": "Home Assistant", "password": "password123", "scopes": ["station:read"]},
    )
    assert created["success"] is True
    secret = created["data"]["token"]
    token_id = created["data"]["data"]["id"]
    assert secret.startswith(apitokens.TOKEN_PREFIX)
    listed = await _socket_call(sio, "owner", "tokens.list")
    assert listed["data"][0]["id"] == token_id
    assert secret not in str(listed)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=startup.app), base_url="http://testserver"
    ) as client:
        bearer = {"Authorization": f"Bearer {secret}"}
        me = await client.get("/api/v1/me", headers=bearer)
        assert me.status_code == 200
        assert me.json()["user_id"] == owner_id
        assert me.json()["scopes"] == ["station:read"]
        assert (await client.get("/api/v1/station", headers=bearer)).status_code == 200
        assert (await client.get("/api/v1/observations", headers=bearer)).status_code == 403
        assert (await client.get("/api/v1/artifacts", headers=bearer)).status_code == 403
        assert (
            await client.get("/api/v1/me", headers={"Authorization": f"Bearer {browser_secret}"})
        ).status_code == 401
        assert (await client.get("/api/auth/tokens", headers=bearer)).status_code == 404

    # A PAT cannot be used as a browser session on api.call.
    sockethandlers.SOCKET_TOKENS["pat"] = secret
    denied = await _socket_call(sio, "pat", "tokens.list")
    assert denied["success"] is False
    assert sio.disconnected == ["pat"]

    renamed = await _socket_call(
        sio, "owner", "tokens.rename", {"token_id": token_id, "name": "Renamed"}
    )
    assert renamed["data"]["name"] == "Renamed"
    assert (await _socket_call(sio, "owner", "tokens.revoke", {"token_id": token_id}))[
        "success"
    ] is True
    assert (await _socket_call(sio, "owner", "tokens.list"))["data"][0]["status"] == "revoked"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=startup.app), base_url="http://testserver"
    ) as client:
        assert (await client.get("/api/v1/me", headers=bearer)).status_code == 401


@pytest.mark.asyncio
async def test_token_ownership_and_invalid_scopes(token_store):
    _, owner_cookie = await _make_user(token_store, "owner")
    _, other_cookie = await _make_user(token_store, "other")
    sio = FakeSio()
    sockethandlers.register_socketio_handlers(sio)
    sockethandlers.SOCKET_TOKENS.update({"owner": owner_cookie, "other": other_cookie})
    created = await _socket_call(
        sio,
        "owner",
        "tokens.create",
        {"name": "Automation", "password": "password123", "scopes": ["station:read"]},
    )
    token_id = created["data"]["data"]["id"]
    assert (await _socket_call(sio, "other", "tokens.list"))["data"] == []
    assert (
        await _socket_call(sio, "other", "tokens.rename", {"token_id": token_id, "name": "Stolen"})
    )["error"] == "API token not found."
    assert (await _socket_call(sio, "other", "tokens.revoke", {"token_id": token_id}))[
        "error"
    ] == "API token not found."
    invalid = await _socket_call(
        sio,
        "owner",
        "tokens.create",
        {"name": "Bad", "password": "password123", "scopes": ["unknown:scope"]},
    )
    assert invalid["error"] == "Unknown API token scope."


@pytest.mark.asyncio
async def test_expiry_and_password_reset_invalidate_tokens(token_store):
    owner_id, _ = await _make_user(token_store, "owner")
    created = await apitokens.create_token(owner_id, "password123", "Automation", ["station:read"])
    secret = created["token"]
    assert await apitokens.authenticate_token(secret)
    async with token_store() as session:
        from db.models import ApiTokens

        row = await session.get(ApiTokens, uuid.UUID(created["data"]["id"]))
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
    assert await apitokens.authenticate_token(secret) is None

    another = await apitokens.create_token(owner_id, "password123", "Second", ["station:read"])
    assert await apitokens.authenticate_token(another["token"])
    await auth.reset_user_password(owner_id, "replacement123")
    assert await apitokens.authenticate_token(another["token"]) is None


@pytest.mark.asyncio
async def test_public_status_upcoming_observations_and_satellites(token_store):
    public_spec = startup.app.openapi()
    for path in (
        "/api/v1/me",
        "/api/v1/status",
        "/api/v1/observations/upcoming",
        "/api/v1/satellites",
        "/api/v1/satellites/monitored",
    ):
        assert public_spec["paths"][path]["get"]["security"]

    owner_id, _ = await _make_user(token_store, "integrator")
    now = datetime.now(timezone.utc)
    async with token_store() as session:
        session.add_all(
            [
                Satellites(norad_id=25544, name="ISS", source="manual", status="active"),
                Satellites(norad_id=33591, name="NOAA 19", source="manual"),
                MonitoredSatellites(
                    id="iss-monitor",
                    norad_id=25544,
                    enabled=True,
                    satellite_config={"name": "ISS"},
                    hardware_config={},
                    generation_config={"min_elevation": 15},
                    sessions=[],
                ),
                MonitoredSatellites(
                    id="noaa-monitor",
                    norad_id=33591,
                    enabled=False,
                    satellite_config={"name": "NOAA 19"},
                    hardware_config={},
                    generation_config={},
                    sessions=[],
                ),
            ]
        )
        session.add_all(
            [
                ScheduledObservations(
                    id="running",
                    name="ISS now",
                    norad_id=25544,
                    enabled=True,
                    status=ObservationStatus.RUNNING,
                    event_start=now - timedelta(minutes=2),
                    event_end=now + timedelta(minutes=8),
                    satellite_config={"name": "ISS"},
                    pass_config={"peak_altitude": 70},
                    hardware_config={"rig": {"private": "configuration"}},
                    sessions=[],
                ),
                ScheduledObservations(
                    id="next-iss",
                    name="ISS next",
                    norad_id=25544,
                    enabled=True,
                    status=ObservationStatus.SCHEDULED,
                    event_start=now + timedelta(hours=2),
                    event_end=now + timedelta(hours=2, minutes=10),
                    satellite_config={"name": "ISS"},
                    pass_config={"peak_altitude": 45},
                    hardware_config={"rig": {"private": "configuration"}},
                    sessions=[],
                ),
                ScheduledObservations(
                    id="disabled",
                    name="NOAA disabled",
                    norad_id=33591,
                    enabled=False,
                    status=ObservationStatus.SCHEDULED,
                    event_start=now + timedelta(hours=1),
                    event_end=now + timedelta(hours=1, minutes=10),
                    satellite_config={"name": "NOAA 19"},
                    pass_config={},
                    hardware_config={},
                    sessions=[],
                ),
            ]
        )
        await session.commit()

    full = await apitokens.create_token(
        owner_id,
        "password123",
        "Home Assistant",
        ["station:read", "satellites:read", "observations:read"],
    )
    headers = {"Authorization": f"Bearer {full['token']}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=startup.app), base_url="http://testserver"
    ) as client:
        status = await client.get("/api/v1/status", headers=headers)
        assert status.status_code == 200
        assert status.json()["observation_state"] == "observing"
        assert status.json()["active_observation_count"] == 1
        assert status.json()["monitored_satellite_count"] == 1
        assert status.json()["next_observation_at"] == (now + timedelta(hours=2)).isoformat()

        upcoming = await client.get("/api/v1/observations/upcoming", headers=headers)
        assert upcoming.status_code == 200
        assert [item["id"] for item in upcoming.json()["data"]] == ["next-iss"]
        assert upcoming.json()["data"][0]["peak_altitude"] == 45
        assert "hardware_config" not in str(upcoming.json())
        assert "private" not in str(upcoming.json())
        assert (await client.get("/api/v1/observations/upcoming?hours=1", headers=headers)).json()[
            "data"
        ] == []
        assert (
            await client.get("/api/v1/observations/upcoming?norad_id=33591", headers=headers)
        ).json()["data"] == []
        assert (
            await client.get("/api/v1/observations/upcoming?hours=169", headers=headers)
        ).status_code == 422
        assert (
            await client.get("/api/v1/observations/upcoming?limit=101", headers=headers)
        ).status_code == 422
        detail = await client.get("/api/v1/observations/next-iss", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["data"]["id"] == "next-iss"
        assert (
            await client.get("/api/v1/observations/missing", headers=headers)
        ).status_code == 404
        assert (
            await client.get("/api/v1/observations/upcoming?norad_id=25544", headers=headers)
        ).json()["data"][0]["id"] == "next-iss"

        monitored = await client.get("/api/v1/satellites/monitored", headers=headers)
        assert monitored.status_code == 200
        assert [item["norad_id"] for item in monitored.json()["data"]] == [25544]
        assert monitored.json()["pagination"]["total"] == 1
        catalog = await client.get("/api/v1/satellites?query=ISS", headers=headers)
        assert [item["norad_id"] for item in catalog.json()["data"]] == [25544]
        assert catalog.json()["pagination"]["total"] == 1
        wildcard = await client.get("/api/v1/satellites?query=%25", headers=headers)
        assert wildcard.json()["data"] == []
        paged = await client.get("/api/v1/satellites?limit=1&offset=1", headers=headers)
        assert len(paged.json()["data"]) == 1
        assert paged.json()["pagination"]["total"] == 2
        assert (
            await client.get("/api/v1/satellites?limit=101", headers=headers)
        ).status_code == 422
        both = await client.get(
            "/api/v1/satellites/monitored?enabled_only=false&limit=1&offset=1", headers=headers
        )
        assert [item["norad_id"] for item in both.json()["data"]] == [33591]
        assert both.json()["pagination"]["total"] == 2
        satellite = await client.get("/api/v1/satellites/25544", headers=headers)
        assert satellite.json()["data"]["name"] == "ISS"
        assert (await client.get("/api/v1/satellites/99999", headers=headers)).status_code == 404

    station_only = await apitokens.create_token(
        owner_id, "password123", "station only", ["station:read"]
    )
    limited_headers = {"Authorization": f"Bearer {station_only['token']}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=startup.app), base_url="http://testserver"
    ) as client:
        assert (await client.get("/api/v1/status", headers=limited_headers)).status_code == 200
        assert (
            await client.get("/api/v1/observations/upcoming", headers=limited_headers)
        ).status_code == 403
        assert (
            await client.get("/api/v1/satellites/monitored", headers=limited_headers)
        ).status_code == 403
        assert (await client.get("/api/v1/satellites", headers=limited_headers)).status_code == 403
        assert (await client.get("/api/v1/status")).status_code == 401


@pytest.mark.asyncio
async def test_every_public_route_requires_a_pat_and_its_scope(token_store, monkeypatch):
    owner_id, browser_secret = await _make_user(token_store, "scope-matrix")
    full = await apitokens.create_token(
        owner_id,
        "password123",
        "All read scopes",
        ["station:read", "satellites:read", "observations:read", "artifacts:read"],
    )
    station_only = await apitokens.create_token(
        owner_id, "password123", "Station only", ["station:read"]
    )
    satellites_only = await apitokens.create_token(
        owner_id, "password123", "Satellites only", ["satellites:read"]
    )

    class InventoryStub:
        def query(self, *, page, page_size):
            return {
                "items": [{"id": "artifact-1", "name": "Recording"}],
                "total": 1,
                "page": page,
                "page_size": page_size,
                "revision": 1,
                "has_more": False,
                "diskUsage": 0,
            }

        def detail(self, item_id):
            return {"id": "artifact-1", "name": "Recording"} if item_id == "artifact-1" else None

    monkeypatch.setattr(startup, "get_inventory", lambda: InventoryStub())
    full_headers = {"Authorization": f"Bearer {full['token']}"}
    station_headers = {"Authorization": f"Bearer {station_only['token']}"}
    satellites_headers = {"Authorization": f"Bearer {satellites_only['token']}"}
    routes = (
        ("/api/v1/station", "station:read", 200),
        ("/api/v1/status", "station:read", 200),
        ("/api/v1/observations", "observations:read", 200),
        ("/api/v1/observations/upcoming", "observations:read", 200),
        ("/api/v1/observations/missing", "observations:read", 404),
        ("/api/v1/satellites", "satellites:read", 200),
        ("/api/v1/satellites/monitored", "satellites:read", 200),
        ("/api/v1/satellites/99999", "satellites:read", 404),
        ("/api/v1/artifacts?page=2&page_size=1", "artifacts:read", 200),
        ("/api/v1/artifacts/artifact-1", "artifacts:read", 200),
        ("/api/v1/artifacts/missing", "artifacts:read", 404),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=startup.app), base_url="http://testserver"
    ) as client:
        assert (await client.get("/api/v1/me")).status_code == 401
        assert (
            await client.get("/api/v1/me", cookies={auth.AUTH_SESSION_COOKIE_NAME: browser_secret})
        ).status_code == 401
        assert (await client.get("/api/v1/me", headers=full_headers)).json()["user_id"] == owner_id

        for path, required_scope, expected_status in routes:
            assert (await client.get(path)).status_code == 401, path
            wrong_headers = (
                satellites_headers if required_scope == "station:read" else station_headers
            )
            assert (await client.get(path, headers=wrong_headers)).status_code == 403, path
            response = await client.get(path, headers=full_headers)
            assert response.status_code == expected_status, path
            if path.startswith("/api/v1/artifacts") and expected_status == 200:
                assert "artifact-1" in response.text

        artifact_page = await client.get(
            "/api/v1/artifacts?page=2&page_size=1", headers=full_headers
        )
        assert artifact_page.json()["page"] == 2
        assert artifact_page.json()["page_size"] == 1
