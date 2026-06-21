# src/claude_soma/mcp_servers/project_orchestrator/spawner.py
from __future__ import annotations

import base64
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid
from pathlib import Path


# claude --bg was removed in Claude Code 2.1.150. We now wrap each project lead
# in a detached tmux session, matching the pattern used by
# systemd/claude-soma-channel.service. The native claude install at
# ~/.local/bin/claude is required because the npm wrap at /usr/bin/claude
# silently downgrades to --print mode for the flags we need.
NATIVE_CLAUDE_DEFAULT = "/home/ubuntu/.local/bin/claude"
CLAUDE_BIN = os.environ.get("HERMES_CLAUDE_BIN", NATIVE_CLAUDE_DEFAULT)
TMUX_BIN = os.environ.get("HERMES_TMUX_BIN", "/usr/bin/tmux")
TMUX_SESSION_PREFIX = "soma-proj-"

# Each lead runs inside its OWN transient systemd service, created at spawn time
# with systemd-run, so it lives in a sibling cgroup to
# claude-soma-channel.service instead of inside it. Restarting the channel
# (KillMode=control-group) then can't reach the lead. The lead also gets a
# DEDICATED tmux socket so its server is the only thing on that socket -- the
# server is born INSIDE the new unit, so it is parented to the lead's cgroup,
# not the orchestrator's. See docs/notes/2026-05-25-project-lead-cgroup-teardown.md.
SUDO_BIN = os.environ.get("HERMES_SUDO_BIN", "/usr/bin/sudo")
SYSTEMD_RUN_BIN = os.environ.get("HERMES_SYSTEMD_RUN_BIN", "/usr/bin/systemd-run")

# Multi-VPS: remote hosts run leads via the forced-command guard over ssh-on-tailnet.
SSH_BIN = os.environ.get("HERMES_SSH_BIN", "/usr/bin/ssh")
HOSTS_JSON = os.environ.get("HERMES_HOSTS_JSON", "/opt/claude-soma/config/claude/hosts.json")
SSH_CONNECT_TIMEOUT = int(os.environ.get("HERMES_SSH_CONNECT_TIMEOUT", "8"))
SSH_OP_TIMEOUT = int(os.environ.get("HERMES_SSH_OP_TIMEOUT", "15"))
SPAWN_REMOTE_TIMEOUT = int(os.environ.get("HERMES_SSH_SPAWN_TIMEOUT", "25"))
SYSTEMCTL_BIN = os.environ.get("HERMES_SYSTEMCTL_BIN", "/usr/bin/systemctl")
LEAD_SOCKET_PREFIX = "soma-lead-"
LEAD_UNIT_PREFIX = "claude-soma-lead-"

# The fresh systemd unit does NOT inherit the channel's environment the way the
# old shared-tmux spawn did, so we restore the essentials explicitly. The OAuth
# token (no API key -- Max OAuth only) comes from the EnvironmentFile, never the
# command line, so it can't leak via `ps`/audit. Leading `-` => optional, so
# spawn doesn't fail on a box without the file (CI/dev).
LEAD_USER = os.environ.get("HERMES_LEAD_USER", "ubuntu")
LEAD_GROUP = os.environ.get("HERMES_LEAD_GROUP", "ubuntu")
LEAD_HOME = os.environ.get("HERMES_LEAD_HOME", "/home/ubuntu")
LEAD_ENV_FILE = os.environ.get("HERMES_LEAD_ENV_FILE", "/etc/claude-soma/secrets.env")
LEAD_PATH = os.environ.get(
    "HERMES_LEAD_PATH",
    "/opt/claude-soma/.venv/bin:/home/ubuntu/.local/bin:/home/ubuntu/bin:"
    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin",
)

# Each lead's pane output is teed to <log dir>/<name>.log so a dead lead's
# output survives for forensics. The env var is read at call time (via
# _lead_log_path) so tests can redirect it off /var/log. The CLAUDE.md canonical
# log dir is /var/log/claude-soma (ubuntu-owned, so the orchestrator can create
# the file).
LEAD_LOG_DIR_DEFAULT = "/var/log/claude-soma"

