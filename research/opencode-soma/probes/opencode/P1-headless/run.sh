#!/usr/bin/env bash
# P1 headless: runs the four parts in sequence (each has its own sandbox, ports and evidence log).
D="$(cd "$(dirname "$0")" && pwd)"
for s in session crash-run crash-serve busy; do bash "$D/$s.sh"; done
