from __future__ import annotations

import re
import threading
import time
import uuid
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from .call_control import CallControlError

_SAFE_DESTINATION_RE = re.compile(r"^\+?[0-9][0-9\-]{1,31}$")
_SAFE_TOKEN_RE = re.compile(r"^[a-zA-Z0-9_.:-]{1,128}$")


class LiveKitSipOutboundOrchestrator:
    def __init__(
        self,
        *,
        room_prefix: str = "sip-outbound",
        id_factory: Callable[[], str] | None = None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self.room_prefix = _slug(room_prefix) or "sip-outbound"
        self._id_factory = id_factory or (lambda: f"sip-{uuid.uuid4().hex[:12]}")
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._lock = threading.Lock()
        self._calls: dict[str, dict[str, Any]] = {}

    def create_outbound(self, payload: dict[str, Any]) -> dict[str, Any]:
        destination = _required_destination(payload.get("destination"))
        dry_run = _payload_bool(payload.get("dry_run"), default=True)
        if not dry_run:
            raise CallControlError(
                "LiveKit SIP real outbound is not wired yet",
                status_code=501,
            )

        now = self._now_ms()
        call_id = _safe_call_id(self._id_factory())
        call = {
            "call_id": call_id,
            "business_id": _optional_text(payload.get("business_id")),
            "destination": destination,
            "room": f"{self.room_prefix}-{_slug(call_id)}",
            "status": "created",
            "dry_run": True,
            "pipeline": _optional_text(payload.get("pipeline")) or "public-cloud",
            "voice_id": _optional_text(payload.get("voice_id")),
            "metadata": _metadata(payload.get("metadata")),
            "created_at_ms": now,
            "updated_at_ms": now,
            "events": [
                {
                    "event": "created",
                    "at_ms": now,
                    "status": "created",
                    "dry_run": True,
                }
            ],
        }
        with self._lock:
            self._calls[call_id] = call
        return deepcopy(call)

    def get_outbound(self, call_id: str) -> dict[str, Any] | None:
        with self._lock:
            call = self._calls.get(str(call_id or "").strip())
            return None if call is None else deepcopy(call)

    def list_outbound(self, *, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            calls = sorted(
                self._calls.values(),
                key=lambda call: int(call.get("created_at_ms") or 0),
                reverse=True,
            )
            return [deepcopy(call) for call in calls[:limit]]


def _required_destination(value: object) -> str:
    destination = str(value or "").strip()
    if not destination:
        raise CallControlError("destination is required", status_code=400)
    if not _SAFE_DESTINATION_RE.match(destination):
        raise CallControlError("destination is invalid", status_code=400)
    return destination


def _safe_call_id(value: object) -> str:
    call_id = str(value or "").strip()
    if not call_id or not _SAFE_TOKEN_RE.match(call_id):
        raise CallControlError("call_id is invalid", status_code=500)
    return call_id


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _payload_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _metadata(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): data
        for key, data in value.items()
        if isinstance(key, str) and not key.startswith("_")
    }


def _slug(value: object) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:96]