# Leads load the bot's TOOL MCP servers (playwright x5, voice-stt/tts) via this
# curated --mcp-config: it is the bot's .mcp.json MINUS the control-plane
# servers (hermes-api, which unlink+rebinds the bot's dashboard socket, and
# project-orchestrator, which shares the registry and could recursively
# spawn/kill leads). Telegram is excluded everywhere (bot-only via --settings).
# Read at call time (HERMES_LEAD_MCP_CONFIG) so tests can point elsewhere; if
# the file is absent the flag is simply omitted (leads degrade to their own
# scope rather than failing to spawn).
LEAD_MCP_CONFIG_DEFAULT = "/opt/claude-soma/config/claude/lead-mcp.json"

# systemd-run returns quickly once the oneshot ExecStart (tmux new-session -d)
# detaches; 20s is generous headroom for talking to PID 1 under load.
SPAWN_TIMEOUT = 20

# Where Claude Code stores per-cwd trust state (NOT ~/.claude/settings.json —
# that key is ignored in 2.1.150; only this file is consulted). Override with
# HERMES_CLAUDE_GLOBAL_JSON for tests.
CLAUDE_GLOBAL_JSON_DEFAULT = str(Path.home() / ".claude.json")

MAX_BRIEF_CHARS = 100_000  # safety: keep briefs reasonable
NAME_RX = re.compile(r"^[a-z][a-z0-9-]{0,63}$")

# claude prints a Remote Control URL like
# https://claude.ai/code/session_018tGGH6weoRDokVSn7fmMhR at startup (verified
# live 2026-05-26 across three running leads). Older builds printed
# https://rc.claude.com/<id>; keep that as a fallback so a version skew doesn't
# silently break capture again. The session id is the only variable part
# (base62), so bound it to URL-safe chars instead of \S+ -- \S+ would swallow a
# trailing box-drawing glyph if the URL ever renders mid-line in the pane.
RC_URL_RX = re.compile(
    r"https?://claude\.ai/code/session_[A-Za-z0-9_-]+"
    r"|https?://rc\.claude\.com/\S+"
)

# How long to keep polling tmux capture for the Remote Control URL before
# giving up. claude prints the URL within ~5s usually, but the first capture
# right after tmux returns sometimes misses it.
RC_URL_POLL_SECONDS = 30
RC_URL_POLL_INTERVAL = 2


class BriefTooLong(Exception):
    pass


class InvalidProjectName(Exception):
    pass


def _claude() -> str:
    if Path(CLAUDE_BIN).exists():
        return CLAUDE_BIN
    found = shutil.which(CLAUDE_BIN) or shutil.which("claude")
    if found is None:
        raise RuntimeError(
            f"claude binary not found (tried {CLAUDE_BIN!r} and PATH)"
        )
    return found


def _tmux() -> str:
    if Path(TMUX_BIN).exists():
        return TMUX_BIN
    found = shutil.which(TMUX_BIN) or shutil.which("tmux")
    if found is None:
        raise RuntimeError(f"tmux binary not found (tried {TMUX_BIN!r} and PATH)")
    return found


def _session_name(name: str) -> str:
    return f"{TMUX_SESSION_PREFIX}{name}"


def _bare_name(name: str) -> str:
    """Accept either a bare project name or a full `soma-proj-<name>` session
    name (agent_id) and return the bare name."""
    if name.startswith(TMUX_SESSION_PREFIX):
        return name[len(TMUX_SESSION_PREFIX):]
    return name


def _lead_socket(name: str) -> str:
    return f"{LEAD_SOCKET_PREFIX}{name}"


def _lead_unit(name: str) -> str:
    return f"{LEAD_UNIT_PREFIX}{name}.service"


def _lead_log_path(name: str) -> Path:
    base = os.environ.get("HERMES_LEAD_LOG_DIR", LEAD_LOG_DIR_DEFAULT)
    return Path(base) / f"{name}.log"


