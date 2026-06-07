from __future__ import annotations

import json

from app.livekit_tts import AliyunCosyVoiceTtsSynthesizer, MockTtsSynthesizer


def test_mock_tts_synthesizer_responds_to_llm_final():
    synthesizer = MockTtsSynthesizer()

    events = synthesizer.synthesize(
        {
            "event": "llm_response_final",
            "text": "收到，我会继续按测试链路回复。",
        }
    )

    assert events == [
        {
            "event": "tts_started",
            "provider": "mock",
            "text": "收到，我会继续按测试链路回复。",
            "audio_format": "mock",
        },
        {
            "event": "tts_final",
            "provider": "mock",
            "text": "收到，我会继续按测试链路回复。",
            "audio_format": "mock",
            "audio_duration_ms": 0,
        },
    ]


def test_mock_tts_synthesizer_ignores_non_final_llm_events():
    synthesizer = MockTtsSynthesizer()

    assert synthesizer.synthesize({"event": "llm_response_started"}) == []


def test_aliyun_cosyvoice_tts_synthesizer_collects_pcm_audio():
    pcm = (1000).to_bytes(2, "little", signed=True) * 240
    connection = FakeCosyVoiceConnection(
        [
            {
                "header": {
                    "event": "task-started",
                    "task_id": "test-task-id",
                },
                "payload": {},
            },
            {
                "header": {
                    "event": "result-generated",
                    "task_id": "test-task-id",
                },
                "payload": {
                    "output": {
                        "type": "sentence-synthesis",
                    }
                },
            },
            pcm,
            {
                "header": {
                    "event": "task-finished",
                    "task_id": "test-task-id",
                },
                "payload": {"usage": {"characters": 4}},
            },
        ]
    )
    synthesizer = AliyunCosyVoiceTtsSynthesizer(
        api_key="dashscope-key",
        url="wss://dashscope.example/ws",
        model="cosyvoice-v3-flash",
        voice="longanyang",
        sample_rate=24000,
        connect_factory=lambda url, **kwargs: connection.with_kwargs(**kwargs),
        task_id_factory=lambda: "test-task-id",
    )

    events = synthesizer.synthesize(
        {
            "event": "llm_response_final",
            "text": "您好。",
        }
    )

    assert events[0] == {
        "event": "tts_started",
        "provider": "aliyun-cosyvoice",
        "text": "您好。",
        "model": "cosyvoice-v3-flash",
        "voice": "longanyang",
        "audio_format": "pcm",
        "audio_sample_rate": 24000,
    }
    assert events[1] == {
        "event": "tts_final",
        "provider": "aliyun-cosyvoice",
        "text": "您好。",
        "model": "cosyvoice-v3-flash",
        "voice": "longanyang",
        "audio_format": "pcm",
        "audio_sample_rate": 24000,
        "audio_num_channels": 1,
        "audio_byte_count": len(pcm),
        "audio_duration_ms": 10,
        "_audio_pcm": pcm,
    }
    assert connection.additional_headers == {
        "Authorization": "Bearer dashscope-key",
    }
    assert [json.loads(item)["header"]["action"] for item in connection.sent] == [
        "run-task",
        "continue-task",
        "finish-task",
    ]
    run_task = json.loads(connection.sent[0])
    assert run_task["payload"]["parameters"] == {
        "text_type": "PlainText",
        "voice": "longanyang",
        "format": "pcm",
        "sample_rate": 24000,
        "volume": 50,
        "rate": 1.0,
        "pitch": 1.0,
        "enable_ssml": False,
    }


def test_aliyun_cosyvoice_tts_synthesizer_ignores_non_final_llm_events():
    synthesizer = AliyunCosyVoiceTtsSynthesizer(
        api_key="dashscope-key",
        url="wss://dashscope.example/ws",
        model="cosyvoice-v3-flash",
        voice="longanyang",
        connect_factory=lambda url, **kwargs: FakeCosyVoiceConnection([]),
    )

    assert synthesizer.synthesize({"event": "llm_response_started"}) == []


class FakeCosyVoiceConnection:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent: list[str] = []
        self.additional_headers = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def with_kwargs(self, **kwargs):
        self.additional_headers = kwargs.get("additional_headers")
        return self

    def send(self, message: str):
        self.sent.append(message)

    def recv(self, timeout=None):
        if not self.messages:
            raise AssertionError("unexpected recv")
        return self.messages.pop(0)
