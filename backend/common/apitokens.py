"""Creation, ownership, and authentication of scoped public API tokens."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import and_, func, select

from common.auth import verify_password
from db import AsyncSessionLocal
from db.models import ApiTokens, Users

TOKEN_PREFIX = "gs_pat_"
MAX_ACTIVE_TOKENS = 50
EXPIRY_DAYS = {30, 90, 365}
SCOPES = {
    "station:read": "Read station identity and status",
    "satellites:read": "Read monitored satellites and satellite metadata",
    "observations:read": "Read scheduled observations and their status",
    "artifacts:read": "Read file library metadata",
    "artifacts:download": "Download file library contents",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _serialize(row: ApiTokens) -> dict[str, Any]:
    now = _now()
    status = (
        "revoked"
        if row.revoked_at is not None
        else "expired" if row.expires_at is not None and row.expires_at <= now else "active"
    )
    return {
        "id": str(row.id),
        "name": row.name,
        "hint": f"{TOKEN_PREFIX}{row.id.hex[:8]}…",
        "scopes": list(row.scopes or []),
        "status": status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
    }


def _validate_name(name: str) -> str:
    value = str(name or "").strip()
    if not 1 <= len(value) <= 80 or any(ord(char) < 32 for char in value):
        raise ValueError("Token name must be 1–80 printable characters.")
    return value


def _validate_scopes(scopes: list[str]) -> list[str]:
    if not isinstance(scopes, list) or not scopes:
        raise ValueError("Select at least one scope.")
    if any(not isinstance(scope, str) or scope not in SCOPES for scope in scopes):
        raise ValueError("Unknown API token scope.")
    return sorted(set(scopes))


async def list_tokens(user_id: str) -> list[dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(ApiTokens)
                    .where(ApiTokens.user_id == uuid.UUID(user_id))
                    .order_by(ApiTokens.created_at.desc())
                    .limit(200)
                )
            )
            .scalars()
            .all()
        )
        return [_serialize(row) for row in rows]


async def create_token(
    user_id: str,
    password: str,
    name: str,
    scopes: list[str],
    expires_in_days: Optional[int] = 365,
) -> dict[str, Any]:
    name = _validate_name(name)
    granted_scopes = _validate_scopes(scopes)
    if expires_in_days is not None and expires_in_days not in EXPIRY_DAYS:
        raise ValueError("Expiry must be 30, 90, 365 days, or never.")

    now = _now()
    owner_id = uuid.UUID(user_id)
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(select(Users).where(Users.id == owner_id).limit(1))
        ).scalar_one_or_none()
        if user is None or not user.is_active:
            raise ValueError("Active account required.")
        if user.locked_until and user.locked_until > now:
            raise ValueError("Account temporarily locked. Try again later.")
        if not verify_password(password, user.password_hash):
            # Reuse the login lockout counter so a stolen browser session cannot
            # be used to make unlimited password guesses on this endpoint.
            user.failed_login_count = int(user.failed_login_count or 0) + 1
            if user.failed_login_count >= 5:
                user.failed_login_count = 0
                user.locked_until = now + timedelta(minutes=10)
            await session.commit()
            raise ValueError("Incorrect password.")
        user.failed_login_count = 0
        user.locked_until = None

        active_count = (
            await session.execute(
                select(func.count())
                .select_from(ApiTokens)
                .where(
                    and_(
                        ApiTokens.user_id == owner_id,
                        ApiTokens.revoked_at.is_(None),
                        (ApiTokens.expires_at.is_(None) | (ApiTokens.expires_at > now)),
                    )
                )
            )
        ).scalar_one()
        if active_count >= MAX_ACTIVE_TOKENS:
            raise ValueError("Maximum of 50 active API tokens reached.")

        token_id = uuid.uuid4()
        raw_token = f"{TOKEN_PREFIX}{token_id.hex}.{secrets.token_urlsafe(32)}"
        row = ApiTokens(
            id=token_id,
            user_id=owner_id,
            name=name,
            token_hash=_hash(raw_token),
            scopes=granted_scopes,
            expires_at=(now + timedelta(days=expires_in_days)) if expires_in_days else None,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.commit()
        return {"token": raw_token, "data": _serialize(row)}


async def rename_token(user_id: str, token_id: str, name: str) -> Optional[dict[str, Any]]:
    name = _validate_name(name)
    try:
        parsed_id = uuid.UUID(token_id)
    except ValueError:
        return None
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(ApiTokens).where(
                    and_(ApiTokens.id == parsed_id, ApiTokens.user_id == uuid.UUID(user_id))
                )
            )
        ).scalar_one_or_none()
        if row is None or row.revoked_at is not None:
            return None
        row.name = name
        row.updated_at = _now()
        await session.commit()
        return _serialize(row)


async def revoke_token(user_id: str, token_id: str) -> bool:
    try:
        parsed_id = uuid.UUID(token_id)
    except ValueError:
        return False
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(ApiTokens).where(
                    and_(ApiTokens.id == parsed_id, ApiTokens.user_id == uuid.UUID(user_id))
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        if row.revoked_at is None:
            row.revoked_at = _now()
            row.updated_at = row.revoked_at
            await session.commit()
        return True


async def authenticate_token(raw_token: Optional[str]) -> Optional[dict[str, Any]]:
    """Authenticate only PATs; browser sessions never enter this path."""
    if not raw_token or not raw_token.startswith(TOKEN_PREFIX):
        return None
    now = _now()
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ApiTokens, Users)
            .join(Users, Users.id == ApiTokens.user_id)
            .where(
                and_(
                    ApiTokens.token_hash == _hash(raw_token),
                    ApiTokens.revoked_at.is_(None),
                    (ApiTokens.expires_at.is_(None) | (ApiTokens.expires_at > now)),
                    Users.is_active.is_(True),
                )
            )
            .limit(1)
        )
        pair = result.first()
        if pair is None:
            return None
        token, user = pair
        if token.last_used_at is None or now - token.last_used_at >= timedelta(minutes=5):
            # Polling clients may make many requests; avoid a SQLite write each time.
            token.last_used_at = now
            await session.commit()
        return {
            "token_id": str(token.id),
            "user_id": str(user.id),
            "username": user.username,
            "role": user.role,
            "scopes": list(token.scopes or []),
        }
