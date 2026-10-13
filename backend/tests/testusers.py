"""Admin user management through the Socket.IO command dispatcher."""

import logging
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from common import auth
from db.models import AuthSessions, UserRole, Users
from handlers.entities import users
from handlers.routing import dispatch_request
from handlers.routing.registry import HandlerRegistry
from server import startup


@pytest.fixture
def user_commands(db_engine, monkeypatch):
    maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(auth, "AsyncSessionLocal", maker)

    async def no_setup(force_refresh=False):
        del force_refresh
        return False

    monkeypatch.setattr(auth, "is_setup_required", no_setup)
    registry = HandlerRegistry()
    users.register_handlers(registry)
    return maker, registry


async def _call(registry, command, data, context):
    return await dispatch_request(
        None, command, data, logging.getLogger(__name__), "sid-1", registry, auth_context=context
    )


@pytest.mark.asyncio
async def test_admin_user_commands_reuse_account_rules(user_commands):
    maker, registry = user_commands
    async with maker() as session:
        admin = Users(
            username="admin",
            username_norm="admin",
            password_hash=auth.hash_password("password123"),
            role=UserRole.ADMIN.value,
            is_active=True,
        )
        session.add(admin)
        await session.commit()
        admin_id = str(admin.id)

    context = {"user_id": admin_id, "role": "admin"}
    created = await _call(
        registry,
        "users.create",
        {"username": "operator", "password": "password123", "role": "operator"},
        context,
    )
    assert created["success"] is True
    user_id = created["data"]["id"]
    assert created["data"]["username"] == "operator"

    listed = await _call(registry, "users.list", None, context)
    assert [item["id"] for item in listed["data"]] == [admin_id, user_id]

    updated = await _call(
        registry,
        "users.update",
        {"user_id": user_id, "role": "operator", "is_active": False},
        context,
    )
    assert updated["success"] is True
    assert updated["data"]["is_active"] is False

    async with maker() as session:
        target = (
            await session.execute(select(Users).where(Users.username == "operator"))
        ).scalar_one()
        session.add(
            AuthSessions(
                user_id=target.id,
                token_hash=auth._hash_token("target-session"),
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        await session.commit()

    reset = await _call(
        registry,
        "users.reset_password",
        {"user_id": user_id, "password": "replacement123"},
        context,
    )
    assert reset["success"] is True
    assert await auth.authenticate_token("target-session") is None

    deleted = await _call(registry, "users.delete", {"user_id": user_id}, context)
    assert deleted["success"] is True
    assert deleted["data"]["id"] == user_id
    assert (await _call(registry, "users.delete", {"user_id": admin_id}, context))[
        "success"
    ] is False


@pytest.mark.asyncio
async def test_user_commands_require_admin_and_valid_payload(user_commands):
    _, registry = user_commands
    operator = {"user_id": "operator-id", "role": "operator"}
    for command in (
        "users.list",
        "users.create",
        "users.update",
        "users.reset_password",
        "users.delete",
    ):
        denied = await _call(registry, command, {}, operator)
        assert denied["success"] is False
        assert denied["error"] == "Not authorized for this action."

    admin = {"user_id": "admin-id", "role": "admin"}
    invalid = await _call(registry, "users.update", {"role": "operator"}, admin)
    assert invalid["error"] == "User ID is required."
    assert (await users.list_users(None, None, None, "sid-1"))["success"] is False


@pytest.mark.asyncio
async def test_user_management_is_removed_from_http_api():
    paths = startup.app.openapi()["paths"]
    assert not any(path.startswith("/api/auth/users") for path in paths)
    assert startup.sio.eio.cors_allowed_origins is None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=startup.app), base_url="http://testserver"
    ) as client:
        retired = await client.get("/api/auth/users")
        unknown = await client.get("/api/v1/unknown")
    assert retired.status_code == 404
    assert retired.json() == {"detail": "API endpoint not found."}
    assert unknown.status_code == 404
