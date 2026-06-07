from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import struct
from collections.abc import Callable
from typing import Any

from .config import LiveKitConfig, load_config
from .env_loader import load_env_file
from .livekit_dialog_policy import MockDialoguePolicy, OpenAICompatibleDialoguePolicy
from .livekit_streaming_asr import (
    DEFAULT_ALIYUN_NLS_URL,
    AliyunNlsStreamingAsrAdapter,
    MockStreamingAsrAdapter,
)
from .livekit_tts import (
    DEFAULT_ALIYUN_TTS_WS_URL,
    AliyunCosyVoiceTtsSynthesizer,
    MockTtsSynthesizer,
)
from .livekit_web_debug import LiveKitWebDebugSessionFactory


AgentEventWriter = Callable[[dict[str, object]], object]


async def run_livekit_agent_once(
    config: LiveKitConfig,
    *,
    room_name: str,
    identity: str = "agent-worker",
    name: str = "LiveKit Agent",
    duration_seconds: float = 30.0,
    audio_frame_limit: int = 0,
    audio_sample_rate: int = 16000,
    audio_num_channels: int = 1,
    asr_provider: str = "none",
    mock_asr_final_after_frames: int = 5,
    dialog_provider: str = "none",
    tts_provider: str = "none",
    publish_mock_tts_audio: bool = False,
    rtc_module: Any | None = None,
    on_event: AgentEventWriter | None = None,
    now: int | None = None,
) -> dict[str, object]:
    rtc = rtc_module or _import_livekit_rtc()
    session = LiveKitWebDebugSessionFactory(config).create_session(
        {
            "room": room_name,
            "identity": identity,
            "name": name,
        },
        now=now,
    )
    room = rtc.Room()
    writer = on_event or _print_event
    audio_tasks: set[asyncio.Task[None]] = set()

    room.on(
        "participant_connected",
        lambda participant: writer(
            {
                "event": "participant_connected",
                "room": session["room"],
                "identity": session["identity"],
                "participant": participant.identity,
            }
        ),
    )
    room.on(
        "participant_disconnected",
        lambda participant: writer(
            {
                "event": "participant_disconnected",
                "room": session["room"],
                "identity": session["identity"],
                "participant": participant.identity,
            }
        ),
    )

    def on_track_subscribed(track, publication, participant) -> None:
        if getattr(track, "kind", None) != rtc.TrackKind.KIND_AUDIO:
            return
        writer(
            {
                "event": "audio_track_subscribed",
                "room": session["room"],
                "identity": session["identity"],
                "participant": participant.identity,
            }
        )
        if audio_frame_limit <= 0:
            return
        task = asyncio.create_task(
            _consume_audio_stream(
                rtc,
                room,
                track,
                participant_identity=participant.identity,
                room_name=str(session["room"]),
                identity=str(session["identity"]),
                writer=writer,
                frame_limit=audio_frame_limit,
                sample_rate=audio_sample_rate,
                num_channels=audio_num_channels,
                asr_adapter=_build_streaming_asr_adapter(
                    asr_provider,
                    mock_asr_final_after_frames=mock_asr_final_after_frames,
                ),
                dialogue_policy=_build_dialogue_policy(dialog_provider),
                tts_synthesizer=_build_tts_synthesizer(tts_provider),
                publish_mock_tts_audio=publish_mock_tts_audio,
            )
        )
        audio_tasks.add(task)

    room.on("track_subscribed", on_track_subscribed)

    await room.connect(
        str(session["livekitUrl"]),
        str(session["token"]),
        rtc.RoomOptions(auto_subscribe=True),
    )
    writer(
        {
            "event": "connected",
            "room": session["room"],
            "identity": session["identity"],
            "remote_participants": sorted(room.remote_participants.keys()),
        }
    )
    try:
        if duration_seconds > 0:
            await asyncio.sleep(duration_seconds)
        elif audio_tasks:
            await asyncio.wait(audio_tasks, timeout=5)
    finally:
        for task in list(audio_tasks):
            if not task.done():
                task.cancel()
        if audio_tasks:
            await asyncio.gather(*audio_tasks, return_exceptions=True)
        await room.disconnect()
        writer(
            {
                "event": "disconnected",
                "room": session["room"],
                "identity": session["identity"],
            }
        )

    return {
        "room": session["room"],
        "identity": session["identity"],
        "livekitUrl": session["livekitUrl"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Minimal LiveKit Agent Worker")
    parser.add_argument("--config", default="configs/local.example.toml")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--room", default="demo")
    parser.add_argument("--identity", default="agent-worker")
    parser.add_argument("--name", default="LiveKit Agent")
    parser.add_argument("--duration-seconds", type=float, default=30.0)
    parser.add_argument("--audio-frame-limit", type=int, default=0)
    parser.add_argument("--audio-sample-rate", type=int, default=16000)
    parser.add_argument(
        "--asr-provider",
        choices=("none", "mock", "aliyun-nls"),
        default="none",
    )
    parser.add_argument("--mock-asr-final-after-frames", type=int, default=5)
    parser.add_argument(
        "--dialog-provider",
        choices=("none", "mock", "openai-compatible"),
        default="none",
    )
    parser.add_argument(
        "--tts-provider",
        choices=("none", "mock", "aliyun-cosyvoice"),
        default="none",
    )
    parser.add_argument("--publish-mock-tts-audio", action="store_true")
    parser.add_argument(
        "--pipeline",
        choices=("none", "mock", "public-cloud"),
        default="none",
    )
    args = parser.parse_args()
    pipeline_settings = _build_pipeline_settings(
        pipeline=args.pipeline,
        asr_provider=args.asr_provider,
        dialog_provider=args.dialog_provider,
        tts_provider=args.tts_provider,
        publish_mock_tts_audio=args.publish_mock_tts_audio,
    )

    if args.env_file:
        load_env_file(args.env_file)
    config = load_config(args.config)
    if not config.livekit.enabled:
        raise RuntimeError("livekit.enabled must be true to run the agent worker")
    asyncio.run(
        run_livekit_agent_once(
            config.livekit,
            room_name=args.room,
            identity=args.identity,
            name=args.name,
            duration_seconds=args.duration_seconds,
            audio_frame_limit=args.audio_frame_limit,
            audio_sample_rate=args.audio_sample_rate,
            asr_provider=str(pipeline_settings["asr_provider"]),
            mock_asr_final_after_frames=args.mock_asr_final_after_frames,
            dialog_provider=str(pipeline_settings["dialog_provider"]),
            tts_provider=str(pipeline_settings["tts_provider"]),
            publish_mock_tts_audio=bool(pipeline_settings["publish_mock_tts_audio"]),
        )
    )
    return 0


def _import_livekit_rtc():
    try:
        from livekit import rtc
    except ImportError as err:
        raise RuntimeError(
            "missing LiveKit Python SDK; run with `uv run --with livekit ...`"
        ) from err
    return rtc


async def _consume_audio_stream(
    rtc,
    room,
    track,
    *,
    participant_identity: str,
    room_name: str,
    identity: str,
    writer: AgentEventWriter,
    frame_limit: int,
    sample_rate: int,
    num_channels: int,
    asr_adapter: Any | None = None,
    dialogue_policy: MockDialoguePolicy | None = None,
    tts_synthesizer: MockTtsSynthesizer | None = None,
    publish_mock_tts_audio: bool = False,
) -> None:
    stream = rtc.AudioStream(track, sample_rate=sample_rate, num_channels=num_channels)
    frame_index = 0
    base_event = {
        "room": room_name,
        "identity": identity,
        "participant": participant_identity,
    }
    try:
        async for event in stream:
            frame_index += 1
            summary = _audio_frame_summary(event.frame)
            writer(
                {
                    "event": "audio_frame",
                    **base_event,
                    "frame_index": frame_index,
                    **summary,
                }
            )
            if asr_adapter is not None:
                await _write_asr_events(
                    rtc,
                    room,
                    base_event,
                    _accept_asr_frame(asr_adapter, event.frame, summary),
                    writer=writer,
                    dialogue_policy=dialogue_policy,
                    tts_synthesizer=tts_synthesizer,
                    publish_mock_tts_audio=publish_mock_tts_audio,
                )
            if frame_index >= frame_limit:
                break
    finally:
        if hasattr(stream, "aclose"):
            await stream.aclose()
        if asr_adapter is not None:
            await _write_asr_events(
                rtc,
                room,
                base_event,
                asr_adapter.finish(),
                writer=writer,
                dialogue_policy=dialogue_policy,
                tts_synthesizer=tts_synthesizer,
                publish_mock_tts_audio=publish_mock_tts_audio,
            )
        writer(
            {
                "event": "audio_stream_completed",
                **base_event,
                "frames": frame_index,
            }
        )


def _audio_frame_summary(frame) -> dict[str, int]:
    samples = _int16_samples(frame.data)
    if not samples:
        rms = 0
        peak = 0
    else:
        squares = sum(int(sample) * int(sample) for sample in samples)
        rms = int((squares / len(samples)) ** 0.5)
        peak = max(abs(int(sample)) for sample in samples)
    return {
        "sample_rate": int(frame.sample_rate),
        "num_channels": int(frame.num_channels),
        "samples_per_channel": int(frame.samples_per_channel),
        "rms": rms,
        "peak": peak,
    }


def _int16_samples(data) -> list[int]:
    view = memoryview(data)
    if view.format in {"h", "<h", ">h"}:
        return [int(sample) for sample in view]
    return [int(sample) for sample in memoryview(bytes(view)).cast("h")]


def _build_streaming_asr_adapter(
    provider: str,
    *,
    mock_asr_final_after_frames: int,
) -> MockStreamingAsrAdapter | None:
    if provider == "none":
        return None
    if provider == "mock":
        return MockStreamingAsrAdapter(
            final_after_frames=mock_asr_final_after_frames,
        )
    if provider == "aliyun-nls":
        return AliyunNlsStreamingAsrAdapter(
            appkey=os.getenv("ALIYUN_NLS_APPKEY", ""),
            token=os.getenv("ALIYUN_NLS_TOKEN", ""),
            url=os.getenv("ALIYUN_NLS_URL", DEFAULT_ALIYUN_NLS_URL),
        )
    raise ValueError(f"unsupported ASR provider: {provider}")


def _accept_asr_frame(asr_adapter, frame, frame_summary: dict[str, int]):
    if hasattr(asr_adapter, "accept_audio_frame"):
        return asr_adapter.accept_audio_frame(frame, frame_summary)
    return asr_adapter.accept_frame(frame_summary)


def _build_dialogue_policy(
    provider: str,
) -> MockDialoguePolicy | OpenAICompatibleDialoguePolicy | None:
    if provider == "none":
        return None
    if provider == "mock":
        return MockDialoguePolicy()
    if provider == "openai-compatible":
        return OpenAICompatibleDialoguePolicy(
            api_key=os.getenv("LLM_API_KEY") or os.getenv("DASHSCOPE_API_KEY", ""),
            base_url=os.getenv(
                "LLM_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            model=os.getenv("LLM_MODEL", "qwen-plus"),
        )
    raise ValueError(f"unsupported dialogue provider: {provider}")


def _build_tts_synthesizer(
    provider: str,
) -> MockTtsSynthesizer | AliyunCosyVoiceTtsSynthesizer | None:
    if provider == "none":
        return None
    if provider == "mock":
        return MockTtsSynthesizer()
    if provider == "aliyun-cosyvoice":
        return AliyunCosyVoiceTtsSynthesizer(
            api_key=os.getenv("ALIYUN_TTS_API_KEY")
            or os.getenv("DASHSCOPE_API_KEY", ""),
            url=os.getenv("ALIYUN_TTS_WS_URL", DEFAULT_ALIYUN_TTS_WS_URL),
            model=os.getenv("ALIYUN_TTS_MODEL", "cosyvoice-v3-flash"),
            voice=os.getenv("ALIYUN_TTS_VOICE", "longanyang"),
            sample_rate=int(os.getenv("ALIYUN_TTS_SAMPLE_RATE", "24000")),
        )
    raise ValueError(f"unsupported TTS provider: {provider}")


def _build_pipeline_settings(
    *,
    pipeline: str,
    asr_provider: str,
    dialog_provider: str,
    tts_provider: str,
    publish_mock_tts_audio: bool,
) -> dict[str, object]:
    if pipeline == "none":
        return {
            "asr_provider": asr_provider,
            "dialog_provider": dialog_provider,
            "tts_provider": tts_provider,
            "publish_mock_tts_audio": publish_mock_tts_audio,
        }
    if pipeline == "mock":
        return {
            "asr_provider": "mock",
            "dialog_provider": "mock",
            "tts_provider": "mock",
            "publish_mock_tts_audio": True,
        }
    if pipeline == "public-cloud":
        return {
            "asr_provider": "aliyun-nls",
            "dialog_provider": "openai-compatible",
            "tts_provider": "aliyun-cosyvoice",
            "publish_mock_tts_audio": False,
        }
    raise ValueError(f"unsupported pipeline: {pipeline}")


async def _write_asr_events(
    rtc,
    room,
    base_event: dict[str, object],
    asr_events: list[dict[str, object]],
    *,
    writer: AgentEventWriter,
    dialogue_policy: MockDialoguePolicy | None,
    tts_synthesizer: MockTtsSynthesizer | None,
    publish_mock_tts_audio: bool,
) -> None:
    for asr_event in asr_events:
        writer({**base_event, **asr_event})
        if dialogue_policy is None:
            continue
        for dialogue_event in dialogue_policy.respond(asr_event):
            writer({**base_event, **dialogue_event})
            if tts_synthesizer is None:
                continue
            for tts_event in tts_synthesizer.synthesize(dialogue_event):
                writer({**base_event, **_public_event(tts_event)})
                if tts_event.get("event") == "tts_final" and publish_mock_tts_audio:
                    await _publish_mock_tts_audio(rtc, room, base_event, writer=writer)
                if tts_event.get("_audio_pcm"):
                    await _publish_tts_pcm_audio(
                        rtc,
                        room,
                        base_event,
                        tts_event,
                        writer=writer,
                    )


def _public_event(event: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in event.items() if not key.startswith("_")}


async def _publish_tts_pcm_audio(
    rtc,
    room,
    base_event: dict[str, object],
    tts_event: dict[str, object],
    *,
    writer: AgentEventWriter,
) -> None:
    pcm = bytes(tts_event["_audio_pcm"])
    if not pcm:
        return
    track_name = str(tts_event.get("track_name") or "tts-audio")
    sample_rate = int(tts_event.get("audio_sample_rate", 24000))
    num_channels = int(tts_event.get("audio_num_channels", 1))
    bytes_per_sample = 2
    samples_per_20ms = max(1, int(sample_rate * 20 / 1000))
    frame_bytes = samples_per_20ms * num_channels * bytes_per_sample
    source = rtc.AudioSource(sample_rate, num_channels)
    writer({**base_event, "event": "tts_audio_publish_started", "track_name": track_name})
    try:
        track = rtc.LocalAudioTrack.create_audio_track(track_name, source)
        options = rtc.TrackPublishOptions()
        options.source = rtc.TrackSource.SOURCE_MICROPHONE
        await room.local_participant.publish_track(track, options)
        for offset in range(0, len(pcm), frame_bytes):
            chunk = pcm[offset : offset + frame_bytes]
            if len(chunk) < frame_bytes:
                chunk += b"\x00" * (frame_bytes - len(chunk))
            await source.capture_frame(
                rtc.AudioFrame(
                    data=chunk,
                    sample_rate=sample_rate,
                    num_channels=num_channels,
                    samples_per_channel=samples_per_20ms,
                )
            )
        await source.wait_for_playout()
    finally:
        if hasattr(source, "aclose"):
            await source.aclose()
    writer(
        {
            **base_event,
            "event": "tts_audio_publish_finished",
            "track_name": track_name,
            "sample_rate": sample_rate,
            "num_channels": num_channels,
            "audio_byte_count": len(pcm),
            "audio_duration_ms": int(tts_event.get("audio_duration_ms", 0)),
        }
    )


async def _publish_mock_tts_audio(
    rtc,
    room,
    base_event: dict[str, object],
    *,
    writer: AgentEventWriter,
) -> None:
    track_name = "mock-tts-audio"
    sample_rate = 48000
    num_channels = 1
    duration_ms = 100
    samples_per_channel = int(sample_rate * duration_ms / 1000)
    source = rtc.AudioSource(sample_rate, num_channels)
    writer({**base_event, "event": "tts_audio_publish_started", "track_name": track_name})
    try:
        track = rtc.LocalAudioTrack.create_audio_track(track_name, source)
        options = rtc.TrackPublishOptions()
        options.source = rtc.TrackSource.SOURCE_MICROPHONE
        await room.local_participant.publish_track(track, options)
        await source.capture_frame(
            _build_mock_tts_audio_frame(
                rtc,
                sample_rate=sample_rate,
                num_channels=num_channels,
                samples_per_channel=samples_per_channel,
            )
        )
        await source.wait_for_playout()
    finally:
        if hasattr(source, "aclose"):
            await source.aclose()
    writer(
        {
            **base_event,
            "event": "tts_audio_publish_finished",
            "track_name": track_name,
            "sample_rate": sample_rate,
            "num_channels": num_channels,
            "samples_per_channel": samples_per_channel,
            "audio_duration_ms": duration_ms,
        }
    )


def _build_mock_tts_audio_frame(
    rtc,
    *,
    sample_rate: int,
    num_channels: int,
    samples_per_channel: int,
):
    frequency_hz = 440
    amplitude = 2400
    samples = [
        int(amplitude * math.sin(2 * math.pi * frequency_hz * index / sample_rate))
        for index in range(samples_per_channel)
    ]
    pcm = b"".join(struct.pack("<h", sample) for sample in samples)
    return rtc.AudioFrame(
        data=pcm,
        sample_rate=sample_rate,
        num_channels=num_channels,
        samples_per_channel=samples_per_channel,
    )


def _print_event(event: dict[str, object]) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
