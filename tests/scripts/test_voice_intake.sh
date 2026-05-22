#!/usr/bin/env bash
set -euo pipefail
HERE="$(dirname "$0")/../.."
EVENT='{"user_prompt":"[voice]","meta":{"audio_path":"/opt/whisper.cpp/samples/jfk.wav"}}'
RESULT="$(echo "$EVENT" | bash "$HERE/scripts/voice_intake.sh")"
echo "$RESULT" | jq -e '.user_prompt | contains("voice transcript")' >/dev/null
echo "$RESULT" | jq -e '.meta_inject.voice_in == true' >/dev/null
echo "OK"
