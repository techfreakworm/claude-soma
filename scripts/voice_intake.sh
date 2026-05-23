#!/usr/bin/env bash
# scripts/voice_intake.sh
#
# UserPromptSubmit hook that WAS supposed to transcribe voice memos before
# they reached claude. In practice the Telegram channel plugin downloads
# the .oga to a path inside the message, and claude calls
# `mcp__voice-stt__transcribe` on it directly — so this hook ends up firing
# for every text DM with nothing useful to do.
#
# The hook output schema we used (`{decision: "continue", user_prompt: ...,
# meta_inject: ...}`) was rejected as invalid by Claude Code 2.1.150's
# stricter UserPromptSubmit schema. Until the schema stabilizes (the docs
# moved between minors), we keep this as a silent no-op: exit 0 with no
# output is the universally accepted "do nothing, pass through" signal.
#
# V1.5 follow-up: rewrite this against the current hook schema
# (hookSpecificOutput.additionalContext) if we find a real use case where
# pre-processing the prompt server-side beats letting claude orchestrate
# the voice MCP itself.

exit 0
