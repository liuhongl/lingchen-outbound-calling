from __future__ import annotations

import asyncio
from array import array

import pytest

from app.config import LiveKitConfig
from app.livekit_agent_worker import (
    _accept_asr_frame,
    _audio_frame_summary,
    _build_event_writer,
    _build_dialogue_policy,
    _build_pipeline_settings,
    _build_streaming_asr_adapter,
    _build_tts_synthesizer,
    _print_event,
    _write_asr_events,
    run_livekit_agent_once,
)
from app.livekit_dialog_policy import OpenAICompatibleDialoguePolicy
from app.livekit_streaming_asr import AliyunNlsStreamingAsrAdapter
from app.livekit_tts import AliyunCosyVoiceTtsSynthesizer


def test_run_livekit_agent_once_connects_to_requested_room(monkeypatch):
    monkeypatch.setenv("TEST_LIVEKIT_API_KEY", "api-key")
    monkeypatch.setenv("TEST_LIVEKIT_API_SECRET", "secret")
    rtc = FakeRtcModule()
    events: list[dict[str, object]] = []

    result = asyncio.run(
        run_livekit_agent_once(
            LiveKitConfig(
                enabled=True,
                url="wss://livekit.example",
                api_key_env="TEST_LIVEKIT_API_KEY",
                api_secret_env="TEST_LIVEKIT_API_SECRET",
                web_debug_room_prefix="web-debug",
            ),
            room_name="demo",
            identity="agent-worker",
            name="LiveKit Agent",
            duration_seconds=0,
            rtc_module=rtc,
            on_event=events.append,
            now=1_700_000_000,
        )
    )

    room = rtc.created_room
    assert room is not None
    assert room.connected_url == "wss://livekit.example"
    assert room.connected_token.count(".") == 2
    assert room.disconnected is True
    assert result["room"] == "web-debug-demo"
    assert result["identity"] == "agent-worker"
    assert events == [
        {
            "event": "connected",
            "room": "web-debug-demo",
            "identity": "agent-worker",
            "remote_participants": [],
        },
        {
            "event": "disconnected",
            "room": "web-debug-demo",
            "identity": "agent-worker",
        },
    ]


def test_run_livekit_agent_once_reports_audio_frames(monkeypatch):
    monkeypatch.setenv("TEST_LIVEKIT_API_KEY", "api-key")
    monkeypatch.setenv("TEST_LIVEKIT_API_SECRET", "secret")
    rtc = FakeRtcModule()
    rtc.track_to_subscribe = FakeTrack(kind=rtc.TrackKind.KIND_AUDIO)
    events: list[dict[str, object]] = []

    asyncio.run(
        run_livekit_agent_once(
            LiveKitConfig(
                enabled=True,
                url="wss://livekit.example",
                api_key_env="TEST_LIVEKIT_API_KEY",
                api_secret_env="TEST_LIVEKIT_API_SECRET",
                web_debug_room_prefix="web-debug",
            ),
            room_name="demo",
            identity="agent-worker",
            duration_seconds=0,
            audio_frame_limit=2,
            rtc_module=rtc,
            on_event=events.append,
            now=1_700_000_000,
        )
    )

    event_names = [event["event"] for event in events]
    assert event_names == [
        "audio_track_subscribed",
        "connected",
        "audio_frame",
        "audio_frame",
        "audio_stream_completed",
        "disconnected",
    ]
    assert events[0]["participant"] == "browser-user"
    assert events[2] == {
        "event": "audio_frame",
        "room": "web-debug-demo",
        "identity": "agent-worker",
        "participant": "browser-user",
        "frame_index": 1,
        "sample_rate": 16000,
        "num_channels": 1,
        "samples_per_channel": 4,
        "rms": 1000,
        "peak": 1000,
    }


