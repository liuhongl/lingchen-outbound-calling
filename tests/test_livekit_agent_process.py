from __future__ import annotations

from pathlib import Path

from app.livekit_agent_process import LiveKitAgentProcessManager


def test_start_launches_public_cloud_agent_command():
    popen = FakePopenFactory()
    manager = LiveKitAgentProcessManager(
        cwd=Path("/repo"),
        uv_executable="uv-test",
        config_path="configs/test.toml",
        env_file=".env.test",
        event_sink_url="http://127.0.0.1:9100/livekit/web-debug/events",
        popen_factory=popen,
        now_ms=lambda: 123456,
    )

    agent = manager.start(
        {
            "room": "web-debug-demo",
            "pipeline": "public-cloud",
            "duration_seconds": 600,
            "audio_frame_limit": 9000,
        }
    )

    command = popen.commands[0]
    assert agent["room"] == "web-debug-demo"
    assert agent["running"] is True
    assert agent["pipeline"] == "public-cloud"
    assert agent["pid"] == 4321
    assert agent["startedAtMs"] == 123456
    assert command[:2] == ["uv-test", "run"]
    assert "--with" in command
    assert "livekit" in command
    assert "openai" in command
    assert "python" in command
    assert command[command.index("--config") + 1] == "configs/test.toml"
    assert command[command.index("--env-file") + 1] == ".env.test"
    assert command[command.index("--room") + 1] == "web-debug-demo"
    assert command[command.index("--pipeline") + 1] == "public-cloud"
    assert (
        command[command.index("--event-sink-url") + 1]
        == "http://127.0.0.1:9100/livekit/web-debug/events"
    )
    assert command[command.index("--duration-seconds") + 1] == "600"
    assert command[command.index("--audio-frame-limit") + 1] == "9000"
    assert command[command.index("--audio-sample-rate") + 1] == "8000"


def test_start_reuses_running_agent_for_same_room():
    popen = FakePopenFactory()
    manager = LiveKitAgentProcessManager(popen_factory=popen)

    first = manager.start({"room": "web-debug-demo"})
    second = manager.start({"room": "web-debug-demo"})

    assert first["pid"] == second["pid"]
    assert second["alreadyRunning"] is True
    assert len(popen.commands) == 1


def test_stop_terminates_running_agent():
    popen = FakePopenFactory()
    manager = LiveKitAgentProcessManager(popen_factory=popen)
    manager.start({"room": "web-debug-demo"})

    stopped = manager.stop("web-debug-demo")

    assert stopped["room"] == "web-debug-demo"
    assert stopped["running"] is False
    assert popen.processes[0].terminated is True
    assert popen.processes[0].waited is True


class FakePopenFactory:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.processes: list[FakeProcess] = []

    def __call__(self, command, **kwargs):
        self.commands.append(list(command))
        process = FakeProcess(pid=4321 + len(self.processes))
        self.processes.append(process)
        return process


class FakeProcess:
    def __init__(self, *, pid: int) -> None:
        self.pid = pid
        self.returncode = None
        self.terminated = False
        self.waited = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout=None):
        self.waited = True
        return self.returncode
