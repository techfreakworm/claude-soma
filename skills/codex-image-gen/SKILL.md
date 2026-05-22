---
name: codex-image-gen
description: |
  Generate or edit an image by delegating to the user's Codex CLI subscription.
  Use when the user requests "draw / render / generate / create / sketch /
  design an image of X" or similar. Returns a local path to the generated PNG
  which the Telegram channel will upload as an image.
allowed-tools: Bash(codex *), Read
---

# codex-image-gen

When invoked, you have a `prompt` describing the desired image.

## Process

1. Choose an output path: `/tmp/codex_img_<short-uuid>.png`.

2. Invoke Codex's image generation:

```bash
codex --image --prompt "<the user's image prompt, sanitized>" --output <path>
```

3. Confirm the file exists with `ls -lh <path>`. Expected size 100 KB – 4 MB.

4. Reply to the user with the local path. The Telegram channel's media
   handling will upload the file as an image (NOT a voice note even if the
   request came in via voice).

## Notes

- Codex uses the user's separate ChatGPT subscription — does NOT count against
  Claude Max credits.
- For style or aspect-ratio control, pass them in the prompt (e.g. "16:9
  cinematic, dramatic lighting, ...").
- If Codex fails (e.g. content policy), reply with an explanation and offer
  to refine the prompt.
