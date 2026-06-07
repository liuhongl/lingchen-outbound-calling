from __future__ import annotations

import re
import threading
import time
from copy import deepcopy
from typing import Any

from .call_control import CallControlError

DEFAULT_ANALYSIS_TASK_TYPES = (
    "summary",
    "tags",
    "quality",
    "promise_to_pay",
)
_SAFE_CALL_ID_RE = re.compile(r"^[a-zA-Z0-9_.:-]{1,128}$")


class LiveKitPostCallResultStore:
    def __init__(self, *, now_ms=None) -> None:
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._lock = threading.Lock()
        self._results: dict[str, dict[str, Any]] = {}

    def create_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        call_id = _required_call_id(payload.get("call_id"))
        turns = _turns(payload.get("turns"))
        now = self._now_ms()
        result = {
            "call_id": call_id,
            "room": _optional_text(payload.get("room")),
            "source": _optional_text(payload.get("source")) or "livekit",
            "status": _optional_text(payload.get("status")) or "completed",
            "turn_count": len(turns),
            "turns": turns,
            "analysis_tasks": [
                _analysis_task(call_id, task_type, now)
                for task_type in DEFAULT_ANALYSIS_TASK_TYPES
            ],
            "metadata": _metadata(payload.get("metadata")),
            "created_at_ms": now,
            "updated_at_ms": now,
        }
        with self._lock:
            self._results[call_id] = result
        return deepcopy(result)

    def get_result(self, call_id: str) -> dict[str, Any] | None:
        with self._lock:
            result = self._results.get(str(call_id or "").strip())
            return None if result is None else deepcopy(result)

    def list_results(self, *, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            results = sorted(
                self._results.values(),
                key=lambda result: int(result.get("created_at_ms") or 0),
                reverse=True,
            )
            return [deepcopy(result) for result in results[:limit]]


def _analysis_task(call_id: str, task_type: str, now: int) -> dict[str, Any]:
    return {
        "task_id": f"{call_id}:{task_type}",
        "call_id": call_id,
        "task_type": task_type,
        "status": "queued",
        "created_at_ms": now,
        "updated_at_ms": now,
    }


def _required_call_id(value: object) -> str:
    call_id = str(value or "").strip()
    if not call_id:
        raise CallControlError("call_id is required", status_code=400)
    if not _SAFE_CALL_ID_RE.match(call_id):
        raise CallControlError("call_id is invalid", status_code=400)
    return call_id


def _turns(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [deepcopy(turn) for turn in value if isinstance(turn, dict)]


def _metadata(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): data
        for key, data in value.items()
        if isinstance(key, str) and not key.startswith("_")
    }


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