def _wrap_in_transient_unit(
    name: str, inner_argv: list[str], mem_props: list[str] | None = None
) -> list[str]:
    """Wrap `inner_argv` so it runs inside its own transient systemd service,
    giving the lead a cgroup independent of claude-soma-channel.service.

    `mem_props` are optional per-tier --property=MemoryMax/MemoryHigh flags; the
    default (None/[]) keeps the local argv byte-for-byte unchanged."""
    return [
        SUDO_BIN, "-n", SYSTEMD_RUN_BIN, "--collect", "--quiet",
        f"--unit={_lead_unit(name)}",
        "--property=Type=oneshot",
        "--property=RemainAfterExit=yes",
        f"--property=User={LEAD_USER}",
        f"--property=Group={LEAD_GROUP}",
        f"--property=EnvironmentFile=-{LEAD_ENV_FILE}",
        *list(mem_props or []),
        f"--setenv=HOME={LEAD_HOME}",
        f"--setenv=PATH={LEAD_PATH}",
        "--setenv=CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1",
        # Disable Claude Code prompt suggestions in leads: the faint autosuggested
        # input-box text can be mistaken for / accidentally submitted as operator
        # input over the channel. The env var overrides the promptSuggestionEnabled
        # setting and is independent of --setting-sources.
        "--setenv=CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION=0",
        f"--setenv=HERMES_LEAD_NAME={name}",
        "--",
        *inner_argv,
    ]


def _claude_global_json() -> Path:
    return Path(os.environ.get("HERMES_CLAUDE_GLOBAL_JSON", CLAUDE_GLOBAL_JSON_DEFAULT))


def _pretrust_cwd(cwd: Path) -> None:
    """Pre-mark `cwd` as trusted in ~/.claude.json so claude skips the
    "Quick safety check: is this a project you trust?" interactive dialog at
    startup. Without this, a tmux-detached claude blocks forever waiting for
    a human to hit Enter.

    Idempotent: existing project entries are preserved/merged. The whole file
    is rewritten atomically (tempfile + os.replace) to avoid a torn write that
    would corrupt other projects' state.
    """
    path = _claude_global_json()
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        # Don't bulldoze a corrupt file; just bail and let the dialog appear
        # (operator will see the prompt in the tmux pane and can investigate).
        return

    projects = data.setdefault("projects", {})
    entry = projects.setdefault(str(cwd), {})
    entry["hasTrustDialogAccepted"] = True
    entry.setdefault("projectOnboardingSeenCount", 0)

    # Atomic write: write to a sibling tempfile, fsync, rename. Preserves
    # the existing inode's owner/mode by recreating them after the rename.
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=".claude.json.", suffix=".tmp", dir=str(parent),
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _capture_rc_url(session: str, socket: str, timeout: float | None = None) -> str:
    """Poll the tmux pane until the Remote Control URL appears, or timeout.

    The original implementation was a single capture-pane call right after
    tmux returned, which raced with claude's startup output — most of the
    time the URL hadn't printed yet at that exact instant and we silently
    returned "". Polling gives claude time to actually emit the URL.

    `timeout=None` reads the module constant at call time, so tests can
    monkeypatch RC_URL_POLL_SECONDS without rebinding the default.

    `socket` is the lead's dedicated tmux socket (-L), since each lead now runs
    its own tmux server inside its own systemd unit.
    """
    if timeout is None:
        timeout = RC_URL_POLL_SECONDS
    deadline = time.monotonic() + timeout
    last_stdout = ""
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                [_tmux(), "-L", socket, "capture-pane", "-p", "-t", session],
                capture_output=True, text=True, check=True, timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return ""
        last_stdout = result.stdout or ""
        match = RC_URL_RX.search(last_stdout)
        if match:
            return match.group(0)
        time.sleep(RC_URL_POLL_INTERVAL)
    return ""


# ---------------------------------------------------------------------------
# Multi-VPS: host registry + RemoteRunner (speaks the forced-command guard
# contract in scripts/remote-exec-guard.sh). For a remote host RemoteRunner
# NEVER builds systemd-run/tmux -- the guard constructs those on B with
# User=ubuntu + unit hardcoded. Local host keeps today's path unchanged.
# ---------------------------------------------------------------------------

GUARD_DENY_RC = 99


def load_hosts() -> dict:
    try:
        with open(HOSTS_JSON) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"local": {"tailnet_ip": None, "ssh_identity": None}}


def _host_cfg(host: str) -> dict:
    hosts = load_hosts()
    if host not in hosts:
        raise RuntimeError(f"unknown host {host!r}; not in {HOSTS_JSON}")
    return hosts[host]


