from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from typing import Any


class LiveKitDebugEventStore:
    def __init__(self, *, max_events: int = 500) -> None:
        self.max_events = max_events
        self._sequence = 0
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def append(self, event: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._sequence += 1
            item = {
                "sequence": self._sequence,
                "receivedAtMs": int(time.time() * 1000),
                **_public_event(event),
            }
            self._events.append(item)
            if len(self._events) > self.max_events:
                self._events = self._events[-self.max_events :]
            return dict(item)

    def list_events(
        self,
        *,
        room: str | None = None,
        after: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._lock:
            events = [
                event
                for event in self._events
                if int(event["sequence"]) > after
                and (room is None or str(event.get("room", "")) == room)
            ]
            return [dict(event) for event in events[-limit:]]


def _public_event(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in event.items()
        if not str(key).startswith("_")
    }
