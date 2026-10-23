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

"""VFO updates for satellite tracking - applies doppler correction to internal VFOs."""

import logging

from vfos.state import INTERNAL_VFO_NUMBER, VFOManager

logger = logging.getLogger("vfo-state")


async def handle_vfo_updates_for_tracking(sockio, tracking_data):
    """
    Apply doppler correction to internal observation VFOs.

    User VFOs: Managed entirely in the UI (client-side)
    Internal VFOs: Updated here with doppler-corrected frequencies for automated observations

    This function is called by tracker/messages.py whenever new doppler data arrives
    from the tracker process. It updates the center frequency of any internal VFO
    that is locked to a transmitter.

    Args:
        sockio: Socket.IO server instance (unused here, but available)
        tracking_data: Dictionary containing rig_data with transmitters doppler data
                      Format: {"rig_data": {"transmitters": [...]}, ...}
    """
    tracker_id = tracking_data.get("tracker_id")
    doppler_status = (tracking_data.get("rig_data") or {}).get("doppler_status")
    if not tracker_id or doppler_status not in {"tracking", "unavailable"}:
        return

    # Extract transmitter doppler data
    rig_data = tracking_data.get("rig_data") or {}
    transmitters = rig_data.get("transmitters") or []

    # Get VFO manager instance
    vfo_manager = VFOManager()

    # Get all internal observation sessions
    # Internal sessions have IDs like "internal:observation-uuid"
    all_internal_sessions = vfo_manager.get_all_internal_sessions()

    def sessions_for_observation(observation_id: str) -> list[str]:
        prefix = VFOManager.make_internal_session_id(observation_id)
        return [
            session_id
            for session_id in all_internal_sessions
            if session_id == prefix or session_id.startswith(f"{prefix}:")
        ]

    # OBS slot IDs use the observation ID directly when it starts with "obs-";
    # otherwise the slot adds that prefix. Prefer the exact ID when both exist.
    internal_sessions = sessions_for_observation(str(tracker_id))
    if not internal_sessions and str(tracker_id).startswith("obs-"):
        internal_sessions = sessions_for_observation(str(tracker_id)[4:])

    if not internal_sessions:
        return

    # For each internal session, check all VFOs (1-INTERNAL_VFO_NUMBER)
    updates_applied = 0
    for session_id in internal_sessions:
        for vfo_num in range(1, INTERNAL_VFO_NUMBER + 1):  # VFOs numbered 1-INTERNAL_VFO_NUMBER
            vfo_state = vfo_manager.get_vfo_state(session_id, vfo_num)

            # Skip if VFO doesn't exist or is not active
            if not vfo_state or not vfo_state.active:
                continue

            # Skip if VFO is not locked to a transmitter
            locked_tx_id = vfo_state.locked_transmitter_id
            if not locked_tx_id or locked_tx_id == "none":
                continue

            transmitter = next(
                (item for item in transmitters if item.get("id") == locked_tx_id), None
            )
            doppler_freq = transmitter.get("downlink_observed_freq", 0) if transmitter else 0
            if doppler_status == "unavailable" or not doppler_freq or doppler_freq <= 0:
                if vfo_state.doppler_status != "unavailable":
                    vfo_manager.update_vfo_state(
                        session_id=session_id, vfo_id=vfo_num, doppler_status="unavailable"
                    )
                continue

            # Only publish a new frequency after 10 Hz of movement, but retain
            # tracking status even during the slower part of a pass.
            freq_diff = abs(doppler_freq - vfo_state.center_freq)
            if freq_diff > 10 or vfo_state.doppler_status != "tracking":
                vfo_manager.update_vfo_state(
                    session_id=session_id,
                    vfo_id=vfo_num,
                    center_freq=int(doppler_freq) if freq_diff > 10 else None,
                    doppler_status="tracking",
                )
                if freq_diff > 10:
                    updates_applied += 1
                    logger.debug(
                        "Applied doppler correction to internal VFO: "
                        "session=%s, vfo=%s, tx=%s, freq=%.6f MHz (diff=%.2f kHz)",
                        session_id,
                        vfo_num,
                        locked_tx_id,
                        doppler_freq / 1e6,
                        freq_diff / 1e3,
                    )

    # Log summary if updates were applied (debug level to reduce log spam)
    if updates_applied > 0:
        logger.debug(
            f"Applied doppler correction to {updates_applied} internal VFO(s) "
            f"across {len(internal_sessions)} observation session(s)"
        )
