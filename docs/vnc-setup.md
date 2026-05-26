# VNC desktop on the VPS (for platform auth)

A lightweight XFCE desktop runs on the OCI VPS, reachable over the Tailscale tailnet via
VNC. Its purpose is **human-in-the-loop platform authentication**: you VNC in, do the
interactive browser logins (X, LinkedIn, Medium, etc.), and the repo's Playwright-based
social-automation skills then reuse those authenticated sessions.

It is reachable **only over the tailnet** - never the public internet.

## Connect

- Host/port: `100.103.37.115:15901` (the VPS tailnet IP) or
  `claude-soma-vps.taile26033.ts.net:15901`
- Display: `:1`, geometry 1920x1080
- Password: stored in `/home/ubuntu/.vnc/passwd` on the VPS (8 chars; VNC auth truncates
  at 8). Reset procedure below.
- Any standard client works (TigerVNC Viewer, RealVNC Viewer, bVNC on Android). The raw
  RFB handshake flows through, so use the IP/port directly.

## Architecture / security model

- The VNC server (`Xtigervnc`, display `:1`) binds **127.0.0.1:5901 only**
  (`-localhost yes`). It never listens on a public-facing socket.
- `tailscale serve --bg --tcp 15901 tcp://localhost:5901` forwards tailnet port `15901`
  to the localhost VNC server. This is Tailscale **serve** (tailnet-only), NOT Funnel -
  there is no public exposure.
- The box's iptables only ACCEPTs new TCP on 22/80/443 plus tailnet traffic via the
  `ts-input` (tailscale0) chain; everything else hits a final REJECT. So even setting
  aside the localhost binding, 5901 is not reachable from the public internet.
- Net effect: only devices on your tailnet can reach the desktop, and the VNC server
  itself is safe-by-construction (localhost-bound) independent of firewall correctness.

## IMPORTANT gotcha: do not serve on port 5901

Do **not** point `tailscale serve` at port `5901`. TigerVNC's `vncserver` wrapper runs a
pre-start gate (`checkTCPPortUsed($rfbport)` in `/usr/share/perl5/TigerVNC/Wrapper.pm`,
near line 946) that refuses to start if port 5901 is already bound - printing
"A VNC server is already running as :1". A `tailscale serve` listener on 5901 trips that
gate, so `vncserver@1.service` crash-loops on every restart and on every reboot.

Forward a **non-590x** tailnet port (we use `15901`) to `localhost:5901` instead. That
keeps port 5901 free for the VNC server's own pre-start check while still giving you a
clean tailnet endpoint. (This bug cost a long debugging session on 2026-05-26; the
original session only survived because it had started before the serve forward existed.)

## Reset the VNC password

```bash
printf '%s' 'NEWPASSWORD' | vncpasswd -f > /home/ubuntu/.vnc/passwd
chmod 600 /home/ubuntu/.vnc/passwd
sudo systemctl restart vncserver@1.service
```

The restart is required: the running server reads a per-session copy of the password
file made at startup, so editing `~/.vnc/passwd` alone does not affect the live session.
(VNC auth uses at most 8 characters.)

## From-scratch setup

Run `scripts/setup_vnc.sh` on a fresh VPS (as user ubuntu). It installs the packages,
the xstartup, the systemd unit, sets the password, enables `vncserver@1`, and sets up the
tailnet `serve` forward on 15901. It is safe to re-run.

## Files captured in this repo

- `systemd/vncserver@.service` - the systemd template unit (custom; runs as `User=ubuntu`,
  `-localhost yes`, 1920x1080, with stale-lock cleanup in `ExecStartPre`). Kept under
  TigerVNC's conventional `vncserver@.service` name so `systemctl enable vncserver@1`
  works as expected, rather than renamed to the `claude-soma-*` convention.
- `config/vnc/xstartup` - launches the XFCE session under a fresh dbus.
- `scripts/setup_vnc.sh` - reproducible installer.

Note: `/etc/tigervnc/vncserver.users` is intentionally unused - the unit hardcodes
`User=ubuntu` instead of relying on the display-to-user mapping.
