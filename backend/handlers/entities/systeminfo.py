# Copyright (c) 2025 Efstratios Goudelis
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""System information handlers."""

from typing import Any, Dict, Optional

from server.libraryinfo import get_frontend_library_versions, get_library_versions
from server.version import UpdateCheckError, get_full_version_info, get_update_check


async def fetch_version_info(
    sio: Any, data: Optional[Dict], logger: Any, sid: str
) -> Dict[str, Any]:
    """Return build details and a fresh server timestamp for clock sampling."""
    del sio, data, logger, sid
    return {"success": True, "data": get_full_version_info()}


async def fetch_update_check(
    sio: Any, data: Optional[Dict], logger: Any, sid: str
) -> Dict[str, Any]:
    """Check GitHub releases, bypassing the cache only for an explicit refresh."""
    del sio, sid
    refresh = isinstance(data, dict) and data.get("refresh") is True
    try:
        result = await get_update_check(force_refresh=refresh)
        return {"success": True, "data": result}
    except UpdateCheckError as exc:
        logger.warning("Update check failed: %s", exc)
        return {"success": False, "error": str(exc)}


async def fetch_library_versions(
    sio: Any, data: Optional[Dict], logger: Any, sid: str
) -> Dict[str, Any]:
    """
    Fetch all library versions and metadata.

    Args:
        sio: Socket.IO server instance
        data: Not used
        logger: Logger instance
        sid: Socket.IO session ID

    Returns:
        Dictionary with success status and library versions
    """
    logger.debug("Fetching library versions")
    try:
        library_info = get_library_versions()
        return {"success": True, "data": library_info}
    except Exception as e:
        logger.error(f"Error fetching library versions: {e}")
        return {"success": False, "error": str(e)}


async def fetch_frontend_library_versions(
    sio: Any, data: Optional[Dict], logger: Any, sid: str
) -> Dict[str, Any]:
    """
    Fetch all frontend library versions from package.json.

    Args:
        sio: Socket.IO server instance
        data: Not used
        logger: Logger instance
        sid: Socket.IO session ID

    Returns:
        Dictionary with success status and frontend library versions
    """
    logger.debug("Fetching frontend library versions")
    try:
        library_info = get_frontend_library_versions()
        return {"success": True, "data": library_info}
    except Exception as e:
        logger.error(f"Error fetching frontend library versions: {e}")
        return {"success": False, "error": str(e)}


def register_handlers(registry):
    """Register system info handlers with the command registry."""
    registry.register_batch(
        {
            "system.version": (fetch_version_info, "api_call"),
            "system.update_check": (fetch_update_check, "api_call"),
            "fetch_library_versions": (fetch_library_versions, "api_call"),
            "fetch_frontend_library_versions": (
                fetch_frontend_library_versions,
                "api_call",
            ),
        }
    )
