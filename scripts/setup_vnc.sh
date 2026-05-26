#!/usr/bin/env bash
#
# Reproduce the Claude Soma VNC desktop used for human-in-the-loop platform auth
# (interactive browser logins that the Playwright social-automation skills reuse).
# Run on the OCI VPS as user ubuntu; uses sudo for system steps. Safe to re-run.
#
# See docs/vnc-setup.md for the full rationale, the security model, and the
# port-5901 gotcha that this script deliberately avoids.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VNC_USER="${VNC_USER:-ubuntu}"
VNC_HOME="/home/${VNC_USER}"
DISPLAY_NUM="${DISPLAY_NUM:-1}"
RFB_PORT="$((5900 + DISPLAY_NUM))"            # local port Xtigervnc binds (localhost only)
SERVE_PORT="${SERVE_PORT:-15901}"             # tailnet port; see WARNING below

echo "[setup_vnc] installing packages..."
sudo apt-get update -y
sudo apt-get install -y xfce4 tigervnc-standalone-server tigervnc-common tigervnc-tools dbus-x11

echo "[setup_vnc] installing xstartup..."
install -d -m 700 "${VNC_HOME}/.vnc"
install -m 755 "${REPO_ROOT}/config/vnc/xstartup" "${VNC_HOME}/.vnc/xstartup"

echo "[setup_vnc] setting VNC password (interactive, only if not already set)..."
if [ ! -s "${VNC_HOME}/.vnc/passwd" ]; then
  vncpasswd "${VNC_HOME}/.vnc/passwd"
  chmod 600 "${VNC_HOME}/.vnc/passwd"
else
  echo "[setup_vnc]   ${VNC_HOME}/.vnc/passwd already present; leaving it."
fi

echo "[setup_vnc] installing systemd unit and enabling vncserver@${DISPLAY_NUM}..."
sudo install -m 644 "${REPO_ROOT}/systemd/vncserver@.service" /etc/systemd/system/vncserver@.service
sudo systemctl daemon-reload
sudo systemctl enable --now "vncserver@${DISPLAY_NUM}.service"

# WARNING: do NOT set SERVE_PORT to 5901 (or any 590x that maps to an active display).
# TigerVNC's vncserver pre-start check (checkTCPPortUsed in TigerVNC/Wrapper.pm) refuses
# to start if the rfbport is already bound. A `tailscale serve` listener on 5901 trips
# that check, so vncserver@1 crash-loops on every restart/reboot. Forward a non-590x
# tailnet port to the localhost rfbport instead.
echo "[setup_vnc] exposing display :${DISPLAY_NUM} to the tailnet on port ${SERVE_PORT}..."
sudo tailscale serve --bg --tcp "${SERVE_PORT}" "tcp://localhost:${RFB_PORT}"

TS_IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"
echo "[setup_vnc] done."
echo "[setup_vnc] connect a VNC client to ${TS_IP:-<tailnet-ip>}:${SERVE_PORT} (display :${DISPLAY_NUM})."
echo "[setup_vnc] undo the tailnet exposure with: sudo tailscale serve --tcp=${SERVE_PORT} off"
