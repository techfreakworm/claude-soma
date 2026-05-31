## Standing Notify Convention

Every lead has `mcp__hermes-notify__notify_orchestrator` available. Use it at the
boundaries below. Omitting these calls means the operator has no visibility into
what you are doing and cannot respond to blockers in time.

### STARTED

Fire when you begin a major task (a discrete unit of work, not every tiny step).

```
mcp__hermes-notify__notify_orchestrator(
    type="STARTED",
    payload={"description": "<what you are about to do>", "eta": "<optional rough time estimate>"}
)
```

### MILESTONE

Fire on each git commit and on each major sub-task completion (e.g. schema
designed, API wired, tests passing). The server throttles delivery to one
notification per 5 minutes per lead, so fire freely — duplicates are absorbed.

```
mcp__hermes-notify__notify_orchestrator(
    type="MILESTONE",
    payload={"description": "<what was just completed>"}
)
```

### COMPLETED

Fire when the full task wraps. Include paths and URLs where relevant so the
operator can act immediately without asking follow-up questions.

```
mcp__hermes-notify__notify_orchestrator(
    type="COMPLETED",
    payload={
        "description": "<summary of what was done>",
        "paths": ["<absolute path 1>", ...],   # optional
        "urls": ["<url 1>", ...]                # optional
    }
)
```

### NEEDS_INPUT

Fire when you are blocked and cannot proceed without a human decision. Do NOT
spin, retry, or guess — block immediately and fire this.

```
mcp__hermes-notify__notify_orchestrator(
    type="NEEDS_INPUT",
    payload={
        "question": "<what you need the operator to decide>",
        "options": ["option A", "option B"],   # optional
        "timeout": 300                          # optional, seconds
    }
)
```

### ERROR

Fire on hard failures (unrecoverable error, repeated tool failure, unexpected
state that blocks all forward progress).

```
mcp__hermes-notify__notify_orchestrator(
    type="ERROR",
    payload={
        "error": "<short error label>",
        "context": "<what you were doing when it failed>",
        "traceback": "<relevant stack trace or command output>",   # optional
        "recoverable": false                                        # optional bool
    }
)
```
