from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any


DEFAULT_ALIYUN_TTS_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"


class MockTtsSynthesizer:
    """Diagnostic TTS boundary for validating LLM-to-TTS event flow."""

    def synthesize(self, llm_event: Mapping[str, object]) -> list[dict[str, object]]:
        if llm_event.get("event") != "llm_response_final":
            return []
        text = str(llm_event.get("text", ""))
        return [
            {
                "event": "tts_started",
                "provider": "mock",
                "text": text,
                "audio_format": "mock",
            },
            {
                "event": "tts_final",
                "provider": "mock",
                "text": text,
                "audio_format": "mock",
                "audio_duration_ms": 0,
            },
        ]


class AliyunCosyVoiceTtsSynthesizer:
    """CosyVoice realtime TTS adapter backed by DashScope WebSocket API."""

    def __init__(
        self,
        *,
        api_key: str,
        url: str = DEFAULT_ALIYUN_TTS_WS_URL,
        model: str = "cosyvoice-v3-flash",
        voice: str = "longanyang",
        sample_rate: int = 24000,
        volume: int = 50,
        rate: float = 1.0,
        pitch: float = 1.0,
        connect_factory=None,
        task_id_factory=None,
    ) -> None:
        if not api_key:
            raise ValueError("missing DASHSCOPE_API_KEY or ALIYUN_TTS_API_KEY")
        if not url:
            raise ValueError("missing ALIYUN_TTS_WS_URL")
        if not model:
            raise ValueError("missing ALIYUN_TTS_MODEL")
        if not voice:
            raise ValueError("missing ALIYUN_TTS_VOICE")
        self.api_key = api_key
        self.url = url
        self.model = model
        self.voice = voice
        self.sample_rate = sample_rate
        self.volume = volume
        self.rate = rate
        self.pitch = pitch
        self._connect_factory = connect_factory or _connect_websocket
        self._task_id_factory = task_id_factory or (lambda: str(uuid.uuid4()))

    def synthesize(self, llm_event: Mapping[str, object]) -> list[dict[str, object]]:
        if llm_event.get("event") != "llm_response_final":
            return []
        text = str(llm_event.get("text", "")).strip()
        if not text:
            return []

        started_event = {
            "event": "tts_started",
            "provider": "aliyun-cosyvoice",
            "text": text,
            "model": self.model,
            "voice": self.voice,
            "audio_format": "pcm",
            "audio_sample_rate": self.sample_rate,
        }
        pcm = self._synthesize_pcm(text)
        duration_ms = int((len(pcm) / 2) / self.sample_rate * 1000) if pcm else 0
        return [
            started_event,
            {
                "event": "tts_final",
                "provider": "aliyun-cosyvoice",
                "text": text,
                "model": self.model,
                "voice": self.voice,
                "audio_format": "pcm",
                "audio_sample_rate": self.sample_rate,
                "audio_num_channels": 1,
                "audio_byte_count": len(pcm),
                "audio_duration_ms": duration_ms,
                "_audio_pcm": pcm,
            },
        ]

    def _synthesize_pcm(self, text: str) -> bytes:
        task_id = self._task_id_factory()
        audio_chunks: list[bytes] = []
        with self._connect_factory(
            self.url,
            additional_headers={"Authorization": f"Bearer {self.api_key}"},
        ) as websocket:
            websocket.send(json.dumps(self._run_task_payload(task_id), ensure_ascii=False))
            self._wait_for_event(websocket, "task-started")
            websocket.send(
                json.dumps(
                    {
                        "header": {
                            "action": "continue-task",
                            "task_id": task_id,
                            "streaming": "duplex",
                        },
                        "payload": {
                            "input": {
                                "text": text,
                            }
                        },
                    },
                    ensure_ascii=False,
                )
            )
            websocket.send(
                json.dumps(
                    {
                        "header": {
                            "action": "finish-task",
                            "task_id": task_id,
                            "streaming": "duplex",
                        },
                        "payload": {
                            "input": {},
                        },
                    },
                    ensure_ascii=False,
                )
            )
            while True:
                message = websocket.recv()
                if isinstance(message, bytes):
                    audio_chunks.append(message)
                    continue
                event = _parse_json_message(message)
                event_name = str(event.get("header", {}).get("event", ""))
                if event_name == "task-finished":
                    break
                if event_name == "task-failed":
                    header = event.get("header", {})
                    raise RuntimeError(
                        "Aliyun CosyVoice task failed: "
                        f"{header.get('error_code', '')} {header.get('error_message', '')}".strip()
                    )
        return b"".join(audio_chunks)

    def _run_task_payload(self, task_id: str) -> dict[str, Any]:
        return {
            "header": {
                "action": "run-task",
                "task_id": task_id,
                "streaming": "duplex",
            },
            "payload": {
                "task_group": "audio",
                "task": "tts",
                "function": "SpeechSynthesizer",
                "model": self.model,
                "parameters": {
                    "text_type": "PlainText",
                    "voice": self.voice,
                    "format": "pcm",
                    "sample_rate": self.sample_rate,
                    "volume": self.volume,
                    "rate": self.rate,
                    "pitch": self.pitch,
                    "enable_ssml": False,
                },
                "input": {},
            },
        }

    def _wait_for_event(self, websocket, expected_event: str) -> None:
        while True:
            message = websocket.recv()
            if isinstance(message, bytes):
                continue
            event = _parse_json_message(message)
            event_name = str(event.get("header", {}).get("event", ""))
            if event_name == expected_event:
                return
            if event_name == "task-failed":
                header = event.get("header", {})
                raise RuntimeError(
                    "Aliyun CosyVoice task failed: "
                    f"{header.get('error_code', '')} {header.get('error_message', '')}".strip()
                )


def _connect_websocket(url: str, **kwargs):
    try:
        from websockets.sync.client import connect
    except ImportError as err:
        raise RuntimeError(
            "missing websockets sync client; run with `uv run --with websockets ...`"
        ) from err
    return connect(url, **kwargs)


def _parse_json_message(message) -> Mapping[str, Any]:
    if isinstance(message, Mapping):
        return message
    if isinstance(message, bytes):
        message = message.decode("utf-8", errors="replace")
    if isinstance(message, str):
        parsed = json.loads(message)
        if isinstance(parsed, Mapping):
            return parsed
    raise RuntimeError(f"unexpected CosyVoice message: {message!r}")
