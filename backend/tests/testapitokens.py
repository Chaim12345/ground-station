"""Personal token management over Socket.IO and scoped public HTTP access."""

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from common import apitokens, auth
from db.models import AuthSessions, UserRole, Users
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
