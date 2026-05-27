---
name: voice-action
description: |
  Auto-loaded when the user's incoming message has `voice_in: true` meta.
  Interprets the transcribed user intent and routes to the appropriate skill,
  team, or tool call. Always prefers acting over asking a clarifying question
  when the intent is reasonably clear from voice.
---

# voice-action

The user spoke a request. The transcript is already in your context.

## Always echo the transcript first

Before routing or answering, ALWAYS begin your reply with a short line echoing
what was heard: `Heard: "<the transcript>"`. Do it every time, concisely, so the
user can gauge STT accuracy (model: `base.en`) and flag mis-hearings. Then route
or answer per below.

## Routing heuristics

1. **"Build / make / set up / create me X"** → invoke `spawn-project` with
   inferred type (web-scraper / llm-app / server-app / agentic-coding).

2. **"What's the status of / how's / what's running"** → invoke `list-projects`
   or `project-status` as appropriate.

3. **"Tell <project-name> to / ask <project-name> about"** → invoke
   `message-project` with the named target.

4. **"Draw / render / generate an image of"** → invoke `codex-image-gen`.

5. **"Schedule / every <time>, do"** → invoke `schedule-routine`.

6. **"What are you working on / what am I working on"** → invoke
   `portfolio-status`.

7. **"How much quota / credit have I used"** → invoke `usage-report`.

8. **Open-ended question or chat** → answer directly; default to voice reply
   via `respond-with-voice` if the answer is short.

## Reply modality default

Voice in → voice out, UNLESS the reply would be poorly suited to audio (code,
tables, copy-paste-needed URLs). In those cases, send TEXT and mention "I'll
send this as text since it has code/links."
