"""Process-local fan-out for the public read-only API event stream."""

import asyncio
import time
from dataclasses import dataclass
from typing import Any

MAX_STREAMS = 100
MAX_STREAMS_PER_TOKEN = 5
MAX_QUEUED_EVENTS = 32


def rotator_status(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Reduce worker telemetry to the public rotator state without connection details."""
    data = snapshot.get("rotator_data") or {}
    if "connected" not in data:
        return None
    tracking_state = snapshot.get("tracking_state") or {}
    device_id = tracking_state.get("rotator_id")
    device_id = str(device_id) if device_id not in (None, "", "none") else None
    connected = bool(data["connected"])
    if not connected:
        state = "disconnected"
    elif data.get("motion_unconfirmed"):
        state = "motion_unconfirmed"
    elif data.get("error"):
        state = "error"
    elif any(
        data.get(key)
        for key in ("outofbounds", "minelevation", "maxelevation", "minazimuth", "maxazimuth")
    ):
        state = "out_of_bounds"
    elif data.get("slewing"):
        state = "slewing"
    elif data.get("tracking"):
        state = "tracking"
    elif data.get("park_requested"):
        state = "park_requested"
    elif data.get("parked"):
        state = "parked"
    elif data.get("stopped") and tracking_state.get("rotator_state") == "stopped":
        state = "stopped"
    else:
        state = "connected"
    return {
        "device_id": device_id,
        "state": state,
        # Tracking mode can remain active while the rotator is slewing.
        "tracking_enabled": connected and bool(data.get("tracking")),
        "observed_at": snapshot.get("observed_at"),
    }


def rig_status(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Reduce worker telemetry to a rig state without frequencies or connection settings."""
    data = snapshot.get("rig_data") or {}
    if "connected" not in data:
        return None
    tracking_state = snapshot.get("tracking_state") or {}
    device_id = tracking_state.get("rig_id")
    device_id = str(device_id) if device_id not in (None, "", "none") else None
    connected = bool(data["connected"])
    if data.get("error"):
        state = "error"
    elif not connected:
        state = "disconnected"
    elif data.get("tuning"):
        state = "tuning"
    elif data.get("tracking"):
        state = "tracking"
    elif data.get("stopped") and tracking_state.get("rig_state") == "stopped":
        state = "stopped"
    else:
        state = "connected"
    return {
        "device_id": device_id,
        "state": state,
        "tracking_enabled": connected and bool(data.get("tracking")),
        "observed_at": snapshot.get("observed_at"),
    }


@dataclass(eq=False)
class Subscription:
    token_id: str
    scopes: frozenset[str]
    queue: asyncio.Queue[tuple[str, dict[str, Any]] | None]


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: set[Subscription] = set()
        self._rotator_states: dict[str, dict[str, Any]] = {}
        self._rig_states: dict[str, dict[str, Any]] = {}
        self._closing = False

    def subscribe(self, token_id: str, scopes: list[str]) -> Subscription:
        if self._closing:
            raise RuntimeError("Event streams are closing for service restart.")
        if (
            len(self._subscribers) >= MAX_STREAMS
            or sum(subscriber.token_id == token_id for subscriber in self._subscribers)
            >= MAX_STREAMS_PER_TOKEN
        ):
            raise ValueError("Too many open API event streams.")
        subscriber = Subscription(token_id, frozenset(scopes), asyncio.Queue(MAX_QUEUED_EVENTS))
        self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscription) -> None:
        self._subscribers.discard(subscriber)

    def close_for_shutdown(self) -> None:
        """End open streams and reject reconnects while the process exits."""
        self._closing = True
        for subscriber in tuple(self._subscribers):
            self.unsubscribe(subscriber)
            # Discard queued updates so the termination marker is consumed next.
            while not subscriber.queue.empty():
                subscriber.queue.get_nowait()
            subscriber.queue.put_nowait(None)

    def publish(self, name: str, data: dict[str, Any], scope: str) -> None:
        for subscriber in tuple(self._subscribers):
            if scope not in subscriber.scopes:
                continue
            try:
                subscriber.queue.put_nowait((name, data))
            except asyncio.QueueFull:
                # A slow client cannot receive a trustworthy sequence of changes.
                # Close its stream; reconnecting clients fetch a fresh snapshot.
                self._subscribers.discard(subscriber)
                while not subscriber.queue.empty():
                    subscriber.queue.get_nowait()
                subscriber.queue.put_nowait(None)

    def status_changed(self) -> None:
        self.publish("_status_dirty", {}, "station:read")

    def observations_changed(
        self, observation_id: str | None = None, status: str | None = None
    ) -> None:
        data: dict[str, Any] = {}
        if observation_id is not None:
            data["id"] = observation_id
        if status is not None:
            data["status"] = status
        self.publish("observations.changed", data, "observations:read")
        self.status_changed()

    def target_changed(
        self, tracker_id: str, previous: dict[str, Any] | None, current: dict[str, Any] | None
    ) -> None:
        self.publish(
            "target.changed",
            {"tracker_id": tracker_id, "previous": previous, "current": current},
            "station:read",
        )

    def target_slot_created(self, tracker_id: str) -> None:
        self.publish("target.slot.created", {"tracker_id": tracker_id}, "station:read")

    def target_slot_deleted(self, tracker_id: str) -> None:
        self.publish("target.slot.deleted", {"tracker_id": tracker_id}, "station:read")

    def target_visibility_changed(
        self, tracker_id: str, norad_id: str, name: str, elevation_deg: float, visible: bool
    ) -> None:
        event_name = "target.visible" if visible else "target.below_horizon"
        self.publish(
            event_name,
            {
                "tracker_id": tracker_id,
                "norad_id": norad_id,
                "name": name,
                "elevation_deg": elevation_deg,
            },
            "station:read",
        )

    def tracker_hardware_changed(
        self,
        tracker_id: str,
        device: str,
        device_id: str | None,
        connected: bool,
        previous_connected: bool | None,
    ) -> None:
        self.publish(
            "tracker.hardware.changed",
            {
                "tracker_id": tracker_id,
                "device": device,
                "device_id": device_id,
                "connected": connected,
                "previous_connected": previous_connected,
            },
            "station:read",
        )

    def observe_rotator(self, tracker_id: str, snapshot: dict[str, Any]) -> None:
        """Emit one transition per observed state, including worker replacement."""
        current = rotator_status(snapshot)
        if current is None:
            return
        previous = self._rotator_states.get(tracker_id)
        if current["device_id"] is None and (previous is None or previous["device_id"] is None):
            return
        generation = snapshot.get("worker_generation")
        if previous and previous["generation"] != generation:
            # A new worker cannot confirm what happened to the old connection.
            self.rotator_unavailable(tracker_id)
            previous = self._rotator_states.get(tracker_id)
        if previous and all(
            previous[key] == current[key] for key in ("device_id", "state", "tracking_enabled")
        ):
            self._rotator_states[tracker_id] = {**current, "generation": generation}
            return
        self.publish(
            "tracker.rotator.state.changed",
            {
                "tracker_id": tracker_id,
                "device_id": current["device_id"],
                "previous_device_id": previous["device_id"] if previous else None,
                "previous": previous["state"] if previous else None,
                "current": current["state"],
                "previous_tracking_enabled": previous["tracking_enabled"] if previous else None,
                "tracking_enabled": current["tracking_enabled"],
                "observed_at": current["observed_at"],
            },
            "station:read",
        )
        self._rotator_states[tracker_id] = {**current, "generation": generation}

    def rotator_unavailable(self, tracker_id: str) -> None:
        """Report lost worker telemetry without claiming the motor disconnected."""
        previous = self._rotator_states.get(tracker_id)
        if previous is None or previous["state"] == "unavailable":
            return
        current = {**previous, "state": "unavailable", "tracking_enabled": False}
        current["observed_at"] = time.time()
        self.publish(
            "tracker.rotator.state.changed",
            {
                "tracker_id": tracker_id,
                "device_id": previous["device_id"],
                "previous_device_id": previous["device_id"],
                "previous": previous["state"],
                "current": "unavailable",
                "previous_tracking_enabled": previous["tracking_enabled"],
                "tracking_enabled": False,
                "observed_at": current["observed_at"],
            },
            "station:read",
        )
        self._rotator_states[tracker_id] = current

    def reconcile_rotator_workers(self, is_alive) -> None:
        """Detect workers that exited without a final hardware snapshot."""
        for tracker_id in tuple(self._rotator_states):
            if not is_alive(tracker_id):
                self.rotator_unavailable(tracker_id)
                # Keep a brief unavailable state for a quick restart, then
                # release removed observation slots from this process cache.
                unavailable_at = self._rotator_states[tracker_id]["observed_at"]
                if unavailable_at is not None and time.time() - unavailable_at > 60:
                    self._rotator_states.pop(tracker_id, None)

    def observe_rig(self, tracker_id: str, snapshot: dict[str, Any]) -> None:
        """Emit rig mode transitions, including a new worker's first observation."""
        current = rig_status(snapshot)
        if current is None:
            return
        previous = self._rig_states.get(tracker_id)
        if current["device_id"] is None and (previous is None or previous["device_id"] is None):
            return
        generation = snapshot.get("worker_generation")
        if previous and previous["generation"] != generation:
            self.rig_unavailable(tracker_id)
            previous = self._rig_states.get(tracker_id)
        if previous and all(
            previous[key] == current[key] for key in ("device_id", "state", "tracking_enabled")
        ):
            self._rig_states[tracker_id] = {**current, "generation": generation}
            return
        self.publish(
            "tracker.rig.state.changed",
            {
                "tracker_id": tracker_id,
                "device_id": current["device_id"],
                "previous_device_id": previous["device_id"] if previous else None,
                "previous": previous["state"] if previous else None,
                "current": current["state"],
                "previous_tracking_enabled": previous["tracking_enabled"] if previous else None,
                "tracking_enabled": current["tracking_enabled"],
                "observed_at": current["observed_at"],
            },
            "station:read",
        )
        self._rig_states[tracker_id] = {**current, "generation": generation}

    def rig_unavailable(self, tracker_id: str) -> None:
        """A stopped worker cannot confirm whether its radio disconnected."""
        previous = self._rig_states.get(tracker_id)
        if previous is None or previous["state"] == "unavailable":
            return
        current = {**previous, "state": "unavailable", "tracking_enabled": False}
        current["observed_at"] = time.time()
        self.publish(
            "tracker.rig.state.changed",
            {
                "tracker_id": tracker_id,
                "device_id": previous["device_id"],
                "previous_device_id": previous["device_id"],
                "previous": previous["state"],
                "current": "unavailable",
                "previous_tracking_enabled": previous["tracking_enabled"],
                "tracking_enabled": False,
                "observed_at": current["observed_at"],
            },
            "station:read",
        )
        self._rig_states[tracker_id] = current

    def reconcile_rig_workers(self, is_alive) -> None:
        """Detect workers that exited without a final rig snapshot."""
        for tracker_id in tuple(self._rig_states):
            if not is_alive(tracker_id):
                self.rig_unavailable(tracker_id)
                unavailable_at = self._rig_states[tracker_id]["observed_at"]
                if unavailable_at is not None and time.time() - unavailable_at > 60:
                    self._rig_states.pop(tracker_id, None)

    def artifact_ready(self, item_id: str, item_type: str, name: str) -> None:
        self.publish(
            "artifact.ready",
            {"id": item_id, "type": item_type, "name": name},
            "artifacts:read",
        )

    def sdr_stream_started(self, sdr_id: str) -> None:
        self.publish("sdr.streaming.started", {"sdr_id": sdr_id}, "station:read")

    def sdr_stream_stopped(self, sdr_id: str) -> None:
        self.publish("sdr.streaming.stopped", {"sdr_id": sdr_id}, "station:read")


public_events = EventBroker()
