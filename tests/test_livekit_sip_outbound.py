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
            "destination": "18518968743",
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
        "destination": "18518968743",
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
    first = manager.create_outbound({"destination": "18518968743", "dry_run": True})
    second = manager.create_outbound({"destination": "15800967789", "dry_run": True})

    assert manager.get_outbound(first["call_id"]) == first
    assert [call["call_id"] for call in manager.list_outbound()] == [
        second["call_id"],
        first["call_id"],
    ]


def test_create_outbound_rejects_real_dial_before_sip_is_wired():
    manager = LiveKitSipOutboundOrchestrator(id_factory=lambda: "sip-test-1")

    with pytest.raises(CallControlError) as err:
        manager.create_outbound({"destination": "18518968743", "dry_run": False})

    assert err.value.status_code == 501
    assert str(err.value) == "LiveKit SIP real outbound is not wired yet"
    assert manager.list_outbound() == []


def test_create_real_outbound_calls_livekit_sip_participant_creator():
    requests = []
    manager = LiveKitSipOutboundOrchestrator(
        room_prefix="sip-prod",
        livekit_url="wss://livekit.example",
        api_key_env="TEST_LIVEKIT_API_KEY",
        api_secret_env="TEST_LIVEKIT_API_SECRET",
        sip_outbound_real_calls_enabled=True,
        sip_outbound_trunk_id="ST_abc",
        sip_outbound_caller_id="037123124845",
        env={
            "TEST_LIVEKIT_API_KEY": "key",
            "TEST_LIVEKIT_API_SECRET": "secret",
        },
        id_factory=lambda: "sip-test-1",
        now_ms=lambda: 1780800000000,
        sip_participant_creator=lambda request: requests.append(request)
        or {
            "participant_identity": "sip-test-1",
            "sip_call_id": "sip-call-1",
        },
    )

    call = manager.create_outbound(
        {
            "destination": "18518968743",
            "business_id": "debt-001",
            "dry_run": False,
            "pipeline": "public-cloud",
        }
    )

    assert requests == [
        {
            "livekit_url": "wss://livekit.example",
            "api_key": "key",
            "api_secret": "secret",
            "room_name": "sip-prod-sip-test-1",
            "sip_trunk_id": "ST_abc",
            "sip_number": "037123124845",
            "sip_call_to": "18518968743",
            "participant_identity": "sip-test-1",
            "participant_name": "18518968743",
            "wait_until_answered": False,
        }
    ]
    assert call["call_id"] == "sip-test-1"
    assert call["room"] == "sip-prod-sip-test-1"
    assert call["dry_run"] is False
    assert call["status"] == "sip_participant_created"
    assert call["sip_participant"] == {
        "participant_identity": "sip-test-1",
        "sip_call_id": "sip-call-1",
    }
    assert call["events"] == [
        {
            "event": "created",
            "at_ms": 1780800000000,
            "status": "created",
            "dry_run": False,
        },
        {
            "event": "sip_participant_create_requested",
            "at_ms": 1780800000000,
            "sip_trunk_id": "ST_abc",
            "sip_number": "037123124845",
            "sip_call_to": "18518968743",
        },
        {
            "event": "sip_participant_created",
            "at_ms": 1780800000000,
            "status": "sip_participant_created",
        },
    ]
    assert manager.get_outbound("sip-test-1") == call


def test_preflight_reports_missing_real_outbound_configuration():
    manager = LiveKitSipOutboundOrchestrator(
        livekit_url="wss://livekit.example",
        api_key_env="TEST_LIVEKIT_API_KEY",
        api_secret_env="TEST_LIVEKIT_API_SECRET",
        env={"TEST_LIVEKIT_API_KEY": "key"},
    )

    preflight = manager.preflight({"destination": "18518968743"})

    assert preflight["ready"] is False
    assert preflight["real_call_enabled"] is False
    assert preflight["destination_valid"] is True
    assert preflight["missing"] == [
        "livekit.api_secret",
        "livekit.sip_outbound_trunk_id",
        "livekit.sip_outbound_caller_id",
        "livekit.sip_outbound_real_calls_enabled",
    ]
    assert preflight["invalid"] == []
    assert preflight["warnings"] == []


