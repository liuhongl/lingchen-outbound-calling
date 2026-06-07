from __future__ import annotations

from copy import deepcopy
from typing import Any


def build_livekit_turns(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for event in sorted(events, key=lambda item: int(item.get("sequence") or 0)):
        event_name = str(event.get("event") or "")
        if event_name == "asr_final":
            current = _new_turn(len(turns) + 1, event)
            turns.append(current)
            continue
        if current is None:
            continue
        if event_name == "llm_response_final":
            _attach_llm(current, event)
        elif event_name == "tts_final":
            _attach_tts(current, event)
        elif event_name == "tts_audio_publish_finished":
            _attach_audio_publish(current, event)

    return [deepcopy(turn) for turn in turns]


def _new_turn(turn_index: int, event: dict[str, Any]) -> dict[str, Any]:
    received_at_ms = _event_time(event)
    turn = {
        "turn_index": turn_index,
        "started_at_ms": received_at_ms,
        "updated_at_ms": received_at_ms,
        "user_text": str(event.get("text") or "").strip(),
        "asr": {
            "sequence": int(event.get("sequence") or 0),
            "receivedAtMs": received_at_ms,
        },
    }
    _copy_text(event, turn, "room")
    _copy_text(event, turn, "participant")
    _copy_text(event, turn["asr"], "provider")
    return turn


def _attach_llm(turn: dict[str, Any], event: dict[str, Any]) -> None:
    received_at_ms = _event_time(event)
    turn["updated_at_ms"] = max(int(turn["updated_at_ms"]), received_at_ms)
    turn["assistant_text"] = str(event.get("text") or "").strip()
    turn["llm"] = {
        "sequence": int(event.get("sequence") or 0),
        "receivedAtMs": received_at_ms,
    }
    _copy_text(event, turn["llm"], "provider")
    _copy_text(event, turn["llm"], "model")
    _refresh_latency(turn)


def _attach_tts(turn: dict[str, Any], event: dict[str, Any]) -> None:
    received_at_ms = _event_time(event)
    turn["updated_at_ms"] = max(int(turn["updated_at_ms"]), received_at_ms)
    turn["tts"] = {
        "sequence": int(event.get("sequence") or 0),
        "receivedAtMs": received_at_ms,
    }
    for key in (
        "provider",
        "model",
        "voice",
        "audio_duration_ms",
        "audio_byte_count",
    ):
        _copy_value(event, turn["tts"], key)
    _refresh_latency(turn)


def _attach_audio_publish(turn: dict[str, Any], event: dict[str, Any]) -> None:
    received_at_ms = _event_time(event)
    turn["updated_at_ms"] = max(int(turn["updated_at_ms"]), received_at_ms)
    turn["audio_publish"] = {
        "sequence": int(event.get("sequence") or 0),
        "receivedAtMs": received_at_ms,
    }
    for key in ("track_name", "audio_duration_ms"):
        _copy_value(event, turn["audio_publish"], key)
    _refresh_latency(turn)


def _refresh_latency(turn: dict[str, Any]) -> None:
    latency: dict[str, int] = {}
    asr_time = _nested_time(turn, "asr")
    llm_time = _nested_time(turn, "llm")
    tts_time = _nested_time(turn, "tts")
    publish_time = _nested_time(turn, "audio_publish")
    if asr_time is not None and llm_time is not None and llm_time >= asr_time:
        latency["asr_to_llm"] = llm_time - asr_time
    if llm_time is not None and tts_time is not None and tts_time >= llm_time:
        latency["llm_to_tts"] = tts_time - llm_time
    if tts_time is not None and publish_time is not None and publish_time >= tts_time:
        latency["tts_to_publish"] = publish_time - tts_time
    if latency:
        turn["latency_ms"] = latency


def _nested_time(turn: dict[str, Any], key: str) -> int | None:
    value = turn.get(key)
    if not isinstance(value, dict):
        return None
    return _optional_int(value.get("receivedAtMs"))


def _event_time(event: dict[str, Any]) -> int:
    return _optional_int(event.get("receivedAtMs")) or 0


def _optional_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _copy_text(source: dict[str, Any], target: dict[str, Any], key: str) -> None:
    value = str(source.get(key) or "").strip()
    if value:
        target[key] = value


def _copy_value(source: dict[str, Any], target: dict[str, Any], key: str) -> None:
    if key in source and source[key] is not None:
        target[key] = source[key]
