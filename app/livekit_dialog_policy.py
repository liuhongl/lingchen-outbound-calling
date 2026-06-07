from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class MockDialoguePolicy:
    """Diagnostic dialogue policy for validating ASR-to-LLM event flow."""

    def __init__(self, *, response_text: str = "收到，我会继续按测试链路回复。") -> None:
        self.response_text = response_text

    def respond(self, asr_event: Mapping[str, object]) -> list[dict[str, object]]:
        if asr_event.get("event") != "asr_final":
            return []
        input_text = str(asr_event.get("text", ""))
        return [
            {
                "event": "llm_response_started",
                "provider": "mock",
                "input_text": input_text,
            },
            {
                "event": "llm_response_final",
                "provider": "mock",
                "input_text": input_text,
                "text": self.response_text,
            },
        ]


class OpenAICompatibleDialoguePolicy:
    """Dialogue policy backed by an OpenAI-compatible chat completion API."""

    default_system_prompt = (
        "你是物业费催收外呼场景的中文语音助手，回复要简短、自然、适合电话口播。"
    )

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        system_prompt: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 120,
        client_factory=None,
    ) -> None:
        if not api_key:
            raise ValueError("missing DASHSCOPE_API_KEY or LLM_API_KEY")
        if not base_url:
            raise ValueError("missing LLM_BASE_URL")
        if not model:
            raise ValueError("missing LLM_MODEL")
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.system_prompt = system_prompt or self.default_system_prompt
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client_factory = client_factory or _create_openai_client
        self._client = None

    def respond(self, asr_event: Mapping[str, object]) -> list[dict[str, object]]:
        if asr_event.get("event") != "asr_final":
            return []
        input_text = str(asr_event.get("text", "")).strip()
        if not input_text:
            return []

        started_event = {
            "event": "llm_response_started",
            "provider": "openai-compatible",
            "input_text": input_text,
            "model": self.model,
        }
        response = self._get_client().chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": input_text},
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        text = _extract_chat_completion_text(response)
        return [
            started_event,
            {
                "event": "llm_response_final",
                "provider": "openai-compatible",
                "input_text": input_text,
                "text": text,
                "model": self.model,
            },
        ]

    def _get_client(self):
        if self._client is None:
            self._client = self._client_factory(
                api_key=self.api_key,
                base_url=self.base_url,
            )
        return self._client


def _create_openai_client(**kwargs):
    try:
        from openai import OpenAI
    except ImportError as err:
        raise RuntimeError(
            "missing OpenAI Python SDK; run with `uv run --with openai ...`"
        ) from err
    return OpenAI(**kwargs)


def _extract_chat_completion_text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    return str(content).strip()