def test_run_livekit_agent_once_ignores_duplicate_audio_tracks_for_participant(
    monkeypatch,
):
    monkeypatch.setenv("TEST_LIVEKIT_API_KEY", "api-key")
    monkeypatch.setenv("TEST_LIVEKIT_API_SECRET", "secret")
    rtc = FakeRtcModule()
    rtc.tracks_to_subscribe = [
        FakeTrack(kind=rtc.TrackKind.KIND_AUDIO),
        FakeTrack(kind=rtc.TrackKind.KIND_AUDIO),
    ]
    events: list[dict[str, object]] = []

    asyncio.run(
        run_livekit_agent_once(
            LiveKitConfig(
                enabled=True,
                url="wss://livekit.example",
                api_key_env="TEST_LIVEKIT_API_KEY",
                api_secret_env="TEST_LIVEKIT_API_SECRET",
                web_debug_room_prefix="web-debug",
            ),
            room_name="demo",
            identity="agent-worker",
            duration_seconds=0,
            audio_frame_limit=2,
            rtc_module=rtc,
            on_event=events.append,
            now=1_700_000_000,
        )
    )

    assert [event["event"] for event in events] == [
        "audio_track_subscribed",
        "audio_track_ignored",
        "connected",
        "audio_frame",
        "audio_frame",
        "audio_stream_completed",
        "disconnected",
    ]
    assert events[1] == {
        "event": "audio_track_ignored",
        "room": "web-debug-demo",
        "identity": "agent-worker",
        "participant": "browser-user",
        "reason": "duplicate_participant_audio",
    }


def test_run_livekit_agent_once_can_emit_mock_asr_events(monkeypatch):
    monkeypatch.setenv("TEST_LIVEKIT_API_KEY", "api-key")
    monkeypatch.setenv("TEST_LIVEKIT_API_SECRET", "secret")
    rtc = FakeRtcModule()
    rtc.track_to_subscribe = FakeTrack(kind=rtc.TrackKind.KIND_AUDIO)
    events: list[dict[str, object]] = []

    asyncio.run(
        run_livekit_agent_once(
            LiveKitConfig(
                enabled=True,
                url="wss://livekit.example",
                api_key_env="TEST_LIVEKIT_API_KEY",
                api_secret_env="TEST_LIVEKIT_API_SECRET",
                web_debug_room_prefix="web-debug",
            ),
            room_name="demo",
            identity="agent-worker",
            duration_seconds=0,
            audio_frame_limit=2,
            asr_provider="mock",
            mock_asr_final_after_frames=2,
            rtc_module=rtc,
            on_event=events.append,
            now=1_700_000_000,
        )
    )

    assert [event["event"] for event in events] == [
        "audio_track_subscribed",
        "connected",
        "audio_frame",
        "asr_partial",
        "audio_frame",
        "asr_final",
        "audio_stream_completed",
        "disconnected",
    ]
    assert events[3] == {
        "event": "asr_partial",
        "room": "web-debug-demo",
        "identity": "agent-worker",
        "participant": "browser-user",
        "provider": "mock",
        "text": "检测到语音",
        "speech_frames": 1,
        "total_frames": 1,
    }
    assert events[5] == {
        "event": "asr_final",
        "room": "web-debug-demo",
        "identity": "agent-worker",
        "participant": "browser-user",
        "provider": "mock",
        "text": "mock transcript: speech_frames=2 total_frames=2",
        "speech_frames": 2,
        "total_frames": 2,
    }


