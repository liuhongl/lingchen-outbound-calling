from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from typing import Any


DEFAULT_ALIYUN_NLS_URL = "wss://nls-gateway-cn-shanghai.aliyuncs.com/ws/v1"


class MockStreamingAsrAdapter:
    """Diagnostic ASR boundary for LiveKit audio-frame integration tests."""

    def __init__(
        self,
        *,
        final_after_frames: int = 5,
        speech_rms_threshold: int = 1,
    ) -> None:
        if final_after_frames <= 0:
            raise ValueError("final_after_frames must be positive")
        self.final_after_frames = final_after_frames
        self.speech_rms_threshold = speech_rms_threshold
        self.total_frames = 0
        self.speech_frames = 0
        self._partial_emitted = False
        self._final_emitted = False

    def accept_frame(self, frame: Mapping[str, object]) -> list[dict[str, object]]:
        self.total_frames += 1
        rms = int(frame.get("rms", 0))
        peak = int(frame.get("peak", 0))
        if rms < self.speech_rms_threshold and peak < self.speech_rms_threshold:
            return []

        self.speech_frames += 1
        events: list[dict[str, object]] = []
        if not self._partial_emitted:
            self._partial_emitted = True
            events.append(self._build_event("asr_partial", "检测到语音"))
        if self.speech_frames >= self.final_after_frames:
            events.extend(self.finish())
        return events

    def finish(self) -> list[dict[str, object]]:
        if self._final_emitted or self.speech_frames <= 0:
            return []
        self._final_emitted = True
        return [
            self._build_event(
                "asr_final",
                (
                    "mock transcript: "
                    f"speech_frames={self.speech_frames} "
                    f"total_frames={self.total_frames}"
                ),
            )
        ]

    def _build_event(self, event: str, text: str) -> dict[str, object]:
        return {
            "event": event,
            "provider": "mock",
            "text": text,
            "speech_frames": self.speech_frames,
            "total_frames": self.total_frames,
        }


class AliyunNlsStreamingAsrAdapter:
    """Streaming ASR adapter backed by Aliyun NLS realtime transcription."""

    def __init__(
        self,
        *,
        appkey: str,
        token: str,
        url: str = DEFAULT_ALIYUN_NLS_URL,
        transcriber_factory=None,
    ) -> None:
        if not appkey:
            raise ValueError("missing ALIYUN_NLS_APPKEY")
        if not token:
            raise ValueError("missing ALIYUN_NLS_TOKEN")
        self.appkey = appkey
        self.token = token
        self.url = url
        self._transcriber_factory = transcriber_factory or _create_aliyun_transcriber
        self._transcriber = None
        self._started = False
        self._lock = threading.Lock()
        self._events: list[dict[str, object]] = []

    def accept_audio_frame(
        self,
        frame,
        frame_summary: Mapping[str, object],
    ) -> list[dict[str, object]]:
        self._ensure_started(frame_summary)
        pcm_data = _audio_frame_pcm_bytes(frame)
        if pcm_data:
            self._transcriber.send_audio(pcm_data)
        return self.drain_events()

    def drain_events(self) -> list[dict[str, object]]:
        with self._lock:
            events = list(self._events)
            self._events.clear()
        return events

    def finish(self) -> list[dict[str, object]]:
        if self._started and self._transcriber is not None:
            self._transcriber.stop()
        self._started = False
        return self.drain_events()

    def _ensure_started(self, frame_summary: Mapping[str, object]) -> None:
        if self._started:
            return
        self._transcriber = self._transcriber_factory(
            url=self.url,
            token=self.token,
            appkey=self.appkey,
            on_result_changed=self._on_result_changed,
            on_sentence_end=self._on_sentence_end,
            on_completed=self._on_completed,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._transcriber.start(
            aformat="pcm",
            sample_rate=int(frame_summary["sample_rate"]),
            ch=int(frame_summary["num_channels"]),
            enable_intermediate_result=True,
            enable_punctuation_prediction=True,
            enable_inverse_text_normalization=True,
        )
        self._started = True

    def _on_result_changed(self, message, *args) -> None:
        text = _extract_aliyun_text(message)
        if text:
            self._append_event("asr_partial", text)

    def _on_sentence_end(self, message, *args) -> None:
        text = _extract_aliyun_text(message)
        if text:
            self._append_event("asr_final", text)

    def _on_completed(self, message, *args) -> None:
        text = _extract_aliyun_text(message)
        if text:
            self._append_event("asr_final", text)

    def _on_error(self, message, *args) -> None:
        self._append_event(
            "asr_error",
            "",
            {"error": _stringify_aliyun_message(message)},
        )

    def _on_close(self, *args) -> None:
        return None

    def _append_event(
        self,
        event: str,
        text: str,
        extra: Mapping[str, object] | None = None,
    ) -> None:
        item: dict[str, object] = {
            "event": event,
            "provider": "aliyun-nls",
        }
        if text:
            item["text"] = text
        if extra:
            item.update(extra)
        with self._lock:
            self._events.append(item)


def _create_aliyun_transcriber(**kwargs):
    try:
        import nls
    except ImportError as err:
        raise RuntimeError(
            "missing Aliyun NLS Python SDK; install the official package that "
            "provides the `nls` module"
        ) from err
    return nls.NlsSpeechTranscriber(**kwargs)


def _audio_frame_pcm_bytes(frame) -> bytes:
    view = memoryview(frame.data)
    if view.format == "B":
        return view.tobytes()
    return view.cast("B").tobytes()


def _extract_aliyun_text(message) -> str:
    payload = _parse_aliyun_message(message)
    for path in (
        ("payload", "result"),
        ("payload", "text"),
        ("result",),
        ("text",),
    ):
        value: Any = payload
        for key in path:
            if not isinstance(value, Mapping) or key not in value:
                value = None
                break
            value = value[key]
        if isinstance(value, str) and value:
            return value
    return ""


def _parse_aliyun_message(message) -> Mapping[str, Any]:
    if isinstance(message, Mapping):
        return message
    if isinstance(message, bytes):
        message = message.decode("utf-8", errors="replace")
    if isinstance(message, str):
        try:
            parsed = json.loads(message)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, Mapping):
            return parsed
    return {}


def _stringify_aliyun_message(message) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, bytes):
        return message.decode("utf-8", errors="replace")
    try:
        return json.dumps(message, ensure_ascii=False)
    except TypeError:
        return str(message)
