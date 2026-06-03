#!/usr/bin/env bash
# scripts/lib-friendly.sh — friendly UX helpers for install scripts.
#
# Sourced by bootstrap.sh + finalize-caddy.sh. Provides:
#   friendly_warn   — print a friendly NON-FATAL box explaining a known
#                     issue + exact remediation, then continue
#   friendly_halt   — print a friendly FATAL box + exit non-zero
#   friendly_section_header — print a step header
#
# Principle: a non-technical user must NEVER see a raw systemd/pnpm/caddy
# error without a plain-language "here is what to do." Wrap every known
# failure mode in friendly_warn or friendly_halt.

if [[ -t 1 ]]; then
    _F_CYAN="\033[36m"; _F_YELLOW="\033[33m"; _F_RED="\033[31m"; _F_BOLD="\033[1m"; _F_RESET="\033[0m"
else
    _F_CYAN=""; _F_YELLOW=""; _F_RED=""; _F_BOLD=""; _F_RESET=""
fi

_friendly_box() {
    local color="$1"; shift
    local title="$1"; shift
    local body="$*"
    printf "%b" "$color"
    echo "================================================================"
    printf "%b%s%b\n" "${_F_BOLD}" "$title" "$color"
    echo "----------------------------------------------------------------"
    printf "%b%s%b\n" "${_F_RESET}" "$body" "$color"
    echo "================================================================"
    printf "%b\n" "${_F_RESET}"
}

friendly_warn() {
    local title="$1"; shift
    _friendly_box "${_F_YELLOW}" "$title" "$@"
}

friendly_halt() {
    local title="$1"; shift
    _friendly_box "${_F_RED}" "$title" "$@"
    exit 1
}

friendly_section_header() {
    printf "\n%b==== %s ====%b\n" "${_F_CYAN}${_F_BOLD}" "$1" "${_F_RESET}"
}
