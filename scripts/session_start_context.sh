#!/usr/bin/env bash
# scripts/session_start_context.sh
#
# Injected into Claude's context at session start.
# Lists active projects + recent ~/Projects/llm/* commit activity.

set -euo pipefail

REGISTRY="/opt/claude-soma/registry.sqlite"
PROJECTS_DIR="${HOME}/Projects/llm"

projects_block=""
if [[ -f "$REGISTRY" ]]; then
    projects_block="$(sqlite3 "$REGISTRY" "SELECT name || ' (' || type || ', ' || status || ')' FROM projects WHERE status != 'killed' ORDER BY last_activity DESC LIMIT 10;" 2>/dev/null || echo "(none)")"
fi

recent_block=""
if [[ -d "$PROJECTS_DIR" ]]; then
    recent_block="$(find "$PROJECTS_DIR" -maxdepth 2 -name ".git" -type d 2>/dev/null | head -8 | while read gitdir; do
        proj="$(basename "$(dirname "$gitdir")")"
        last="$(cd "$(dirname "$gitdir")" && git log -1 --format='%cr: %s' 2>/dev/null || echo 'no commits')"
        echo "- $proj — $last"
    done)"
fi

cat <<EOF
## Claude Soma session context (auto-injected)

**Active projects (from orchestrator registry):**
${projects_block:-(no active projects)}

**Recent activity in ~/Projects/llm/*:**
${recent_block:-(none)}
EOF
