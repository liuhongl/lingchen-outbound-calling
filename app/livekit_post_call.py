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

    def claim_next_analysis_task(
        self,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        payload = payload or {}
        call_id = _optional_call_id(payload.get("call_id"))
        task_type = _optional_task_type(payload.get("task_type"))
        with self._lock:
            for result in self._oldest_results_locked():
                if call_id and result.get("call_id") != call_id:
                    continue
                for task in result["analysis_tasks"]:
                    if task_type and task.get("task_type") != task_type:
                        continue
                    if task.get("status") != "queued":
                        continue
                    now = self._now_ms()
                    task["status"] = "running"
                    task["started_at_ms"] = now
                    task["updated_at_ms"] = now
                    result["updated_at_ms"] = now
                    return {
                        "task": deepcopy(task),
                        "result": deepcopy(result),
                    }
        return None

    def complete_analysis_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._finish_analysis_task(
            payload,
            status="completed",
            result=_metadata(payload.get("result")),
        )

    def fail_analysis_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._finish_analysis_task(
            payload,
            status="failed",
            error=_optional_text(payload.get("error")) or "analysis task failed",
        )

    def _finish_analysis_task(
        self,
        payload: dict[str, Any],
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        call_id = _required_call_id(payload.get("call_id"))
        task_type = _required_task_type(payload.get("task_type"))
        now = self._now_ms()
        with self._lock:
            post_call_result = self._results.get(call_id)
            if post_call_result is None:
                raise CallControlError("post-call result not found", status_code=404)
            task = _find_task(post_call_result, task_type)
            if task is None:
                raise CallControlError("analysis task not found", status_code=404)
            task["status"] = status
            task["updated_at_ms"] = now
            if status == "completed":
                task["completed_at_ms"] = now
                task["result"] = result or {}
                task.pop("error", None)
            elif status == "failed":
                task["failed_at_ms"] = now
                task["error"] = error or "analysis task failed"
                task.pop("result", None)
            post_call_result["updated_at_ms"] = now
            return {
                "task": deepcopy(task),
                "result": deepcopy(post_call_result),
            }

    def _oldest_results_locked(self) -> list[dict[str, Any]]:
        return sorted(
            self._results.values(),
            key=lambda result: int(result.get("created_at_ms") or 0),
        )


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


def _optional_call_id(value: object) -> str | None:
    if value is None:
        return None
    return _required_call_id(value)


def _required_task_type(value: object) -> str:
    task_type = str(value or "").strip()
    if not task_type:
        raise CallControlError("task_type is required", status_code=400)
    if task_type not in DEFAULT_ANALYSIS_TASK_TYPES:
        raise CallControlError("task_type is invalid", status_code=400)
    return task_type


def _optional_task_type(value: object) -> str | None:
    if value is None:
        return None
    return _required_task_type(value)


def _find_task(
    post_call_result: dict[str, Any],
    task_type: str,
) -> dict[str, Any] | None:
    for task in post_call_result["analysis_tasks"]:
        if task.get("task_type") == task_type:
            return task
    return None


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
