"""Scoped API token behavior across the HTTP and account boundaries."""

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from common import apitokens, auth
from db.models import AuthSessions, UserRole, Users
from server import startup


@pytest.fixture
def token_store(db_engine, monkeypatch):
    maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(auth, "AsyncSessionLocal", maker)
    monkeypatch.setattr(apitokens, "AsyncSessionLocal", maker)
    monkeypatch.setattr(startup, "AsyncSessionLocal", maker)
    return maker


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


@pytest.mark.asyncio
async def test_token_is_shown_once_scoped_and_revoked(token_store):
    owner_id, browser_secret = await _make_user(token_store, "owner")
    headers = {"Origin": "http://testserver"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=startup.app), base_url="http://testserver"
    ) as client:
        client.cookies.set(auth.AUTH_SESSION_COOKIE_NAME, browser_secret)
        created = await client.post(
            "/api/auth/tokens",
            headers=headers,
            json={
                "name": "Home Assistant",
                "password": "password123",
                "scopes": ["station:read"],
                "expires_in_days": 365,
            },
        )
        assert created.status_code == 201
        secret = created.json()["token"]
        token_id = created.json()["data"]["id"]
        assert secret.startswith(apitokens.TOKEN_PREFIX)
        assert created.headers["cache-control"] == "no-store"

        listed = await client.get("/api/auth/tokens")
        assert listed.status_code == 200
        assert listed.json()["data"][0]["id"] == token_id
        assert secret not in listed.text

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

        # PATs never authenticate as browser sessions or enter legacy commands.
        client.cookies.clear()
        assert (await client.get("/api/auth/tokens", headers=bearer)).status_code == 401
        assert await auth.authenticate_token(secret) is None
        client.cookies.set(auth.AUTH_SESSION_COOKIE_NAME, browser_secret)

        revoked = await client.delete(f"/api/auth/tokens/{token_id}", headers=headers)
        assert revoked.status_code == 200
        assert (await client.get("/api/v1/me", headers=bearer)).status_code == 401


@pytest.mark.asyncio
async def test_token_ownership_and_origin(token_store):
    owner_id, owner_cookie = await _make_user(token_store, "owner")
    _, other_cookie = await _make_user(token_store, "other")
    result = await apitokens.create_token(owner_id, "password123", "Automation", ["station:read"])
    token_id = result["data"]["id"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=startup.app), base_url="http://testserver"
    ) as client:
        client.cookies.set(auth.AUTH_SESSION_COOKIE_NAME, other_cookie)
        assert (await client.get("/api/auth/tokens")).json()["data"] == []
        assert (
            await client.delete(
                f"/api/auth/tokens/{token_id}", headers={"Origin": "http://testserver"}
            )
        ).status_code == 404
        client.cookies.set(auth.AUTH_SESSION_COOKIE_NAME, owner_cookie)
        assert (
            await client.post(
                "/api/auth/tokens",
                headers={"Origin": "http://evil.example"},
                json={"name": "Bad", "password": "password123", "scopes": ["station:read"]},
            )
        ).status_code == 403
        assert (
            await client.post(
                "/api/auth/tokens",
                headers={"Origin": "http://testserver"},
                json={"name": "Bad", "password": "password123", "scopes": ["unknown:scope"]},
            )
        ).status_code == 400


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
