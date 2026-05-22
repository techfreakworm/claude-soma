#!/usr/bin/env bash
# scripts/voice_intake.sh
#
# Invoked by UserPromptSubmit hook when meta.audio_path is present.
# Reads the hook event JSON on stdin, transcribes the audio via voice_stt MCP,
# and emits a JSON output that rewrites the user prompt to include the transcript.
#
# Hook protocol: https://code.claude.com/docs/en/hooks

set -euo pipefail

EVENT_JSON="$(cat)"
AUDIO_PATH="$(jq -r '.meta.audio_path // empty' <<<"$EVENT_JSON")"

if [[ -z "$AUDIO_PATH" || ! -f "$AUDIO_PATH" ]]; then
    # No audio meta or file missing — emit pass-through.
    jq -nc '{decision: "continue"}'
    exit 0
fi

# Use the voice_stt MCP via a one-shot python invocation.
TRANSCRIPT="$(
    /opt/claude-soma/.venv/bin/python -c "
import json, sys
from claude_soma.mcp_servers.voice_stt.server import transcribe_impl
r = transcribe_impl('$AUDIO_PATH', language='auto')
print(json.dumps(r))
" 2>/dev/null
)"

if [[ -z "$TRANSCRIPT" ]]; then
    jq -nc '{decision: "continue"}'
    exit 0
fi

TEXT="$(jq -r '.text' <<<"$TRANSCRIPT")"
LANG="$(jq -r '.language_detected' <<<"$TRANSCRIPT")"
DUR="$(jq -r '.duration_seconds' <<<"$TRANSCRIPT")"

ORIGINAL="$(jq -r '.user_prompt // .prompt // ""' <<<"$EVENT_JSON")"

# Rewrite prompt: keep original (channel often inserts placeholder),
# prepend a clear transcript marker so downstream skills can detect "voice in".
NEW_PROMPT=$(printf "[voice transcript · lang=%s · dur=%ss]\n%s\n\n(audio path: %s)" \
    "$LANG" "$DUR" "$TEXT" "$AUDIO_PATH")

jq -nc \
  --arg p "$NEW_PROMPT" \
  --arg ap "$AUDIO_PATH" \
  '{decision: "continue", user_prompt: $p, meta_inject: {voice_in: true, audio_path: $ap}}'
