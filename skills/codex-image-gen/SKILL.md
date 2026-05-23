---
name: codex-image-gen
description: |
  Generate or edit an image by delegating to the user's Codex CLI subscription.
  Use when the user requests "draw / render / generate / create / sketch /
  design an image of X" or similar. Returns a local PNG path which the
  Telegram channel will upload as a photo.
allowed-tools: Bash(codex *), Bash(ls *), Bash(file *), Read
---

# codex-image-gen

When invoked, you have a `prompt` describing the desired image.

## How Codex CLI handles image generation

Codex CLI does NOT have a direct `--image-out` flag. Codex is an agentic
coding assistant authed via the user's ChatGPT subscription. To generate
an image, you instruct codex agentically — it then calls OpenAI's image
model on your behalf using its own ChatGPT auth (no API key needed).

`codex login status` should report "Logged in using ChatGPT" before this
skill runs; if it doesn't, fail fast with the explanation that auth is
missing — DO NOT assume the failure is anything else (e.g. missing
OPENAI_API_KEY is a wrong inference; codex doesn't use API keys for the
ChatGPT subscription path).

## Process

1. Pick an output path: `OUT=/tmp/codex_img_$(uuidgen | cut -c1-8).png`.

2. Invoke codex non-interactively with workspace-write sandbox so it can
   actually save the file:

   ```bash
   codex exec --skip-git-repo-check --sandbox workspace-write --cd /tmp \
     "Generate an image: <THE USER'S PROMPT, single line>.
     Save the PNG to $OUT and reply with only the absolute file path."
   ```

   Notes on the flags:
   - `--skip-git-repo-check` lets codex run outside a git repo (the bot
     usually runs from `/opt/claude-soma`, but we're writing to `/tmp`).
   - `--sandbox workspace-write` permits file writes inside `--cd`.
     The default `read-only` blocks codex from saving the PNG.
   - `--cd /tmp` makes `/tmp` codex's working root for this run.

3. Verify the file exists and looks like a real image:

   ```bash
   ls -lh "$OUT" && file "$OUT"
   ```

   Expect size 100 KB – 4 MB and `file` to say `PNG image data, ...`.
   If size is < 5 KB or file type is wrong, codex likely returned a
   description instead of saving — re-run with a more explicit save
   instruction or fail with that diagnostic.

4. Reply to the user with the absolute `$OUT` path. The Telegram channel's
   `mcp__plugin_telegram_telegram__reply` tool accepts a `files: [path]`
   parameter and uploads PNGs as photos with inline preview (not as voice
   notes, even if the request originated as a voice memo).

## Sanitization

- Strip newlines from the user's prompt before interpolating into the
  codex exec instruction (a literal newline can break the shell quoting).
- If the user's prompt contains content-policy-likely terms (nudity,
  violence, real-person likenesses, etc.), pass it through but be ready
  to surface codex's policy refusal verbatim — don't paraphrase.

## Notes

- Codex uses the user's separate ChatGPT subscription — does NOT count
  against Claude Max credits.
- For aspect-ratio / style / composition control, embed it in the prompt
  (e.g. "16:9 cinematic, soft rim light, painterly").
- If codex returns text without writing the file, the most common cause
  is the sandbox flag being wrong — verify `--sandbox workspace-write`
  is present.
- ChatGPT Plus / Pro subscribers have limited image-gen quota; sustained
  bursts may rate-limit.
