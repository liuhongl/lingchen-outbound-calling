from __future__ import annotations

import json

import pytest

from app.livekit_streaming_asr import (
    AliyunNlsStreamingAsrAdapter,
    MockStreamingAsrAdapter,
)


def test_mock_streaming_asr_emits_partial_and_final_for_speech_frames():
    adapter = MockStreamingAsrAdapter(final_after_frames=2)

    first_events = adapter.accept_frame({"frame_index": 1, "rms": 10, "peak": 20})
    second_events = adapter.accept_frame({"frame_index": 2, "rms": 12, "peak": 22})

    assert first_events == [
        {
            "event": "asr_partial",
            "provider": "mock",
            "text": "检测到语音",
            "speech_frames": 1,
            "total_frames": 1,
        }
    ]
    assert second_events == [
        {
            "event": "asr_final",
            "provider": "mock",
            "text": "mock transcript: speech_frames=2 total_frames=2",
            "speech_frames": 2,
            "total_frames": 2,
        }
    ]
    assert adapter.finish() == []


def test_mock_streaming_asr_ignores_silence():
    adapter = MockStreamingAsrAdapter(final_after_frames=1)

    assert adapter.accept_frame({"frame_index": 1, "rms": 0, "peak": 0}) == []
    assert adapter.finish() == []


def test_aliyun_nls_streaming_asr_requires_credentials():
    with pytest.raises(ValueError, match="ALIYUN_NLS_APPKEY"):
        AliyunNlsStreamingAsrAdapter(appkey="", token="token")
    with pytest.raises(ValueError, match="ALIYUN_NLS_TOKEN"):
        AliyunNlsStreamingAsrAdapter(appkey="appkey", token="")


def test_aliyun_nls_streaming_asr_sends_pcm_and_emits_callbacks():
    transcriber = FakeAliyunTranscriber()
    adapter = AliyunNlsStreamingAsrAdapter(
        appkey="appkey",
        token="token",
        transcriber_factory=lambda **kwargs: transcriber.init(**kwargs),
    )
    frame = FakeAudioFrame(
        data=(1000).to_bytes(2, "little", signed=True) * 2,
        sample_rate=16000,
        num_channels=1,
        samples_per_channel=2,
    )

    first_events = adapter.accept_audio_frame(
        frame,
        {
            "sample_rate": 16000,
            "num_channels": 1,
            "samples_per_channel": 2,
        },
    )
    transcriber.callbacks["on_result_changed"](
        json.dumps({"payload": {"result": "您好"}})
    )
    transcriber.callbacks["on_sentence_end"](
        json.dumps({"payload": {"result": "您好，请问"}})
    )
    callback_events = adapter.drain_events()
    finish_events = adapter.finish()

    assert first_events == []
    assert transcriber.started_with == {
        "aformat": "pcm",
        "sample_rate": 16000,
        "ch": 1,
        "enable_intermediate_result": True,
        "enable_punctuation_prediction": True,
        "enable_inverse_text_normalization": True,
    }
    assert transcriber.audio_chunks == [
        (1000).to_bytes(2, "little", signed=True) * 2
    ]
    assert callback_events == [
        {
            "event": "asr_partial",
            "provider": "aliyun-nls",
            "text": "您好",
        },
        {
            "event": "asr_final",
            "provider": "aliyun-nls",
            "text": "您好，请问",
        },
    ]
    assert transcriber.stopped is True
    assert finish_events == []


class FakeAliyunTranscriber:
    def __init__(self):
        self.callbacks = {}
        self.started_with = None
        self.audio_chunks = []
        self.stopped = False

    def init(self, **kwargs):
        self.url = kwargs["url"]
        self.token = kwargs["token"]
        self.appkey = kwargs["appkey"]
        self.callbacks = {
            "on_result_changed": kwargs["on_result_changed"],
            "on_sentence_end": kwargs["on_sentence_end"],
            "on_completed": kwargs["on_completed"],
            "on_error": kwargs["on_error"],
            "on_close": kwargs["on_close"],
        }
        return self

    def start(self, **kwargs):
        self.started_with = kwargs
        return True

    def send_audio(self, pcm_data):
        self.audio_chunks.append(pcm_data)
        return True

    def stop(self):
        self.stopped = True
        return True


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
