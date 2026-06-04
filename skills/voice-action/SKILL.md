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

## Always echo the transcript first — HARD GATE

**This is a HARD GATE. Skipping it is a hard error.** Before routing or
answering, your reply MUST begin with the `Heard:` echo line on its own
line:

```
Heard: "<the transcript>"

<your reply>
```

Rules:

- Every voice note. Every single time. No exceptions for one-word replies,
  no exceptions when also replying by voice, no exceptions if you're
  acting on the inferred intent without a textual answer (in that case
  the `Heard:` line stands alone as the reply text alongside whatever
  tool calls you trigger).
- **Verbatim transcript** — copy the `voice-stt` output as-is. Do not
  paraphrase, normalize, or "clean up" — the whole point is so the user
  sees what the base.en model heard.
- The `Heard:` line is the FIRST content in your reply. Anything else
  goes after a blank line.

Why: STT (`base.en`) mishears at a non-trivial rate, especially names,
numbers, and code identifiers. The echo is how the user catches mishearings
in real time. Silently acting on a misheard transcript wastes a turn AND
hides the failure mode — both bad.

Then route or answer per below.

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
