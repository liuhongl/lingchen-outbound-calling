from __future__ import annotations

import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from .call_control import CallControlError

_RAW_DOMESTIC_MOBILE_RE = re.compile(r"^1[3-9][0-9]{9}$")
_SAFE_TOKEN_RE = re.compile(r"^[a-zA-Z0-9_.:-]{1,128}$")
_CURRENT_PROVIDER_CALLER_ID = "037123124845"
_RAW_DOMESTIC_MOBILE_HINT = (
    "current SIP provider requires raw domestic mobile numbers, "
    "for example 18518968743; do not add +86, 86, 0, or 9 prefix"
)
_CURRENT_PROVIDER_PROFILE = {
    "sip_proxy": "47.94.86.132:5089",
    "transport": "UDP",
    "caller_id": _CURRENT_PROVIDER_CALLER_ID,
    "destination_format": "raw_domestic_mobile",
    "destination_example": "18518968743",
    "codec": "PCMA/8000",
    "dtmf": "telephone-event/RFC2833",
    "dtmf_payload": 101,
    "rtp_profile": "RTP/AVP",
}


class LiveKitSipOutboundOrchestrator:
    def __init__(
        self,
        *,
        room_prefix: str = "sip-outbound",
        livekit_url: str = "",
        api_key_env: str = "LIVEKIT_API_KEY",
        api_secret_env: str = "LIVEKIT_API_SECRET",
        sip_outbound_real_calls_enabled: bool = False,
        sip_outbound_trunk_id: str = "",
        sip_outbound_caller_id: str = "",
        env: Mapping[str, str] | None = None,
        id_factory: Callable[[], str] | None = None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self.room_prefix = _slug(room_prefix) or "sip-outbound"
        self.livekit_url = _optional_text(livekit_url) or ""
        self.api_key_env = _optional_text(api_key_env) or "LIVEKIT_API_KEY"
        self.api_secret_env = _optional_text(api_secret_env) or "LIVEKIT_API_SECRET"
        self.sip_outbound_real_calls_enabled = bool(sip_outbound_real_calls_enabled)
        self.sip_outbound_trunk_id = _optional_text(sip_outbound_trunk_id) or ""
        self.sip_outbound_caller_id = _optional_text(sip_outbound_caller_id) or ""
        self._env = env if env is not None else os.environ
        self._id_factory = id_factory or (lambda: f"sip-{uuid.uuid4().hex[:12]}")
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._lock = threading.Lock()
        self._calls: dict[str, dict[str, Any]] = {}

    def preflight(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        destination = _optional_text(payload.get("destination"))
        destination_valid = destination is None
        if destination is not None:
            destination_valid = bool(_RAW_DOMESTIC_MOBILE_RE.match(destination))

        missing: list[str] = []
        invalid: list[str] = []
        if not self.livekit_url:
            missing.append("livekit.url")
        if not _optional_text(self._env.get(self.api_key_env)):
            missing.append("livekit.api_key")
        if not _optional_text(self._env.get(self.api_secret_env)):
            missing.append("livekit.api_secret")
        if not self.sip_outbound_trunk_id:
            missing.append("livekit.sip_outbound_trunk_id")
        if not self.sip_outbound_caller_id:
            missing.append("livekit.sip_outbound_caller_id")
        if not self.sip_outbound_real_calls_enabled:
            missing.append("livekit.sip_outbound_real_calls_enabled")
        if destination is not None and not destination_valid:
            missing.append("destination")
        if (
            self.sip_outbound_caller_id
            and self.sip_outbound_caller_id != _CURRENT_PROVIDER_CALLER_ID
        ):
            invalid.append("livekit.sip_outbound_caller_id")

        warnings: list[str] = []
        if destination is not None and not destination_valid:
            warnings.append(_RAW_DOMESTIC_MOBILE_HINT)
        if "livekit.sip_outbound_caller_id" in invalid:
            warnings.append(
                f"current SIP provider caller_id must be {_CURRENT_PROVIDER_CALLER_ID}"
            )

        return {
            "ready": not missing and not invalid,
            "real_call_enabled": self.sip_outbound_real_calls_enabled,
            "destination": destination,
            "destination_valid": destination_valid,
            "room_preview": (
                f"{self.room_prefix}-"
                f"{_slug(_optional_text(payload.get('call_id')) or 'preview')}"
            ),
            "trunk_id": self.sip_outbound_trunk_id,
            "caller_id": self.sip_outbound_caller_id,
            "provider_profile": deepcopy(_CURRENT_PROVIDER_PROFILE),
            "missing": missing,
            "invalid": invalid,
            "warnings": warnings,
        }

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
    if not _RAW_DOMESTIC_MOBILE_RE.match(destination):
        raise CallControlError(
            "destination must be a raw 11-digit domestic mobile number",
            status_code=400,
        )
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