def test_preflight_accepts_real_outbound_configuration_without_dialing():
    manager = LiveKitSipOutboundOrchestrator(
        room_prefix="sip-prod",
        livekit_url="wss://livekit.example",
        api_key_env="TEST_LIVEKIT_API_KEY",
        api_secret_env="TEST_LIVEKIT_API_SECRET",
        sip_outbound_real_calls_enabled=True,
        sip_outbound_trunk_id="trunk_abc",
        sip_outbound_caller_id="037123124845",
        env={
            "TEST_LIVEKIT_API_KEY": "key",
            "TEST_LIVEKIT_API_SECRET": "secret",
        },
    )

    preflight = manager.preflight({"destination": "18518968743"})

    assert preflight["ready"] is True
    assert preflight["real_call_enabled"] is True
    assert preflight["destination_valid"] is True
    assert preflight["missing"] == []
    assert preflight["invalid"] == []
    assert preflight["room_preview"].startswith("sip-prod-")
    assert preflight["trunk_id"] == "trunk_abc"
    assert preflight["caller_id"] == "037123124845"
    assert preflight["provider_profile"] == {
        "sip_proxy": "47.94.86.132:5089",
        "transport": "UDP",
        "caller_id": "037123124845",
        "destination_format": "raw_domestic_mobile",
        "destination_example": "18518968743",
        "codec": "PCMA/8000",
        "dtmf": "telephone-event/RFC2833",
        "dtmf_payload": 101,
        "rtp_profile": "RTP/AVP",
    }


def test_preflight_rejects_e164_destination_for_current_provider():
    manager = LiveKitSipOutboundOrchestrator(
        livekit_url="wss://livekit.example",
        api_key_env="TEST_LIVEKIT_API_KEY",
        api_secret_env="TEST_LIVEKIT_API_SECRET",
        sip_outbound_real_calls_enabled=True,
        sip_outbound_trunk_id="trunk_abc",
        sip_outbound_caller_id="037123124845",
        env={
            "TEST_LIVEKIT_API_KEY": "key",
            "TEST_LIVEKIT_API_SECRET": "secret",
        },
    )

    preflight = manager.preflight({"destination": "+8618518968743"})

    assert preflight["ready"] is False
    assert preflight["destination_valid"] is False
    assert preflight["missing"] == ["destination"]
    assert preflight["invalid"] == []
    assert preflight["warnings"] == [
        "current SIP provider requires raw domestic mobile numbers, "
        "for example 18518968743; do not add +86, 86, 0, or 9 prefix"
    ]


def test_preflight_rejects_unapproved_caller_id_for_current_provider():
    manager = LiveKitSipOutboundOrchestrator(
        livekit_url="wss://livekit.example",
        api_key_env="TEST_LIVEKIT_API_KEY",
        api_secret_env="TEST_LIVEKIT_API_SECRET",
        sip_outbound_real_calls_enabled=True,
        sip_outbound_trunk_id="trunk_abc",
        sip_outbound_caller_id="18518968743",
        env={
            "TEST_LIVEKIT_API_KEY": "key",
            "TEST_LIVEKIT_API_SECRET": "secret",
        },
    )

    preflight = manager.preflight({"destination": "18518968743"})

    assert preflight["ready"] is False
    assert preflight["missing"] == []
    assert preflight["invalid"] == ["livekit.sip_outbound_caller_id"]
    assert preflight["warnings"] == [
        "current SIP provider caller_id must be 037123124845"
    ]


def test_create_outbound_rejects_e164_destination_for_current_provider():
    manager = LiveKitSipOutboundOrchestrator()

    with pytest.raises(CallControlError) as err:
        manager.create_outbound({"destination": "+8618518968743", "dry_run": True})

    assert err.value.status_code == 400
    assert str(err.value) == (
        "destination must be a raw 11-digit domestic mobile number"
    )


def test_create_outbound_requires_destination():
    manager = LiveKitSipOutboundOrchestrator()

    with pytest.raises(CallControlError) as err:
        manager.create_outbound({"dry_run": True})

    assert err.value.status_code == 400
    assert str(err.value) == "destination is required"
