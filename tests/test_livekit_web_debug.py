from __future__ import annotations

import base64
import hashlib
import hmac
import json

from app.config import LiveKitConfig
from app.livekit_web_debug import LiveKitWebDebugSessionFactory


def test_livekit_web_debug_session_factory_signs_join_token(monkeypatch):
    monkeypatch.setenv("TEST_LIVEKIT_API_KEY", "api-key")
    monkeypatch.setenv("TEST_LIVEKIT_API_SECRET", "secret")

    factory = LiveKitWebDebugSessionFactory(
        LiveKitConfig(
            enabled=True,
            url="wss://livekit.example",
            api_key_env="TEST_LIVEKIT_API_KEY",
            api_secret_env="TEST_LIVEKIT_API_SECRET",
            web_debug_room_prefix="web-debug",
            web_debug_token_ttl_seconds=600,
        )
    )

    session = factory.create_session(
        {
            "room": "demo A",
            "identity": "user 1",
            "name": "浏览器用户",
        },
        now=1_700_000_000,
    )

    assert session["livekitUrl"] == "wss://livekit.example"
    assert session["room"] == "web-debug-demo-a"
    assert session["identity"] == "user-1"
    assert session["name"] == "浏览器用户"
    assert session["expiresAt"] == 1_700_000_600

    header, payload, signature = _decode_jwt(session["token"])
    assert header == {"alg": "HS256", "typ": "JWT"}
    assert payload == {
        "exp": 1_700_000_600,
        "iss": "api-key",
        "metadata": "",
        "name": "浏览器用户",
        "nbf": 1_700_000_000,
        "sub": "user-1",
        "video": {
            "room": "web-debug-demo-a",
            "roomJoin": True,
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": True,
        },
    }
    assert signature == _sign_token(session["token"], "secret")


def test_livekit_web_debug_session_factory_requires_credentials(monkeypatch):
    monkeypatch.delenv("TEST_LIVEKIT_API_KEY", raising=False)
    monkeypatch.setenv("TEST_LIVEKIT_API_SECRET", "secret")
    factory = LiveKitWebDebugSessionFactory(
        LiveKitConfig(
            enabled=True,
            api_key_env="TEST_LIVEKIT_API_KEY",
            api_secret_env="TEST_LIVEKIT_API_SECRET",
        )
    )

    try:
        factory.create_session({}, now=1_700_000_000)
    except RuntimeError as err:
        assert "missing LiveKit credentials" in str(err)
        assert "TEST_LIVEKIT_API_KEY" in str(err)
    else:
        raise AssertionError("expected missing LiveKit credentials to fail")


def test_livekit_web_debug_session_factory_does_not_double_prefix_room(monkeypatch):
    monkeypatch.setenv("TEST_LIVEKIT_API_KEY", "api-key")
    monkeypatch.setenv("TEST_LIVEKIT_API_SECRET", "secret")
    factory = LiveKitWebDebugSessionFactory(
        LiveKitConfig(
            enabled=True,
            api_key_env="TEST_LIVEKIT_API_KEY",
            api_secret_env="TEST_LIVEKIT_API_SECRET",
            web_debug_room_prefix="web-debug",
        )
    )

    session = factory.create_session(
        {"room": "web-debug-demo", "identity": "agent-worker"},
        now=1_700_000_000,
    )

    assert session["room"] == "web-debug-demo"


def _decode_jwt(token: str) -> tuple[dict[str, object], dict[str, object], str]:
    encoded_header, encoded_payload, signature = token.split(".")
    return (
        _decode_json_segment(encoded_header),
        _decode_json_segment(encoded_payload),
        signature,
    )


def _decode_json_segment(segment: str) -> dict[str, object]:
    padding = "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(segment + padding).decode("utf-8"))


def _sign_token(token: str, secret: str) -> str:
    signing_input = ".".join(token.split(".")[:2]).encode("ascii")
    digest = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