def test_run_livekit_agent_once_can_emit_mock_dialogue_events(monkeypatch):
    monkeypatch.setenv("TEST_LIVEKIT_API_KEY", "api-key")
    monkeypatch.setenv("TEST_LIVEKIT_API_SECRET", "secret")
    rtc = FakeRtcModule()
    rtc.track_to_subscribe = FakeTrack(kind=rtc.TrackKind.KIND_AUDIO)
    events: list[dict[str, object]] = []

    asyncio.run(
        run_livekit_agent_once(
            LiveKitConfig(
                enabled=True,
                url="wss://livekit.example",
                api_key_env="TEST_LIVEKIT_API_KEY",
                api_secret_env="TEST_LIVEKIT_API_SECRET",
                web_debug_room_prefix="web-debug",
            ),
            room_name="demo",
            identity="agent-worker",
            duration_seconds=0,
            audio_frame_limit=2,
            asr_provider="mock",
            mock_asr_final_after_frames=2,
            dialog_provider="mock",
            rtc_module=rtc,
            on_event=events.append,
            now=1_700_000_000,
        )
    )

    assert [event["event"] for event in events] == [
        "audio_track_subscribed",
        "connected",
        "audio_frame",
        "asr_partial",
        "audio_frame",
        "asr_final",
        "llm_response_started",
        "llm_response_final",
        "audio_stream_completed",
        "disconnected",
    ]
    assert events[6] == {
        "event": "llm_response_started",
        "room": "web-debug-demo",
        "identity": "agent-worker",
        "participant": "browser-user",
        "provider": "mock",
        "input_text": "mock transcript: speech_frames=2 total_frames=2",
    }
    assert events[7] == {
        "event": "llm_response_final",
        "room": "web-debug-demo",
        "identity": "agent-worker",
        "participant": "browser-user",
        "provider": "mock",
        "input_text": "mock transcript: speech_frames=2 total_frames=2",
        "text": "收到，我会继续按测试链路回复。",
    }


def test_run_livekit_agent_once_can_emit_mock_tts_events(monkeypatch):
    monkeypatch.setenv("TEST_LIVEKIT_API_KEY", "api-key")
    monkeypatch.setenv("TEST_LIVEKIT_API_SECRET", "secret")
    rtc = FakeRtcModule()
    rtc.track_to_subscribe = FakeTrack(kind=rtc.TrackKind.KIND_AUDIO)
    events: list[dict[str, object]] = []

    asyncio.run(
        run_livekit_agent_once(
            LiveKitConfig(
                enabled=True,
                url="wss://livekit.example",
                api_key_env="TEST_LIVEKIT_API_KEY",
                api_secret_env="TEST_LIVEKIT_API_SECRET",
                web_debug_room_prefix="web-debug",
            ),
            room_name="demo",
            identity="agent-worker",
            duration_seconds=0,
            audio_frame_limit=2,
            asr_provider="mock",
            mock_asr_final_after_frames=2,
            dialog_provider="mock",
            tts_provider="mock",
            rtc_module=rtc,
            on_event=events.append,
            now=1_700_000_000,
        )
    )

    assert [event["event"] for event in events] == [
        "audio_track_subscribed",
        "connected",
        "audio_frame",
        "asr_partial",
        "audio_frame",
        "asr_final",
        "llm_response_started",
        "llm_response_final",
        "tts_started",
        "tts_final",
        "audio_stream_completed",
        "disconnected",
    ]
    assert events[8] == {
        "event": "tts_started",
        "room": "web-debug-demo",
        "identity": "agent-worker",
        "participant": "browser-user",
        "provider": "mock",
        "text": "收到，我会继续按测试链路回复。",
        "audio_format": "mock",
    }
    assert events[9] == {
        "event": "tts_final",
        "room": "web-debug-demo",
        "identity": "agent-worker",
        "participant": "browser-user",
        "provider": "mock",
        "text": "收到，我会继续按测试链路回复。",
        "audio_format": "mock",
        "audio_duration_ms": 0,
    }


