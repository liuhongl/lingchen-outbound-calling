from __future__ import annotations

from app.livekit_call_turns import build_livekit_turns


def test_build_livekit_turns_groups_asr_llm_and_tts_events():
    turns = build_livekit_turns(
        [
            {
                "sequence": 1,
                "receivedAtMs": 1000,
                "event": "connected",
                "room": "web-debug-demo",
            },
            {
                "sequence": 2,
                "receivedAtMs": 1100,
                "event": "asr_partial",
                "room": "web-debug-demo",
                "text": "你好",
            },
            {
                "sequence": 3,
                "receivedAtMs": 1200,
                "event": "asr_final",
                "room": "web-debug-demo",
                "participant": "browser-user",
                "provider": "aliyun-nls",
                "text": "你好，我想咨询物业费。",
            },
            {
                "sequence": 4,
                "receivedAtMs": 1500,
                "event": "llm_response_final",
                "room": "web-debug-demo",
                "provider": "openai-compatible",
                "model": "qwen-plus",
                "input_text": "你好，我想咨询物业费。",
                "text": "您好，请问您想了解哪套房？",
            },
            {
                "sequence": 5,
                "receivedAtMs": 1800,
                "event": "tts_final",
                "room": "web-debug-demo",
                "provider": "aliyun-cosyvoice",
                "model": "cosyvoice-v3-flash",
                "voice": "longanyang",
                "audio_duration_ms": 2400,
                "audio_byte_count": 115200,
            },
            {
                "sequence": 6,
                "receivedAtMs": 4300,
                "event": "tts_audio_publish_finished",
                "room": "web-debug-demo",
                "track_name": "tts-audio",
                "audio_duration_ms": 2400,
            },
        ]
    )

    assert turns == [
        {
            "turn_index": 1,
            "room": "web-debug-demo",
            "participant": "browser-user",
            "started_at_ms": 1200,
            "updated_at_ms": 4300,
            "user_text": "你好，我想咨询物业费。",
            "asr": {
                "provider": "aliyun-nls",
                "sequence": 3,
                "receivedAtMs": 1200,
            },
            "assistant_text": "您好，请问您想了解哪套房？",
            "llm": {
                "provider": "openai-compatible",
                "model": "qwen-plus",
                "sequence": 4,
                "receivedAtMs": 1500,
            },
            "tts": {
                "provider": "aliyun-cosyvoice",
                "model": "cosyvoice-v3-flash",
                "voice": "longanyang",
                "audio_duration_ms": 2400,
                "audio_byte_count": 115200,
                "sequence": 5,
                "receivedAtMs": 1800,
            },
            "audio_publish": {
                "track_name": "tts-audio",
                "audio_duration_ms": 2400,
                "sequence": 6,
                "receivedAtMs": 4300,
            },
            "latency_ms": {
                "asr_to_llm": 300,
                "llm_to_tts": 300,
                "tts_to_publish": 2500,
            },
        }
    ]


def test_build_livekit_turns_starts_a_new_turn_for_each_asr_final():
    turns = build_livekit_turns(
        [
            {"sequence": 1, "receivedAtMs": 1000, "event": "asr_final", "text": "第一句"},
            {
                "sequence": 2,
                "receivedAtMs": 1100,
                "event": "llm_response_final",
                "text": "第一句回复",
            },
            {"sequence": 3, "receivedAtMs": 2000, "event": "asr_final", "text": "第二句"},
        ]
    )

    assert [turn["user_text"] for turn in turns] == ["第一句", "第二句"]
    assert turns[0]["assistant_text"] == "第一句回复"
    assert "assistant_text" not in turns[1]
