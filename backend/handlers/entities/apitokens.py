"""Personal API-token management for authenticated browser sockets."""

from typing import Any

from common import apitokens
from handlers.routing import get_auth_context


def _owner_id() -> str:
    # Ownership always comes from the revalidated socket session, never the payload.
    context = get_auth_context() or {}
    user_id = str(context.get("user_id") or "").strip()
    if not user_id:
        raise ValueError("Browser login required.")
    return user_id


def _payload(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("Invalid API-token payload.")
    return data


def _token_id(data: dict[str, Any]) -> str:
    token_id = data.get("token_id")
    if not isinstance(token_id, str) or not token_id.strip():
        raise ValueError("API token ID is required.")
    return token_id.strip()


async def list_scopes(sio: Any, data: Any, logger: Any, sid: str) -> dict[str, Any]:
    del sio, data, logger, sid
    try:
        _owner_id()
        return {
            "success": True,
            "data": [{"id": key, "description": label} for key, label in apitokens.SCOPES.items()],
        }
    except ValueError as exc:
        return {"success": False, "error": str(exc)}


async def list_tokens(sio: Any, data: Any, logger: Any, sid: str) -> dict[str, Any]:
    del sio, data, logger, sid
    try:
        return {"success": True, "data": await apitokens.list_tokens(_owner_id())}
    except ValueError as exc:
        return {"success": False, "error": str(exc)}


async def create_token(sio: Any, data: Any, logger: Any, sid: str) -> dict[str, Any]:
    del sio, logger, sid
    try:
        payload = _payload(data)
        if not isinstance(payload.get("password"), str):
            raise ValueError("Password is required.")
        expiry = payload.get("expires_in_days", 365)
        if expiry is not None and (type(expiry) is not int or expiry not in apitokens.EXPIRY_DAYS):
            raise ValueError("Expiry must be 30, 90, 365 days, or never.")
        result = await apitokens.create_token(
            _owner_id(),
            password=payload.get("password", ""),
            name=payload.get("name", ""),
            scopes=payload.get("scopes", []),
            expires_in_days=expiry,
        )
        # The raw secret is returned only in this direct acknowledgement.
        return {"success": True, "data": result}
    except ValueError as exc:
        return {"success": False, "error": str(exc)}


async def rename_token(sio: Any, data: Any, logger: Any, sid: str) -> dict[str, Any]:
    del sio, logger, sid
    try:
        payload = _payload(data)
        result = await apitokens.rename_token(
            _owner_id(), _token_id(payload), payload.get("name", "")
        )
        if result is None:
            return {"success": False, "error": "API token not found."}
        return {"success": True, "data": result}
    except ValueError as exc:
        return {"success": False, "error": str(exc)}


async def revoke_token(sio: Any, data: Any, logger: Any, sid: str) -> dict[str, Any]:
    del sio, logger, sid
    try:
        payload = _payload(data)
        if not await apitokens.revoke_token(_owner_id(), _token_id(payload)):
            return {"success": False, "error": "API token not found."}
        return {"success": True}
    except ValueError as exc:
        return {"success": False, "error": str(exc)}


def register_handlers(registry: Any) -> None:
    registry.register_batch(
        {
            "tokens.scopes": (list_scopes, "api_call"),
            "tokens.list": (list_tokens, "api_call"),
            "tokens.create": (create_token, "api_call"),
            "tokens.rename": (rename_token, "api_call"),
            "tokens.revoke": (revoke_token, "api_call"),
        }
    )
