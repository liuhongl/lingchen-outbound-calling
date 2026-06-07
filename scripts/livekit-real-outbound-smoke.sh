#!/usr/bin/env bash
set -euo pipefail

GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:9100}"
DESTINATION=""
BUSINESS_ID="manual-real-call-001"
PIPELINE="public-cloud"
CALL_ID="real-smoke"
WAIT_UNTIL_ANSWERED="false"
CONFIRM_REAL_CALL="false"

usage() {
  cat <<'EOF'
Usage:
  scripts/livekit-real-outbound-smoke.sh --destination 18518968743
  scripts/livekit-real-outbound-smoke.sh --destination 18518968743 --confirm-real-call

Options:
  --destination NUMBER       Raw 11-digit domestic mobile number. Do not add +86.
  --business-id VALUE        Business id attached to the smoke call.
  --pipeline VALUE           Pipeline label. Default: public-cloud.
  --gateway-url URL          Gateway URL. Default: http://127.0.0.1:9100.
  --wait-until-answered      Ask LiveKit to wait until callee answers.
  --confirm-real-call        Required to send dry_run=false and dial the phone.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --destination)
      DESTINATION="${2:-}"
      shift 2
      ;;
    --business-id)
      BUSINESS_ID="${2:-}"
      shift 2
      ;;
    --pipeline)
      PIPELINE="${2:-}"
      shift 2
      ;;
    --gateway-url)
      GATEWAY_URL="${2:-}"
      shift 2
      ;;
    --wait-until-answered)
      WAIT_UNTIL_ANSWERED="true"
      shift
      ;;
    --confirm-real-call)
      CONFIRM_REAL_CALL="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 64
      ;;
  esac
done

if [[ ! "$DESTINATION" =~ ^1[3-9][0-9]{9}$ ]]; then
  printf 'destination must be a raw 11-digit domestic mobile number, for example 18518968743\n' >&2
  exit 64
fi

json_preflight_body() {
  python3 - "$DESTINATION" "$CALL_ID" <<'PY'
import json
import sys

print(json.dumps(
    {
        "destination": sys.argv[1],
        "call_id": sys.argv[2],
    },
    separators=(",", ":"),
))
PY
}

json_real_body() {
  python3 - "$DESTINATION" "$BUSINESS_ID" "$PIPELINE" "$WAIT_UNTIL_ANSWERED" <<'PY'
import json
import sys

print(json.dumps(
    {
        "destination": sys.argv[1],
        "business_id": sys.argv[2],
        "dry_run": False,
        "pipeline": sys.argv[3],
        "wait_until_answered": sys.argv[4].lower() == "true",
    },
    separators=(",", ":"),
))
PY
}

print_preflight_summary() {
  python3 - "$1" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
preflight = payload.get("preflight") or {}
print(f"preflight.ready={preflight.get('ready')}")
print(f"trunk_id={preflight.get('trunk_id')}")
print(f"caller_id={preflight.get('caller_id')}")
print(f"destination_valid={preflight.get('destination_valid')}")
if preflight.get("missing"):
    print("missing=" + ",".join(preflight["missing"]))
if preflight.get("invalid"):
    print("invalid=" + ",".join(preflight["invalid"]))
if preflight.get("warnings"):
    print("warnings=" + " | ".join(preflight["warnings"]))
sys.exit(0 if preflight.get("ready") else 1)
PY
}

print_call_summary() {
  python3 - "$1" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
outbound = payload.get("outbound") or {}
print(f"call_id={outbound.get('call_id')}")
print(f"room={outbound.get('room')}")
print(f"status={outbound.get('status')}")
print(f"dry_run={outbound.get('dry_run')}")
events = outbound.get("events") or []
if events:
    print("events=" + ",".join(str(event.get("event")) for event in events))
PY
}

printf '== LiveKit SIP real outbound preflight ==\n'
preflight_body="$(json_preflight_body)"
preflight_json="$(
  curl -fsS -X POST "$GATEWAY_URL/livekit/sip/outbound/preflight" \
    -H 'Content-Type: application/json' \
    -d "$preflight_body"
)"
if ! print_preflight_summary "$preflight_json"; then
  printf 'REAL CALL NOT SENT: preflight is not ready.\n'
  exit 1
fi

if [[ "$CONFIRM_REAL_CALL" != "true" ]]; then
  printf '\nREAL CALL NOT SENT: pass --confirm-real-call to send dry_run=false.\n'
  printf 'Destination that would be dialed: %s\n' "$DESTINATION"
  printf 'Gateway: %s\n' "$GATEWAY_URL"
  exit 2
fi

printf '\n== Sending real outbound request ==\n'
real_body="$(json_real_body)"
real_json="$(
  curl -fsS -X POST "$GATEWAY_URL/livekit/sip/outbound" \
    -H 'Content-Type: application/json' \
    -d "$real_body"
)"
print_call_summary "$real_json"
printf 'REAL CALL REQUEST SENT. Watch LiveKit Cloud Telephony Calls and provider SIP result codes.\n'
