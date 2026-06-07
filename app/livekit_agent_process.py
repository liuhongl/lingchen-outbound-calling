from __future__ import annotations

import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any


PopenFactory = Callable[..., subprocess.Popen]


class LiveKitAgentProcessManager:
    def __init__(
        self,
        *,
        cwd: Path | str | None = None,
        uv_executable: str = "uv",
        config_path: str = "configs/local.example.toml",
        env_file: str = ".env",
        event_sink_url: str = "http://127.0.0.1:9100/livekit/web-debug/events",
        popen_factory: PopenFactory = subprocess.Popen,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self.cwd = Path(cwd) if cwd is not None else Path.cwd()
        self.uv_executable = uv_executable
        self.config_path = config_path
        self.env_file = env_file
        self.event_sink_url = event_sink_url
        self._popen_factory = popen_factory
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._lock = threading.Lock()
        self._agents: dict[str, _AgentProcess] = {}

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        room = _payload_text(payload.get("room")) or "web-debug-demo"
        pipeline = _payload_text(payload.get("pipeline")) or "public-cloud"
        duration_seconds = _payload_int(payload.get("duration_seconds"), default=600)
        audio_frame_limit = _payload_int(payload.get("audio_frame_limit"), default=9000)
        event_sink_url = (
            _payload_text(payload.get("event_sink_url")) or self.event_sink_url
        )

        with self._lock:
            current = self._agents.get(room)
            if current is not None and current.is_running():
                snapshot = current.snapshot()
                snapshot["alreadyRunning"] = True
                return snapshot

            command = self._build_command(
                room=room,
                pipeline=pipeline,
                duration_seconds=duration_seconds,
                audio_frame_limit=audio_frame_limit,
                event_sink_url=event_sink_url,
            )
            process = self._popen_factory(
                command,
                cwd=str(self.cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            agent = _AgentProcess(
                room=room,
                pipeline=pipeline,
                process=process,
                started_at_ms=self._now_ms(),
            )
            agent.start_tail_threads()
            self._agents[room] = agent
            return agent.snapshot()

    def status(self, room: str) -> dict[str, Any]:
        room = _payload_text(room) or "web-debug-demo"
        with self._lock:
            agent = self._agents.get(room)
            if agent is None:
                return {"room": room, "running": False, "status": "not_started"}
            return agent.snapshot()

    def stop(self, room: str) -> dict[str, Any]:
        room = _payload_text(room) or "web-debug-demo"
        with self._lock:
            agent = self._agents.get(room)
            if agent is None:
                return {"room": room, "running": False, "status": "not_started"}
            agent.stop()
            return agent.snapshot()

    def _build_command(
        self,
        *,
        room: str,
        pipeline: str,
        duration_seconds: int,
        audio_frame_limit: int,
        event_sink_url: str,
    ) -> list[str]:
        return [
            self.uv_executable,
            "run",
            "--with",
            "livekit",
            "--with",
            "openai",
            "--with",
            "websockets",
            "--with",
            "git+https://github.com/aliyun/alibabacloud-nls-python-sdk.git",
            "python",
            "-m",
            "app.livekit_agent_worker",
            "--config",
            self.config_path,
            "--env-file",
            self.env_file,
            "--room",
            room,
            "--identity",
            "agent-worker-public-cloud-browser",
            "--name",
            "LiveKit Agent Public Cloud Browser",
            "--duration-seconds",
            str(duration_seconds),
            "--audio-frame-limit",
            str(audio_frame_limit),
            "--audio-sample-rate",
            "8000",
            "--pipeline",
            pipeline,
            "--event-sink-url",
            event_sink_url,
        ]


class _AgentProcess:
    def __init__(
        self,
        *,
        room: str,
        pipeline: str,
        process: subprocess.Popen,
        started_at_ms: int,
    ) -> None:
        self.room = room
        self.pipeline = pipeline
        self.process = process
        self.started_at_ms = started_at_ms
        self.stdout_tail: deque[str] = deque(maxlen=40)
        self.stderr_tail: deque[str] = deque(maxlen=40)

    def is_running(self) -> bool:
        return self.process.poll() is None

    def snapshot(self) -> dict[str, Any]:
        return {
            "room": self.room,
            "pipeline": self.pipeline,
            "pid": int(self.process.pid),
            "running": self.is_running(),
            "returncode": self.process.poll(),
            "startedAtMs": self.started_at_ms,
            "stdoutTail": list(self.stdout_tail),
            "stderrTail": list(self.stderr_tail),
        }

    def start_tail_threads(self) -> None:
        _start_tail_thread(getattr(self.process, "stdout", None), self.stdout_tail)
        _start_tail_thread(getattr(self.process, "stderr", None), self.stderr_tail)

    def stop(self) -> None:
        if not self.is_running():
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


def _start_tail_thread(stream: Any, tail: deque[str]) -> None:
    if stream is None:
        return

    def read_lines() -> None:
        try:
            for line in stream:
                tail.append(str(line).rstrip())
        finally:
            try:
                stream.close()
            except OSError:
                pass

    thread = threading.Thread(target=read_lines, daemon=True)
    thread.start()


def _payload_text(value: object) -> str:
    return str(value or "").strip()


def _payload_int(value: object, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, parsed)
