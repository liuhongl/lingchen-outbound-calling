from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from typing import Any

from .config import LiveKitConfig


class LiveKitWebDebugSessionFactory:
    def __init__(self, config: LiveKitConfig):
        self.config = config

    def create_session(
        self,
        payload: dict[str, Any],
        *,
        now: int | None = None,
    ) -> dict[str, Any]:
        api_key = os.getenv(self.config.api_key_env, "").strip()
        api_secret = os.getenv(self.config.api_secret_env, "").strip()
        missing = [
            name
            for name, value in (
                (self.config.api_key_env, api_key),
                (self.config.api_secret_env, api_secret),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "missing LiveKit credentials in environment: " + ", ".join(missing)
            )

        issued_at = int(time.time()) if now is None else int(now)
        expires_at = issued_at + self.config.web_debug_token_ttl_seconds
        room = self._room_name(payload.get("room"))
        identity = _slug(payload.get("identity")) or f"browser-{uuid.uuid4().hex[:8]}"
        name = str(payload.get("name") or identity).strip() or identity
        token = _encode_jwt(
            {
                "exp": expires_at,
                "iss": api_key,
                "metadata": "",
                "name": name,
                "nbf": issued_at,
                "sub": identity,
                "video": {
                    "room": room,
                    "roomJoin": True,
                    "canPublish": True,
                    "canSubscribe": True,
                    "canPublishData": True,
                },
            },
            api_secret,
        )
        return {
            "livekitUrl": self.config.url,
            "room": room,
            "identity": identity,
            "name": name,
            "token": token,
            "expiresAt": expires_at,
        }

    def _room_name(self, value: object) -> str:
        prefix = _slug(self.config.web_debug_room_prefix) or "web-debug"
        suffix = _slug(value)
        if not suffix or suffix == prefix:
            return prefix
        if suffix.startswith(f"{prefix}-"):
            return suffix
        return f"{prefix}-{suffix}"


def _encode_jwt(payload: dict[str, Any], secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = ".".join(
        [
            _base64url_json(header),
            _base64url_json(payload),
        ]
    )
    digest = hmac.new(
        secret.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{_base64url_bytes(digest)}"


def _base64url_json(value: dict[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _base64url_bytes(raw)


def _base64url_bytes(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _slug(value: object) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:80]
