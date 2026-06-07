from __future__ import annotations

import pytest

from app.call_control import CallControlError
from app.livekit_sip_outbound import LiveKitSipOutboundOrchestrator


def test_create_dry_run_outbound_session_builds_room_and_status():
    ids = iter(["sip-test-1"])
    manager = LiveKitSipOutboundOrchestrator(
        id_factory=lambda: next(ids),
        now_ms=lambda: 1780800000000,
    )

    call = manager.create_outbound(
        {
            "destination": "+8613800138000",
            "business_id": "debt-001",
            "dry_run": True,
            "pipeline": "public-cloud",
            "voice_id": "longanyang",
            "metadata": {"tenant_id": "tenant-a"},
        }
    )

    assert call == {
        "call_id": "sip-test-1",
        "business_id": "debt-001",
        "destination": "+8613800138000",
        "room": "sip-outbound-sip-test-1",
        "status": "created",
        "dry_run": True,
        "pipeline": "public-cloud",
        "voice_id": "longanyang",
        "metadata": {"tenant_id": "tenant-a"},
        "created_at_ms": 1780800000000,
        "updated_at_ms": 1780800000000,
        "events": [
            {
                "event": "created",
                "at_ms": 1780800000000,
                "status": "created",
                "dry_run": True,
            }
        ],
    }


def test_list_and_get_outbound_sessions_are_newest_first():
    ids = iter(["sip-test-1", "sip-test-2"])
    clock = iter([1000, 2000])
    manager = LiveKitSipOutboundOrchestrator(
        id_factory=lambda: next(ids),
        now_ms=lambda: next(clock),
    )
    first = manager.create_outbound({"destination": "1001", "dry_run": True})
    second = manager.create_outbound({"destination": "1002", "dry_run": True})

    assert manager.get_outbound(first["call_id"]) == first
    assert [call["call_id"] for call in manager.list_outbound()] == [
        second["call_id"],
        first["call_id"],
    ]


def test_create_outbound_rejects_real_dial_before_sip_is_wired():
    manager = LiveKitSipOutboundOrchestrator(id_factory=lambda: "sip-test-1")

    with pytest.raises(CallControlError) as err:
        manager.create_outbound({"destination": "+8613800138000", "dry_run": False})

    assert err.value.status_code == 501
    assert str(err.value) == "LiveKit SIP real outbound is not wired yet"
    assert manager.list_outbound() == []


def test_preflight_reports_missing_real_outbound_configuration():
    manager = LiveKitSipOutboundOrchestrator(
        livekit_url="wss://livekit.example",
        api_key_env="TEST_LIVEKIT_API_KEY",
        api_secret_env="TEST_LIVEKIT_API_SECRET",
        env={"TEST_LIVEKIT_API_KEY": "key"},
    )

    preflight = manager.preflight({"destination": "+8613800138000"})

    assert preflight["ready"] is False
    assert preflight["real_call_enabled"] is False
    assert preflight["destination_valid"] is True
    assert preflight["missing"] == [
        "livekit.api_secret",
        "livekit.sip_outbound_trunk_id",
        "livekit.sip_outbound_caller_id",
        "livekit.sip_outbound_real_calls_enabled",
    ]
    assert preflight["warnings"] == []


def test_preflight_accepts_real_outbound_configuration_without_dialing():
    manager = LiveKitSipOutboundOrchestrator(
        room_prefix="sip-prod",
        livekit_url="wss://livekit.example",
        api_key_env="TEST_LIVEKIT_API_KEY",
        api_secret_env="TEST_LIVEKIT_API_SECRET",
        sip_outbound_real_calls_enabled=True,
        sip_outbound_trunk_id="trunk_abc",
        sip_outbound_caller_id="+861055500000",
        env={
            "TEST_LIVEKIT_API_KEY": "key",
            "TEST_LIVEKIT_API_SECRET": "secret",
        },
    )

    preflight = manager.preflight({"destination": "+8613800138000"})

    assert preflight["ready"] is True
    assert preflight["real_call_enabled"] is True
    assert preflight["destination_valid"] is True
    assert preflight["missing"] == []
    assert preflight["room_preview"].startswith("sip-prod-")
    assert preflight["trunk_id"] == "trunk_abc"
    assert preflight["caller_id"] == "+861055500000"


def test_create_outbound_requires_destination():
    manager = LiveKitSipOutboundOrchestrator()

    with pytest.raises(CallControlError) as err:
        manager.create_outbound({"dry_run": True})

    assert err.value.status_code == 400
    assert str(err.value) == "destination is required"
