from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "livekit-real-outbound-smoke.sh"


def test_livekit_real_outbound_smoke_requires_explicit_confirmation(tmp_path):
    fake_bin = _fake_curl(tmp_path)
    result = subprocess.run(
        [str(SCRIPT), "--destination", "18518968743"],
        cwd=ROOT,
        env=_env_with_fake_curl(fake_bin, tmp_path),
        text=True,
        capture_output=True,
        check=False,
    )

    calls = (tmp_path / "curl.log").read_text()
    assert result.returncode == 2
    assert "REAL CALL NOT SENT" in result.stdout
    assert "/livekit/sip/outbound/preflight" in calls
    assert "/livekit/sip/outbound " not in calls


def test_livekit_real_outbound_smoke_sends_real_call_after_confirmation(tmp_path):
    fake_bin = _fake_curl(tmp_path)
    result = subprocess.run(
        [
            str(SCRIPT),
            "--destination",
            "18518968743",
            "--business-id",
            "manual-real-call-001",
            "--confirm-real-call",
        ],
        cwd=ROOT,
        env=_env_with_fake_curl(fake_bin, tmp_path),
        text=True,
        capture_output=True,
        check=False,
    )

    calls = (tmp_path / "curl.log").read_text()
    assert result.returncode == 0
    assert "REAL CALL REQUEST SENT" in result.stdout
    assert "/livekit/sip/outbound/preflight" in calls
    assert "/livekit/sip/outbound " in calls
    assert '"destination":"18518968743"' in calls
    assert '"business_id":"manual-real-call-001"' in calls
    assert '"dry_run":false' in calls
    assert '"wait_until_answered":false' in calls


def _env_with_fake_curl(fake_bin: Path, tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["CURL_LOG"] = str(tmp_path / "curl.log")
    return env


def _fake_curl(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl = fake_bin / "curl"
    curl.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$CURL_LOG"
if printf '%s\\n' "$*" | grep -q '/livekit/sip/outbound/preflight'; then
  printf '%s\\n' '{"status":"ok","preflight":{"ready":true,"real_call_enabled":true,"destination_valid":true,"trunk_id":"ST_test","caller_id":"037123124845","missing":[],"invalid":[],"warnings":[]}}'
elif printf '%s\\n' "$*" | grep -q '/livekit/sip/outbound'; then
  printf '%s\\n' '{"status":"accepted","outbound":{"call_id":"sip-test","room":"sip-outbound-sip-test","status":"sip_participant_created","dry_run":false,"events":[]}}'
else
  printf '%s\\n' '{"status":"ok"}'
fi
""",
        encoding="utf-8",
    )
    curl.chmod(0o755)
    return fake_bin
