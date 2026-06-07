from __future__ import annotations

from app.livekit_dialog_policy import MockDialoguePolicy


def test_mock_dialogue_policy_responds_to_asr_final():
    policy = MockDialoguePolicy()

    events = policy.respond(
        {
            "event": "asr_final",
            "text": "mock transcript: speech_frames=2 total_frames=2",
        }
    )

    assert events == [
        {
            "event": "llm_response_started",
            "provider": "mock",
            "input_text": "mock transcript: speech_frames=2 total_frames=2",
        },
        {
            "event": "llm_response_final",
            "provider": "mock",
            "input_text": "mock transcript: speech_frames=2 total_frames=2",
            "text": "收到，我会继续按测试链路回复。",
        },
    ]


def test_mock_dialogue_policy_ignores_non_final_asr_events():
    policy = MockDialoguePolicy()

    assert policy.respond({"event": "asr_partial", "text": "检测到语音"}) == []
