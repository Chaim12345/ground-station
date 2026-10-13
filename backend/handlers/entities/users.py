"""Admin user-management commands for the authenticated browser socket."""

from typing import Any, Dict, Optional

from common import auth as authsvc
from handlers.routing import get_auth_context


def _admin_user_id() -> Optional[str]:
    # Keep an explicit check here as well as the dispatcher policy: account changes
    # must never become available if a command is omitted from that policy.
    context = get_auth_context() or {}
    if not authsvc.is_admin_role(context.get("role")):
        return None
    return str(context.get("user_id") or "").strip() or None


def _payload(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("Invalid user-management payload.")
    return data


def _required_id(data: Dict[str, Any]) -> str:
    user_id = data.get("user_id")
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("User ID is required.")
    return user_id.strip()


async def list_users(sio: Any, data: Any, logger: Any, sid: str) -> Dict[str, Any]:
    del sio, data, logger, sid
    if not _admin_user_id():
        return {"success": False, "error": "Admin access is required."}
    result: Dict[str, Any] = await authsvc.list_users()
    return result


async def create_user(sio: Any, data: Any, logger: Any, sid: str) -> Dict[str, Any]:
    del sio, logger, sid
    actor_id = _admin_user_id()
    if not actor_id:
        return {"success": False, "error": "Admin access is required."}
    try:
        payload = _payload(data)
        result: Dict[str, Any] = await authsvc.create_user(
            username=str(payload.get("username") or ""),
            password=str(payload.get("password") or ""),
            role=str(payload.get("role") or ""),
            actor_user_id=actor_id,
        )
        return result
    except ValueError as exc:
        return {"success": False, "error": str(exc)}


async def update_user(sio: Any, data: Any, logger: Any, sid: str) -> Dict[str, Any]:
    del sio, logger, sid
    actor_id = _admin_user_id()
    if not actor_id:
        return {"success": False, "error": "Admin access is required."}
    try:
        payload = _payload(data)
        result: Dict[str, Any] = await authsvc.update_user(
            user_id=_required_id(payload),
            role=payload.get("role"),
            is_active=payload.get("is_active"),
            actor_user_id=actor_id,
        )
        return result
    except ValueError as exc:
        return {"success": False, "error": str(exc)}


async def reset_user_password(sio: Any, data: Any, logger: Any, sid: str) -> Dict[str, Any]:
    del sio, logger, sid
    actor_id = _admin_user_id()
    if not actor_id:
        return {"success": False, "error": "Admin access is required."}
    try:
        payload = _payload(data)
        result: Dict[str, Any] = await authsvc.reset_user_password(
            user_id=_required_id(payload),
            new_password=str(payload.get("password") or ""),
            actor_user_id=actor_id,
        )
        return result
    except ValueError as exc:
        return {"success": False, "error": str(exc)}


async def delete_user(sio: Any, data: Any, logger: Any, sid: str) -> Dict[str, Any]:
    del sio, logger, sid
    actor_id = _admin_user_id()
    if not actor_id:
        return {"success": False, "error": "Admin access is required."}
    try:
        payload = _payload(data)
        result: Dict[str, Any] = await authsvc.delete_user(
            user_id=_required_id(payload),
            actor_user_id=actor_id,
        )
        return result
    except ValueError as exc:
        return {"success": False, "error": str(exc)}


def register_handlers(registry: Any) -> None:
    registry.register_batch(
        {
            "users.list": (list_users, "api_call"),
            "users.create": (create_user, "api_call"),
            "users.update": (update_user, "api_call"),
            "users.reset_password": (reset_user_password, "api_call"),
            "users.delete": (delete_user, "api_call"),
        }
    )
