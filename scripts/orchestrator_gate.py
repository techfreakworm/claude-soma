#!/usr/bin/env python3
from __future__ import annotations
import json, os, re, shlex, sys

REASON_TAIL = " — dispatch via Agent (model=opus, run_in_background=true) instead."
ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
WRAPPER_CMDS = {"sudo", "exec", "nohup", "setsid", "env", "time"}
SHELL_C_CMDS = {"bash", "sh", "zsh", "dash"}
EVAL_CMDS    = {"eval"}

def deny(reason: str) -> None:
    json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse",
        "permissionDecision": "deny", "permissionDecisionReason": reason}}, sys.stdout)
    sys.exit(0)

def extract_first_cmd(cmd: str):
    cmd_head = cmd.split("<<", 1)[0]
    try:
        tokens = shlex.split(cmd_head, posix=True)
    except ValueError:
        return None
    if any(t.startswith("$(") or "`" in t for t in tokens):
        return None  # fail-OPEN on substitution; log telemetry below
    while tokens and (ENV_ASSIGN.match(tokens[0]) or tokens[0] in WRAPPER_CMDS):
        tokens = tokens[1:]
    if not tokens: return None
    head = tokens[0]
    if head in SHELL_C_CMDS and len(tokens) >= 3 and tokens[1] in ("-c", "-lc"):
        return extract_first_cmd(tokens[2])
    if head in EVAL_CMDS and len(tokens) >= 2:
        return extract_first_cmd(" ".join(tokens[1:]))
    return os.path.basename(head), tokens

def log_telemetry(event: dict) -> None:
    try:
        path = os.path.expanduser("~/.claude-soma/activity.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        import time
        with open(path, "a") as f:
            f.write(json.dumps({"ts": time.time(), "source": "orchestrator_gate_v3", **event}) + "\n")
    except Exception:
        pass

def main() -> None:
    try: event = json.load(sys.stdin)
    except Exception: sys.exit(0)
    tool = event.get("tool_name", "")
    if not tool: sys.exit(0)

    # Tool-name denies
    if tool in {"Edit", "NotebookEdit"}: deny(f"File edits are substantive work{REASON_TAIL}")
    if tool == "Write":
        fpath = event.get("tool_input", {}).get("file_path", "")
        if (not fpath) or fpath.startswith(("/opt/claude-soma/", "/etc/", "/var/lib/")):
            deny(f"File edits are substantive work{REASON_TAIL}")
    if tool in {"WebFetch", "WebSearch", "Skill"}: deny(f"{tool} is slow/multi-step{REASON_TAIL}")
    if tool.startswith(("mcp__playwright", "mcp__claude_ai_")): deny(f"{tool} is slow/multi-step{REASON_TAIL}")
    if tool in {"mcp__huggingface__gr1_z_image_turbo_generate", "mcp__huggingface__dynamic_space"}:
        deny(f"{tool} is slow/multi-step{REASON_TAIL}")

    if tool != "Bash": sys.exit(0)
    cmd = event.get("tool_input", {}).get("command", "")
    if not cmd: sys.exit(0)
    parsed = extract_first_cmd(cmd)
    if parsed is None:
        log_telemetry({"action": "fail_open_substitution_or_parse_error", "cmd": cmd[:300]})
        sys.exit(0)
    bin_name, tokens = parsed
    args = tokens[1:]

    def first_positional() -> str | None:
        for t in args:
            if t.startswith("-"): continue
            return t
        return None

    fp = first_positional()
    if bin_name in ("apt","apt-get") and fp in ("install","update","upgrade"):
        deny(f"Package install in Bash{REASON_TAIL}")
    if bin_name in ("pip","pip3","pipx") and fp == "install":
        deny(f"Package install in Bash{REASON_TAIL}")
    if bin_name == "npm"  and fp in ("install","i","test"): deny(f"Package install/test in Bash{REASON_TAIL}")
    if bin_name == "pnpm" and fp in ("install","add","test"): deny(f"Package install/test in Bash{REASON_TAIL}")
    if bin_name == "yarn" and fp in ("add","install"): deny(f"Package install in Bash{REASON_TAIL}")
    if bin_name == "cargo" and fp in ("build","install","test"): deny(f"Build/install/test in Bash{REASON_TAIL}")
    if bin_name == "bun"  and fp == "install": deny(f"Package install in Bash{REASON_TAIL}")
    if bin_name == "git"  and fp in ("clone","pull","push"): deny(f"Network git op in Bash{REASON_TAIL}")
    if bin_name == "git"  and fp == "fetch" and any(a in ("--depth","--shallow-since") for a in args):
        deny(f"Network git op in Bash{REASON_TAIL}")
    if bin_name == "docker" and fp in ("build","run"): deny(f"Build command in Bash{REASON_TAIL}")
    if bin_name in ("make","cmake"): deny(f"Build command in Bash{REASON_TAIL}")
    if bin_name == "pytest": deny(f"Test command in Bash{REASON_TAIL}")
    if bin_name == "claude": deny(f"Direct claude subprocess in Bash{REASON_TAIL}")
    if bin_name == "codex":  deny(f"Heavy compute in Bash{REASON_TAIL}")
    if bin_name == "ffmpeg" and "-i" in args: deny(f"Heavy compute in Bash{REASON_TAIL}")
    if bin_name == "whisper-cli" and "-f" in args: deny(f"Heavy compute in Bash{REASON_TAIL}")
    if bin_name in ("curl","wget"):
        full = " ".join(tokens)
        if not re.search(r"\b(localhost|127\.0\.0\.1|0\.0\.0\.0)\b", full):
            deny(f"Network curl/wget in Bash{REASON_TAIL}")
    sys.exit(0)

if __name__ == "__main__": main()
