---
name: respond-with-voice
description: |
  Reply to the user with a voice note instead of (or in addition to) text.
  Use when: (a) the incoming user message had `voice_in: true` meta (mirror modality),
  (b) the user explicitly asked for a voice reply, or (c) the reply is short plain
  language that is voice-friendly. DO NOT use when the reply contains code blocks,
  tables, URLs the user needs to copy, or anything longer than ~3 sentences.
allowed-tools: mcp__voice-tts__synthesize, mcp__telegram__reply_voice
---

# respond-with-voice

When invoked, you have a `reply_text` you want to deliver as audio.

1. Call `mcp__voice-tts__synthesize` with `text=<reply_text>`. This returns
   `{audio_path, duration_seconds, voice_used}`.

2. Call `mcp__telegram__reply_voice` (provided by the telegram channel plugin)
   with `audio_path=<the path from step 1>`. The channel uploads it as a
   Telegram voice note in the current chat.

3. If the reply ALSO contains a code block, a long table, or a URL the user
   needs to copy/click, additionally send those parts as text via the channel's
   normal text reply so the user has copy-pasteable material.

4. Confirm in plain language (one short sentence) that you've sent the voice.
   Do NOT also TTS this confirmation — it would create an echo loop.

## When not to call this skill

- Replies containing more than ~3 sentences of dense info — use text
- Code blocks, JSON, tables — use text
- Error messages with stack traces — use text
- When the user has explicitly disabled voice in this thread (check
  `/admin/conversations` for thread preferences)