def test_run_livekit_agent_once_can_publish_mock_tts_audio(monkeypatch):
    monkeypatch.setenv("TEST_LIVEKIT_API_KEY", "api-key")
    monkeypatch.setenv("TEST_LIVEKIT_API_SECRET", "secret")
    rtc = FakeRtcModule()
    rtc.track_to_subscribe = FakeTrack(kind=rtc.TrackKind.KIND_AUDIO)
    events: list[dict[str, object]] = []

    asyncio.run(
        run_livekit_agent_once(
            LiveKitConfig(
                enabled=True,
                url="wss://livekit.example",
                api_key_env="TEST_LIVEKIT_API_KEY",
                api_secret_env="TEST_LIVEKIT_API_SECRET",
                web_debug_room_prefix="web-debug",
            ),
            room_name="demo",
            identity="agent-worker",
            duration_seconds=0,
            audio_frame_limit=2,
            asr_provider="mock",
            mock_asr_final_after_frames=2,
            dialog_provider="mock",
            tts_provider="mock",
            publish_mock_tts_audio=True,
            rtc_module=rtc,
            on_event=events.append,
            now=1_700_000_000,
        )
    )

    assert [event["event"] for event in events] == [
        "audio_track_subscribed",
        "connected",
        "audio_frame",
        "asr_partial",
        "audio_frame",
        "asr_final",
        "llm_response_started",
        "llm_response_final",
        "tts_started",
        "tts_final",
        "tts_audio_publish_started",
        "tts_audio_publish_finished",
        "audio_stream_completed",
        "disconnected",
    ]
    assert events[10] == {
        "event": "tts_audio_publish_started",
        "room": "web-debug-demo",
        "identity": "agent-worker",
        "participant": "browser-user",
        "track_name": "mock-tts-audio",
    }
    assert events[11] == {
        "event": "tts_audio_publish_finished",
        "room": "web-debug-demo",
        "identity": "agent-worker",
        "participant": "browser-user",
        "track_name": "mock-tts-audio",
        "sample_rate": 48000,
        "num_channels": 1,
        "samples_per_channel": 4800,
        "audio_duration_ms": 100,
    }
    room = rtc.created_room
    assert room is not None
    assert room.local_participant.published_track_names == ["mock-tts-audio"]
    assert len(rtc.created_audio_sources) == 1
    assert rtc.created_audio_sources[0].captured_frames[0].samples_per_channel == 4800
    assert rtc.created_audio_sources[0].closed is True


def test_mock_pipeline_preset_enables_full_mock_chain():
    settings = _build_pipeline_settings(
        pipeline="mock",
        asr_provider="none",
        dialog_provider="none",
        tts_provider="none",
        publish_mock_tts_audio=False,
    )

    assert settings == {
        "asr_provider": "mock",
        "dialog_provider": "mock",
        "tts_provider": "mock",
        "publish_mock_tts_audio": True,
    }


def test_public_cloud_pipeline_preset_enables_cloud_chain():
    settings = _build_pipeline_settings(
        pipeline="public-cloud",
        asr_provider="none",
        dialog_provider="none",
        tts_provider="none",
        publish_mock_tts_audio=True,
    )

    assert settings == {
        "asr_provider": "aliyun-nls",
        "dialog_provider": "openai-compatible",
        "tts_provider": "aliyun-cosyvoice",
        "publish_mock_tts_audio": False,
    }


def test_build_streaming_asr_adapter_accepts_aliyun_nls_env(monkeypatch):
    monkeypatch.setenv("ALIYUN_NLS_APPKEY", "appkey")
    monkeypatch.setenv("ALIYUN_NLS_TOKEN", "token")
    monkeypatch.setenv("ALIYUN_NLS_URL", "wss://nls.example/ws/v1")

    adapter = _build_streaming_asr_adapter(
        "aliyun-nls",
        mock_asr_final_after_frames=5,
    )

    assert isinstance(adapter, AliyunNlsStreamingAsrAdapter)
    assert adapter.appkey == "appkey"
    assert adapter.token == "token"
    assert adapter.url == "wss://nls.example/ws/v1"


def test_build_streaming_asr_adapter_requires_aliyun_nls_credentials(monkeypatch):
    monkeypatch.delenv("ALIYUN_NLS_APPKEY", raising=False)
    monkeypatch.delenv("ALIYUN_NLS_TOKEN", raising=False)

    with pytest.raises(ValueError, match="ALIYUN_NLS_APPKEY"):
        _build_streaming_asr_adapter(
            "aliyun-nls",
            mock_asr_final_after_frames=5,
        )