def build_guard_command(verb, name, *, mode=None, uuid_=None, tier=None, brief=None) -> str:
    """Build ONE guard-contract line. brief is base64 (== `base64 -w0`, no newlines)."""
    if verb in ("spawn", "resume"):
        b64 = base64.b64encode(brief.encode("utf-8")).decode("ascii")
        return f"{verb} {name} {mode} {uuid_} {tier} {b64}"
    if verb in ("kill", "capture", "list", "has-session", "stat-transcript", "rc-url"):
        return f"{verb} {name}"
    raise ValueError(f"unknown guard verb {verb!r}")


class RemoteRunner:
    """Speaks the forced-command guard contract over ssh-on-tailnet."""

    def __init__(self, host: str) -> None:
        self.host = host
        cfg = _host_cfg(host)
        if not cfg.get("tailnet_ip"):
            raise RuntimeError(f"host {host!r} has no tailnet_ip (not remote-capable)")
        self.ip = cfg["tailnet_ip"]
        self.user = cfg.get("ssh_user") or "ubuntu"
        self.identity = cfg["ssh_identity"]

    def _argv(self, line: str) -> list[str]:
        return [
            SSH_BIN, "-i", self.identity,
            "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={SSH_CONNECT_TIMEOUT}",
            f"{self.user}@{self.ip}", line,
        ]

    def run(self, line: str, *, timeout: int) -> subprocess.CompletedProcess:
        return subprocess.run(
            self._argv(line), capture_output=True, text=True, timeout=timeout
        )

    def spawn(self, name, mode, uuid_, tier, brief, *, timeout=SPAWN_REMOTE_TIMEOUT):
        return self.run(
            build_guard_command("spawn", name, mode=mode, uuid_=uuid_, tier=tier, brief=brief),
            timeout=timeout,
        )

    def resume(self, name, mode, uuid_, tier, prompt, *, timeout=SPAWN_REMOTE_TIMEOUT):
        return self.run(
            build_guard_command("resume", name, mode=mode, uuid_=uuid_, tier=tier, brief=prompt),
            timeout=timeout,
        )

    def kill(self, name, *, timeout=SSH_OP_TIMEOUT):
        return self.run(f"kill {name}", timeout=timeout)

    def capture(self, name, *, timeout=SSH_OP_TIMEOUT):
        return self.run(f"capture {name}", timeout=timeout)

    def list_panes(self, name, *, timeout=SSH_OP_TIMEOUT):
        return self.run(f"list {name}", timeout=timeout)

    def has_session(self, name, *, timeout=SSH_OP_TIMEOUT):
        return self.run(f"has-session {name}", timeout=timeout)

    def rc_url(self, name, *, timeout=SSH_OP_TIMEOUT):
        return self.run(f"rc-url {name}", timeout=timeout)


def _classify_remote_liveness(rc: int) -> str:
    if rc == 0:
        return "alive"
    if rc == 1:
        return "dead"  # tmux has-session: no such session
    # rc==255 ssh transport; rc==99 guard DENY; anything else -> NOT 'dead'
    # (never revive on ambiguity).
    return "unreachable"


def _local_mem_props(host: str, tier: str) -> list[str]:
    caps = _host_cfg(host).get("tier_caps", {}).get(tier)
    if not caps:
        return []
    return [
        f"--property=MemoryMax={caps['max_mb']}M",
        f"--property=MemoryHigh={caps['high_mb']}M",
    ]


def _raise_on_guard_error(cp: subprocess.CompletedProcess, ctx: str) -> None:
    stderr = cp.stderr or ""
    if cp.returncode == GUARD_DENY_RC or "remote-exec-guard: DENY" in stderr:
        raise RuntimeError(f"{ctx}: guard DENY: {stderr[-300:]}")
    if cp.returncode == 255:
        raise RuntimeError(f"{ctx}: ssh transport failure: {stderr[-300:]}")
    if cp.returncode != 0:
        raise RuntimeError(f"{ctx}: rc={cp.returncode}: {stderr[-300:]}")


def _capture_rc_url_remote(rr: "RemoteRunner", name: str, timeout: float | None = None) -> str:
    deadline = time.monotonic() + (RC_URL_POLL_SECONDS if timeout is None else timeout)
    while time.monotonic() < deadline:
        try:
            cp = rr.rc_url(name)  # reads the URL from B's pipe-pane log
        except (subprocess.SubprocessError, OSError):
            return ""
        m = RC_URL_RX.search(cp.stdout or "")
        if m:
            return m.group(0)
        time.sleep(RC_URL_POLL_INTERVAL)
    return ""


