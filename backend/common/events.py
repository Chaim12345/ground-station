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


public_events = EventBroker()
