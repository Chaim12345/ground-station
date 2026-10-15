"""Process-local fan-out for the public read-only API event stream."""

import asyncio
from dataclasses import dataclass
from typing import Any

MAX_STREAMS = 100
MAX_STREAMS_PER_TOKEN = 5
MAX_QUEUED_EVENTS = 32


@dataclass(eq=False)
class Subscription:
    token_id: str
    scopes: frozenset[str]
    queue: asyncio.Queue[tuple[str, dict[str, Any]] | None]


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: set[Subscription] = set()

    def subscribe(self, token_id: str, scopes: list[str]) -> Subscription:
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
