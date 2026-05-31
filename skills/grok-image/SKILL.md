---
name: grok-image
description: |
  Generate an image via the grok CLI (/imagine command). Returns a local PNG/JPG
  path. Used as one of the two providers in parallel dual-image dispatch (the other
  is codex-image-gen). Do NOT call this inline — always dispatch via Agent so the
  channel stays responsive.
allowed-tools: mcp__grok_image__generate_image, Bash(ls *), Bash(file *)
model: opus
---

# grok-image

When invoked, you have a `prompt` describing the desired image.

## How grok image generation works

The `mcp__grok_image__generate_image` MCP tool calls the grok CLI:

```
grok -p "/imagine <prompt>" --output-format json
```

The CLI returns a JSON envelope with a `text` field containing a markdown image
link. The server extracts the path and copies the file to `output_dir` (default
`/tmp`). Returns `{"path": str, "session_id": str}`.

## Steps

1. Call `mcp__grok_image__generate_image` with the user's prompt and
   `output_dir="/tmp"`.
2. Return ONLY the JSON result: `{"path": "<absolute path>", "session_id": "<id>"}`.
3. Do NOT send the image to Telegram yourself. The orchestrator collects both
   results (grok + codex) and sends them together in one reply.

## Error handling

If `mcp__grok_image__generate_image` raises a `RuntimeError` or `FileNotFoundError`,
return a plain dict indicating the error:

```python
{"error": "<error message>", "provider": "grok"}
```

The orchestrator will surface this in the reply caption alongside the other
provider's successful result.