def _spawn_remote(*, name, brief, cwd, permission_mode, session_uuid, host, tier) -> dict:
    if session_uuid is None:
        session_uuid = str(uuid.uuid4())
    rr = RemoteRunner(host)
    cp = rr.spawn(name, permission_mode, session_uuid, tier, brief)
    _raise_on_guard_error(cp, f"remote spawn {name!r} on {host}")
    rc_url = _capture_rc_url_remote(rr, name)
    return {"agent_id": _session_name(name), "rc_url": rc_url,
            "cwd": str(cwd), "session_uuid": session_uuid}


def _resume_remote(*, name, prompt, cwd, permission_mode, session_uuid, host, tier) -> dict:
    rr = RemoteRunner(host)
    cp = rr.resume(name, permission_mode, session_uuid, tier, prompt)
    _raise_on_guard_error(cp, f"remote resume {name!r} on {host}")
    rc_url = _capture_rc_url_remote(rr, name)
    return {"agent_id": _session_name(name), "rc_url": rc_url,
            "cwd": str(cwd), "session_uuid": session_uuid}


def spawn_background_lead(
    *,
    name: str,
    brief: str,
    cwd: Path,
    permission_mode: str,
    session_uuid: str | None = None,
    extra_args: list[str] | None = None,
    host: str = "local",
    tier: str = "standard",
) -> dict:
    if not NAME_RX.match(name):
        raise InvalidProjectName(
            f"project name must match {NAME_RX.pattern}, got {name!r}"
        )
    if len(brief) > MAX_BRIEF_CHARS:
        raise BriefTooLong(f"brief is {len(brief)} chars (max {MAX_BRIEF_CHARS})")

    # Remote host: the forced-command guard on B builds systemd-run+tmux+claude-safe
    # itself (pretrust/mkdir/RC-capture included). We only emit the guard contract.
    if host != "local":
        return _spawn_remote(
            name=name, brief=brief, cwd=cwd, permission_mode=permission_mode,
            session_uuid=session_uuid, host=host, tier=tier,
        )

    cwd.mkdir(parents=True, exist_ok=True)

    # Mark the cwd as trusted BEFORE spawning, otherwise claude blocks on the
    # safety dialog forever in a detached tmux pane (no human to hit Enter).
    _pretrust_cwd(cwd)

    # Generate a cloud session UUID for this lead if none was supplied. The UUID
    # is passed as --session-id so claude uploads this session to the cloud under
    # a stable identifier, enabling --resume <uuid> after a kill/OOM/crash.
    if session_uuid is None:
        session_uuid = str(uuid.uuid4())

    session = _session_name(name)
    socket = _lead_socket(name)
    claude_argv: list[str] = [
        _claude(),
        # --session-id <uuid>: pins a stable cloud session ID for this fresh
        # spawn so the operator can later retrieve it via --resume <uuid> after
        # a kill/OOM/crash. --continue is intentionally absent: combining
        # --continue with --session-id without --fork-session is rejected by
        # claude-code ("--session-id can only be used with --continue or
        # --resume if --fork-session is also specified"). For resumes use
        # resume_background_lead, which passes --resume <uuid> instead.
        "--session-id", session_uuid,
        # --remote-control <name>: project leads stay alive after their first
        # task completes (otherwise claude exits) AND get an rc.claude.com URL
        # the operator can attach to from the Claude mobile app / web. The name
        # matches our tmux session name so they're easy to correlate.
        "--remote-control", session,
        "--add-dir", str(cwd),
        "--permission-mode", permission_mode,
        "--dangerously-skip-permissions",
        "--effort", "max",
        # Load USER scope too, not just project,local. This gives leads the
        # user-scope MCP servers (sequential-thinking) and the user's
        # skills/agents/plugins. It used to be `project,local` to keep the
        # then-user-scoped telegram plugin out of leads (it would hijack the
        # bot's poller). That risk is GONE: telegram was moved out of user
        # scope -- the bot now opts in ONLY via
        # `--settings .../channel-settings.json`, which leads never receive.
        # So a lead gets every user MCP/skill/plugin EXCEPT telegram. (Verified
        # 2026-05-26: ~/.claude.json + ~/.claude/settings.json enabledPlugins
        # contain no telegram; /home/ubuntu even disables it explicitly.)
        "--setting-sources", "user,project,local",
    ]
    # Inject the bot's tool MCP servers (playwright, voice) from the curated
    # lead config -- but NOT hermes-api/project-orchestrator (see
    # LEAD_MCP_CONFIG_DEFAULT). Omit the flag if the file isn't present so a
    # box without it (pre-deploy / CI) still spawns leads cleanly.
    lead_mcp_config = os.environ.get("HERMES_LEAD_MCP_CONFIG", LEAD_MCP_CONFIG_DEFAULT)
    if Path(lead_mcp_config).exists():
        claude_argv += ["--mcp-config", lead_mcp_config]
    if extra_args:
        claude_argv.extend(extra_args)
    # The brief is the positional prompt and MUST be guarded by a `--`
    # end-of-options separator. claude's --mcp-config is variadic ("<configs...>"),
    # so a bare trailing brief right after --mcp-config <path> gets swallowed as a
    # second config-file path -> ENAMETOOLONG -> the lead crashes at startup
    # (regression fixed here). `--` also protects a brief that starts with `-`.
    claude_argv += ["--", brief]

    # Tee the lead's pane output to its own log so a dead lead's last output
    # survives. Best-effort: create the dir if we can, but never fail the spawn
    # over logging -- if the dir is unwritable the chained `cat` just exits and
    # the pane is unaffected.
    log_path = _lead_log_path(name)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    # tmux on the lead's OWN socket, wrapped in its OWN transient systemd unit
    # so the server is parented to the lead's cgroup, not the channel's. The
    # `;` is tmux's command separator: chaining pipe-pane into the SAME
    # invocation tees the pane from the moment the session is born, adds no
    # extra spawn subprocess, and -- because the `cat` writer is forked by the
    # tmux server (the lead's cgroup) -- the logging survives a channel restart
    # exactly like the lead does. -O pipes output only; -o is a no-op if a pipe
    # already exists (idempotent).
    tmux_argv: list[str] = [
        _tmux(), "-L", socket, "new-session", "-d", "-s", session,
        "-c", str(cwd), *claude_argv,
        ";", "pipe-pane", "-O", "-o", "-t", session,
        f"cat >> {shlex.quote(str(log_path))}",
    ]
    cmd: list[str] = _wrap_in_transient_unit(
        name, tmux_argv, mem_props=_local_mem_props(host, tier)
    )

    try:
        subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=SPAWN_TIMEOUT,
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "")[-500:]
        if "already exists" in stderr.lower():
            raise RuntimeError(
                f"a systemd unit for project {name!r} already exists "
                f"({_lead_unit(name)}); kill the existing project first, or if "
                f"it is a stale/dead lead run kill_project to clear it. "
                f"stderr: {stderr}"
            ) from e
        raise RuntimeError(f"spawn failed for {name!r}: {stderr}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"spawn timed out for {name!r} ({SPAWN_TIMEOUT}s)") from e

    rc_url = _capture_rc_url(session, socket)

    return {
        "agent_id": session,
        "rc_url": rc_url,
        "cwd": str(cwd),
        "session_uuid": session_uuid,
    }


def _estimate_context_tokens(name: str) -> int:
    """Estimate the cloud session's token count for a lead by summing payload_json
    lengths in lead_events and dividing by 4 (chars-to-tokens approximation).
    Returns 0 if the db or table is absent or on any sqlite error."""
    db_path = os.environ.get("HERMES_ORCH_DB", "/opt/claude-soma/registry.sqlite")
    if not os.path.exists(db_path):
        return 0
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT SUM(LENGTH(payload_json)) FROM lead_events WHERE lead = ?",
            (name,),
        ).fetchone()
        conn.close()
        total = row[0] if row and row[0] is not None else 0
        return int(total) // 4
    except sqlite3.Error:
        return 0