def test_build_dialogue_policy_accepts_openai_compatible_env(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_MODEL", "qwen-test")

    policy = _build_dialogue_policy("openai-compatible")

    assert isinstance(policy, OpenAICompatibleDialoguePolicy)
    assert policy.api_key == "dashscope-key"
    assert policy.base_url == "https://llm.example/v1"
    assert policy.model == "qwen-test"


def test_build_dialogue_policy_requires_openai_compatible_key(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    with pytest.raises(ValueError, match="DASHSCOPE_API_KEY"):
        _build_dialogue_policy("openai-compatible")


def test_build_tts_synthesizer_accepts_aliyun_cosyvoice_env(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")
    monkeypatch.setenv("ALIYUN_TTS_WS_URL", "wss://tts.example/ws")
    monkeypatch.setenv("ALIYUN_TTS_MODEL", "cosyvoice-test")
    monkeypatch.setenv("ALIYUN_TTS_VOICE", "voice-test")
    monkeypatch.setenv("ALIYUN_TTS_SAMPLE_RATE", "16000")

    synthesizer = _build_tts_synthesizer("aliyun-cosyvoice")

    assert isinstance(synthesizer, AliyunCosyVoiceTtsSynthesizer)
    assert synthesizer.api_key == "dashscope-key"
    assert synthesizer.url == "wss://tts.example/ws"
    assert synthesizer.model == "cosyvoice-test"
    assert synthesizer.voice == "voice-test"
    assert synthesizer.sample_rate == 16000


def test_build_tts_synthesizer_requires_aliyun_cosyvoice_key(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("ALIYUN_TTS_API_KEY", raising=False)

    with pytest.raises(ValueError, match="DASHSCOPE_API_KEY"):
        _build_tts_synthesizer("aliyun-cosyvoice")


def test_openai_compatible_dialogue_policy_emits_llm_events():
    class FakeCompletions:
        def __init__(self):
            self.requests = []

        def create(self, **kwargs):
            self.requests.append(kwargs)
            message = type("Message", (), {"content": "您好，请问需要查询哪套房的物业费？"})
            choice = type("Choice", (), {"message": message})
            return type("Response", (), {"choices": [choice]})

    class FakeClient:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    client = FakeClient()
    policy = OpenAICompatibleDialoguePolicy(
        api_key="dashscope-key",
        base_url="https://llm.example/v1",
        model="qwen-test",
        client_factory=lambda **kwargs: client,
    )

    events = policy.respond({"event": "asr_final", "text": "你好，我想咨询物业费"})

    assert events == [
        {
            "event": "llm_response_started",
            "provider": "openai-compatible",
            "input_text": "你好，我想咨询物业费",
            "model": "qwen-test",
        },
        {
            "event": "llm_response_final",
            "provider": "openai-compatible",
            "input_text": "你好，我想咨询物业费",
            "text": "您好，请问需要查询哪套房的物业费？",
            "model": "qwen-test",
        },
    ]
    assert client.chat.completions.requests == [
        {
            "model": "qwen-test",
            "messages": [
                {
                    "role": "system",
                    "content": "你是物业费催收外呼场景的中文语音助手，回复要简短、自然、适合电话口播。",
                },
                {"role": "user", "content": "你好，我想咨询物业费"},
            ],
            "temperature": 0.3,
            "max_tokens": 120,
        }
    ]


def test_accept_asr_frame_prefers_raw_audio_frame_method():
    adapter = FakeRawAudioAsrAdapter()
    frame = FakeAudioFrame(
        data=(1000).to_bytes(2, "little", signed=True),
        sample_rate=16000,
        num_channels=1,
        samples_per_channel=1,
    )
    summary = {
        "sample_rate": 16000,
        "num_channels": 1,
        "samples_per_channel": 1,
        "rms": 1000,
        "peak": 1000,
    }

    events = _accept_asr_frame(adapter, frame, summary)

    assert events == [{"event": "ok"}]
    assert adapter.frames == [frame]
    assert adapter.summaries == [summary]


def test_run_livekit_agent_once_accepts_mock_pipeline_settings(monkeypatch):
    monkeypatch.setenv("TEST_LIVEKIT_API_KEY", "api-key")
    monkeypatch.setenv("TEST_LIVEKIT_API_SECRET", "secret")
    rtc = FakeRtcModule()
    rtc.track_to_subscribe = FakeTrack(kind=rtc.TrackKind.KIND_AUDIO)
    events: list[dict[str, object]] = []
    settings = _build_pipeline_settings(
        pipeline="mock",
        asr_provider="none",
        dialog_provider="none",
        tts_provider="none",
        publish_mock_tts_audio=False,
    )

    asyncio.run(
        run_livekit_agent_once(
            LiveKitConfig(
                enabled=True,
                url="wss://livekit.example",
                api_key_env="TEST_LIVEKIT_API_KEY",
                api_secret_env="TEST_LIVEKIT_API_SECRET",
                web_debug_room_prefix="web-debug",
            ),
            room_name="demo",
            identity="agent-worker",
            duration_seconds=0,
            audio_frame_limit=2,
            mock_asr_final_after_frames=2,
            rtc_module=rtc,
            on_event=events.append,
            now=1_700_000_000,
            **settings,
        )
    )

    assert "tts_audio_publish_finished" in [event["event"] for event in events]
    assert rtc.created_room is not None
    assert rtc.created_room.local_participant.published_track_names == [
        "mock-tts-audio"
    ]


def test_write_asr_events_publishes_real_tts_pcm_without_logging_bytes():
    rtc = FakeRtcModule()
    room = FakeRoom(rtc)
    pcm = (1000).to_bytes(2, "little", signed=True) * 480
    events: list[dict[str, object]] = []

    asyncio.run(
        _write_asr_events(
            rtc,
            room,
            {
                "room": "web-debug-demo",
                "identity": "agent-worker",
                "participant": "browser-user",
            },
            [{"event": "asr_final", "text": "你好"}],
            writer=events.append,
            dialogue_policy=FakeDialoguePolicy(),
            tts_synthesizer=FakePcmTtsSynthesizer(pcm),
            publish_mock_tts_audio=False,
        )
    )

    assert "_audio_pcm" not in events[2]
    assert events[2] == {
        "event": "tts_final",
        "room": "web-debug-demo",
        "identity": "agent-worker",
        "participant": "browser-user",
        "provider": "fake-tts",
        "text": "您好",
        "audio_format": "pcm",
        "audio_sample_rate": 24000,
        "audio_num_channels": 1,
        "audio_byte_count": len(pcm),
        "audio_duration_ms": 20,
    }
    assert events[3]["event"] == "tts_audio_publish_started"
    assert events[4]["event"] == "tts_audio_publish_finished"
    assert events[4]["track_name"] == "tts-audio"
    assert room.local_participant.published_track_names == ["tts-audio"]
    assert len(rtc.created_audio_sources) == 1
    assert rtc.created_audio_sources[0].captured_frames[0].data == pcm


def test_audio_frame_summary_accepts_int16_memoryview():
    summary = _audio_frame_summary(
        FakeAudioFrame(
            data=memoryview(array("h", [1000, -1000, 1000, -1000])),
            sample_rate=16000,
            num_channels=1,
            samples_per_channel=4,
        )
    )

    assert summary == {
        "sample_rate": 16000,
        "num_channels": 1,
        "samples_per_channel": 4,
        "rms": 1000,
        "peak": 1000,
    }


def test_print_event_skips_audio_frame_by_default(capsys):
    _print_event({"event": "audio_frame", "frame_index": 1})
    _print_event({"event": "asr_final", "text": "你好"})

    assert capsys.readouterr().out == '{"event": "asr_final", "text": "你好"}\n'


def test_event_writer_posts_observable_events_without_private_fields():
    printed: list[dict[str, object]] = []
    posted: list[tuple[str, dict[str, object]]] = []
    writer = _build_event_writer(
        "http://127.0.0.1:9100/livekit/web-debug/events",
        base_writer=printed.append,
        post_event=lambda url, event: posted.append((url, event)),
    )

    writer({"event": "audio_frame", "frame_index": 1})
    writer(
        {
            "event": "tts_final",
            "room": "web-debug-demo",
            "text": "您好",
            "audio_byte_count": 123,
            "_audio_pcm": b"raw-audio",
        }
    )

    assert printed == [
        {"event": "audio_frame", "frame_index": 1},
        {
            "event": "tts_final",
            "room": "web-debug-demo",
            "text": "您好",
            "audio_byte_count": 123,
            "_audio_pcm": b"raw-audio",
        },
    ]
    assert posted == [
        (
            "http://127.0.0.1:9100/livekit/web-debug/events",
            {
                "event": "tts_final",
                "room": "web-debug-demo",
                "text": "您好",
                "audio_byte_count": 123,
            },
        )
    ]


def test_event_writer_reports_sink_errors_to_base_writer():
    printed: list[dict[str, object]] = []

    def failing_post(url, event):
        raise RuntimeError("sink down")

    writer = _build_event_writer(
        "http://127.0.0.1:9100/livekit/web-debug/events",
        base_writer=printed.append,
        post_event=failing_post,
    )

    writer({"event": "asr_final", "room": "web-debug-demo", "text": "你好"})

    assert printed == [
        {"event": "asr_final", "room": "web-debug-demo", "text": "你好"},
        {
            "event": "event_sink_error",
            "event_sink_url": "http://127.0.0.1:9100/livekit/web-debug/events",
            "error": "sink down",
        },
    ]


class FakeRtcModule:
    def __init__(self):
        self.created_room: FakeRoom | None = None
        self.track_to_subscribe: FakeTrack | None = None
        self.tracks_to_subscribe: list[FakeTrack] = []
        self.created_audio_sources: list[FakeAudioSource] = []
        self.TrackKind = FakeTrackKind
        self.TrackSource = FakeTrackSource
        self.LocalAudioTrack = FakeLocalAudioTrackFactory
        self.TrackPublishOptions = FakeTrackPublishOptions

    def Room(self):
        self.created_room = FakeRoom(self)
        return self.created_room

    def RoomOptions(self, *, auto_subscribe: bool):
        return {"auto_subscribe": auto_subscribe}

    def AudioStream(self, track, *, sample_rate: int, num_channels: int):
        return FakeAudioStream(
            [
                FakeAudioFrameEvent(
                    FakeAudioFrame(
                        data=(1000).to_bytes(2, "little", signed=True) * 4,
                        sample_rate=sample_rate,
                        num_channels=num_channels,
                        samples_per_channel=4,
                    )
                ),
                FakeAudioFrameEvent(
                    FakeAudioFrame(
                        data=(-2000).to_bytes(2, "little", signed=True) * 4,
                        sample_rate=sample_rate,
                        num_channels=num_channels,
                        samples_per_channel=4,
                    )
                ),
            ]
        )

    def AudioSource(self, sample_rate: int, num_channels: int):
        source = FakeAudioSource(sample_rate=sample_rate, num_channels=num_channels)
        self.created_audio_sources.append(source)
        return source

    def AudioFrame(
        self,
        *,
        data: bytes,
        sample_rate: int,
        num_channels: int,
        samples_per_channel: int,
    ):
        return FakeAudioFrame(
            data=data,
            sample_rate=sample_rate,
            num_channels=num_channels,
            samples_per_channel=samples_per_channel,
        )


class FakeRawAudioAsrAdapter:
    def __init__(self):
        self.frames = []
        self.summaries = []

    def accept_audio_frame(self, frame, summary):
        self.frames.append(frame)
        self.summaries.append(summary)
        return [{"event": "ok"}]


class FakeDialoguePolicy:
    def respond(self, asr_event):
        return [
            {
                "event": "llm_response_final",
                "provider": "fake-llm",
                "input_text": asr_event["text"],
                "text": "您好",
            }
        ]


class FakePcmTtsSynthesizer:
    def __init__(self, pcm: bytes):
        self.pcm = pcm

    def synthesize(self, llm_event):
        return [
            {
                "event": "tts_final",
                "provider": "fake-tts",
                "text": llm_event["text"],
                "audio_format": "pcm",
                "audio_sample_rate": 24000,
                "audio_num_channels": 1,
                "audio_byte_count": len(self.pcm),
                "audio_duration_ms": 20,
                "_audio_pcm": self.pcm,
            }
        ]


class FakeRoom:
    def __init__(self, rtc: FakeRtcModule):
        self.rtc = rtc
        self.connected_url = ""
        self.connected_token = ""
        self.remote_participants = {}
        self.local_participant = FakeLocalParticipant()
        self.disconnected = False
        self._handlers = {}

    def on(self, event: str, handler):
        self._handlers[event] = handler
        return handler

    async def connect(self, url: str, token: str, options):
        self.connected_url = url
        self.connected_token = token
        tracks = list(self.rtc.tracks_to_subscribe)
        if self.rtc.track_to_subscribe is not None:
            tracks.append(self.rtc.track_to_subscribe)
        for track in tracks:
            self._handlers["track_subscribed"](
                track,
                object(),
                FakeParticipant("browser-user"),
            )

    async def disconnect(self):
        self.disconnected = True


class FakeTrackKind:
    KIND_AUDIO = "audio"
    KIND_VIDEO = "video"


class FakeTrackSource:
    SOURCE_MICROPHONE = "microphone"


class FakeTrackPublishOptions:
    def __init__(self):
        self.source = None


class FakeTrack:
    def __init__(self, *, kind: str):
        self.kind = kind


class FakeLocalAudioTrackFactory:
    @staticmethod
    def create_audio_track(name: str, source):
        return FakeLocalAudioTrack(name=name, source=source)


class FakeLocalAudioTrack:
    def __init__(self, *, name: str, source):
        self.name = name
        self.source = source


class FakeLocalParticipant:
    def __init__(self):
        self.published_track_names: list[str] = []
        self.publish_options: list[FakeTrackPublishOptions] = []

    async def publish_track(self, track, options):
        self.published_track_names.append(track.name)
        self.publish_options.append(options)
        return object()


class FakeParticipant:
    def __init__(self, identity: str):
        self.identity = identity


class FakeAudioFrame:
    def __init__(
        self,
        *,
        data: bytes,
        sample_rate: int,
        num_channels: int,
        samples_per_channel: int,
    ):
        self.data = data
        self.sample_rate = sample_rate
        self.num_channels = num_channels
        self.samples_per_channel = samples_per_channel


class FakeAudioFrameEvent:
    def __init__(self, frame: FakeAudioFrame):
        self.frame = frame


class FakeAudioSource:
    def __init__(self, *, sample_rate: int, num_channels: int):
        self.sample_rate = sample_rate
        self.num_channels = num_channels
        self.captured_frames: list[FakeAudioFrame] = []
        self.playout_waited = False
        self.closed = False

    async def capture_frame(self, frame: FakeAudioFrame):
        self.captured_frames.append(frame)

    async def wait_for_playout(self):
        self.playout_waited = True

    async def aclose(self):
        self.closed = True


class FakeAudioStream:
    def __init__(self, events: list[FakeAudioFrameEvent]):
        self.events = events
        self.closed = False

    def __aiter__(self):
        self._iterator = iter(self.events)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration:
            raise StopAsyncIteration

    async def aclose(self):
        self.closed = True