def resume_background_lead(
    *,
    name: str,
    cwd: Path,
    permission_mode: str,
    session_uuid: str,
    extra_args: list[str] | None = None,
    resume_prompt_suffix: str | None = None,
    force: bool = False,
    host: str = "local",
    tier: str = "standard",
) -> dict:
    """Spawn a tmux+systemd lead using --resume <session_uuid> instead of --continue.

    Used by the `resume_project` MCP tool after a lead dies from OOM/crash/kill.
    --resume pulls the named session from the Claude cloud, so the lead picks up
    its full prior transcript even if the local cwd transcript is gone.

    Calls kill_session(name) first to clear any lingering `active (exited)` unit
    that would cause systemd-run to reject the spawn with "already exists".
    """
    if not NAME_RX.match(name):
        raise InvalidProjectName(
            f"project name must match {NAME_RX.pattern}, got {name!r}"
        )
    est_tokens = _estimate_context_tokens(name)
    threshold = int(os.environ.get("HERMES_RESUME_CONTEXT_GUARD_TOKENS", "200000"))
    if est_tokens > threshold and not force:
        raise RuntimeError(
            f"context guard: {name} estimated at {est_tokens} tokens > {threshold}; "
            "kill + re-spawn fresh, or pass force=True to override"
        )

    # Remote host: the guard's `resume` verb stops the old unit and re-spawns
    # with --resume on B. We only emit the contract.
    if host != "local":
        resume_prompt = (
            "You have been resumed after an interruption. "
            "Review your prior work in this session and continue from where you left off."
        )
        if resume_prompt_suffix:
            resume_prompt = resume_prompt + "\n\n" + resume_prompt_suffix
        return _resume_remote(
            name=name, prompt=resume_prompt, cwd=cwd, permission_mode=permission_mode,
            session_uuid=session_uuid, host=host, tier=tier,
        )

    cwd.mkdir(parents=True, exist_ok=True)
    _pretrust_cwd(cwd)

    # Clean up any lingering transient unit (active (exited)) from the dead lead.
    kill_session(name)

    session = _session_name(name)
    socket = _lead_socket(name)
    # Fixed resume prompt: the lead's full transcript is restored from the cloud
    # via --resume; the brief below is a new user message telling it to continue.
    resume_prompt = (
        "You have been resumed after an interruption. "
        "Review your prior work in this session and continue from where you left off."
    )
    if resume_prompt_suffix:
        resume_prompt = resume_prompt + "\n\n" + resume_prompt_suffix
    claude_argv: list[str] = [
        _claude(),
        # --resume <uuid>: pull session from cloud. REPLACES --continue (we want
        # the cloud transcript, not --continue's local-file fallback).
        "--resume", session_uuid,
        "--remote-control", session,
        "--add-dir", str(cwd),
        "--permission-mode", permission_mode,
        "--dangerously-skip-permissions",
        "--effort", "max",
        "--setting-sources", "user,project,local",
    ]
    lead_mcp_config = os.environ.get("HERMES_LEAD_MCP_CONFIG", LEAD_MCP_CONFIG_DEFAULT)
    if Path(lead_mcp_config).exists():
        claude_argv += ["--mcp-config", lead_mcp_config]
    if extra_args:
        claude_argv.extend(extra_args)
    claude_argv += ["--", resume_prompt]

    log_path = _lead_log_path(name)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    tmux_argv: list[str] = [
        _tmux(), "-L", socket, "new-session", "-d", "-s", session,
        "-c", str(cwd), *claude_argv,
        ";", "pipe-pane", "-O", "-o", "-t", session,
        f"cat >> {shlex.quote(str(log_path))}",
    ]
    cmd: list[str] = _wrap_in_transient_unit(
        name, tmux_argv, mem_props=_local_mem_props(host, tier)
    )

    try:
        subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=SPAWN_TIMEOUT,
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "")[-500:]
        raise RuntimeError(f"resume failed for {name!r}: {stderr}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"resume timed out for {name!r} ({SPAWN_TIMEOUT}s)") from e

    rc_url = _capture_rc_url(session, socket)
    return {
        "agent_id": session,
        "rc_url": rc_url,
        "cwd": str(cwd),
        "session_uuid": session_uuid,
    }


def lead_liveness(name: str, host: str = "local") -> str:
    """Tri-state liveness: 'alive' | 'dead' | 'unreachable'.

    Local is never 'unreachable': a local tmux tool-error stays 'alive',
    preserving the old conservative True-on-error (a false 'dead' would hide a
    running lead and risk a duplicate respawn). Remote distinguishes an
    ssh-transport failure ('unreachable' -> never revive) from a tmux-reported
    dead session ('dead' -> safe to revive).
    """
    bare = _bare_name(name)
    if host != "local":
        try:
            cp = RemoteRunner(host).has_session(bare)
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError):
            return "unreachable"
        return _classify_remote_liveness(cp.returncode)
    session = _session_name(bare)
    socket = _lead_socket(bare)
    try:
        result = subprocess.run(
            [_tmux(), "-L", socket, "has-session", "-t", session],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return "alive"  # conservative (== old True-on-error)
    return "alive" if result.returncode == 0 else "dead"


def is_lead_alive(name: str, host: str = "local") -> bool:
    """Bool wrapper over lead_liveness (alive => True) for existing callers.

    NOTE: remote 'unreachable' reads False here. Callers that must distinguish
    unreachable from dead (the watchdog, _reconcile_active, get_status) call
    lead_liveness() directly so they never revive/demote an unreachable-host lead.
    """
    return lead_liveness(name, host) == "alive"


def discover_team(name: str, host: str = "local") -> list[dict[str, str]]:
    """Best-effort roster of a lead's agent-team teammates, read live from its
    tmux panes.

    With CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 a lead spawns its teammates as
    split panes in its window (see docs/KNOWN_BUGS.md), so every pane beyond the
    lead's own is a teammate. We read it live (rather than persist a table)
    because teammates are ephemeral -- they die with the session -- so a cached
    roster would go stale. Returns [] if the lead is gone or has no team.

    Each teammate is labelled by its STABLE pane identity -- ``teammate-<pane_index>``
    with the role from the pane title. We deliberately do NOT relabel panes with
    self-reported "canonical" handles from the registry: there is no reliable
    correspondence between a self-reported handle and a specific live pane, and the
    old positional substitution stamped the i-th registry handle onto the i-th pane
    -- including a lead's own self-registration row whose handle equals a lead name
    -- which made one lead's teammate render under another lead in the admin graph
    (bug 2026-06-16). A ``teammate-<idx>`` handle can never equal a lead name, so
    every teammate nests under its true parent.
    """
    bare = _bare_name(name)
    if host != "local":
        try:
            cp = RemoteRunner(host).list_panes(bare)
        except (subprocess.SubprocessError, OSError):
            return []
        if cp.returncode != 0:
            return []
        stdout = cp.stdout or ""
    else:
        session = _session_name(bare)
        socket = _lead_socket(bare)
        try:
            result = subprocess.run(
                [_tmux(), "-L", socket, "list-panes", "-s", "-t", session,
                 "-F", "#{pane_index}\t#{pane_dead}\t#{pane_title}"],
                capture_output=True, text=True, timeout=10,
            )
        except (subprocess.SubprocessError, OSError):
            return []
        if result.returncode != 0:
            return []
        stdout = result.stdout
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    # The first pane is the lead itself; the rest are its teammates.
    team: list[dict[str, str]] = []
    for i, line in enumerate(lines[1:], start=1):
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        idx, dead, title = parts[0], parts[1], parts[2]
        team.append({
            "handle": f"teammate-{idx}",
            "role": title.strip() or "teammate",
            "status": "dead" if dead == "1" else "active",
        })

    return team


def kill_session(name: str, host: str = "local") -> None:
    bare = _bare_name(name)
    if host != "local":
        # The guard's `kill` verb stops the unit + kills the tmux session on B and
        # exits 0 even if already gone; a non-zero rc means DENY or ssh transport
        # failure, which should surface rather than falsely report "killed".
        cp = RemoteRunner(host).kill(bare)
        _raise_on_guard_error(cp, f"remote kill {bare!r}")
        return
    session = _session_name(bare)
    socket = _lead_socket(bare)
    unit = _lead_unit(bare)
    # Stop the transient unit first: KillMode=control-group tears down the whole
    # cgroup (tmux server included) and avoids leaking an `active (exited)` unit.
    # Best-effort -- the unit may already be gone.
    try:
        subprocess.run(
            [SUDO_BIN, "-n", SYSTEMCTL_BIN, "stop", unit],
            capture_output=True, text=True, check=True, timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    # Belt-and-suspenders: kill the session on its own socket too, tolerating a
    # server already torn down by the unit stop above.
    try:
        subprocess.run(
            [_tmux(), "-L", socket, "kill-session", "-t", session],
            capture_output=True, text=True, check=True, timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # Already gone, or tmux server down — nothing to do.
        return
