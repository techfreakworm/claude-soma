#!/usr/bin/env bash
# scripts/disable-user-telegram-plugin.sh
#
# Telegram poller-hijack ROOT fix (docs/KNOWN_BUGS.md #1): remove the
# telegram@claude-plugins-official entry from user-scope
# ~/.claude/settings.json -> enabledPlugins, so NO session loads the plugin from
# the default (user/project/local) scope chain. The bot re-acquires the plugin
# explicitly via its own launch (scripts/channel-claude.sh passes
# --settings <bot-only settings file>), so it keeps polling while manual shells
# and Agent/Task subagents -- which load the default scopes, not the bot's
# --settings file -- no longer hijack the poller.
#
# Idempotent and reversible: backs up settings.json (timestamped .bak) before
# editing and only touches the one plugin key. Run on the VPS as the ubuntu user
# during the maintenance-window deploy of the root fix; the change takes effect
# when the bot session next (re)starts.
#
# Override the target file with CLAUDE_USER_SETTINGS (used by the tests).
set -euo pipefail

SETTINGS="${CLAUDE_USER_SETTINGS:-$HOME/.claude/settings.json}"
PLUGIN="telegram@claude-plugins-official"

if [ ! -f "$SETTINGS" ]; then
    echo "disable-user-telegram-plugin: no $SETTINGS; nothing to do"
    exit 0
fi

python3 - "$SETTINGS" "$PLUGIN" <<'PY'
import json
import os
import sys
import time

path, plugin = sys.argv[1], sys.argv[2]
with open(path) as f:
    data = json.load(f)

enabled = data.get("enabledPlugins", {})
if plugin not in enabled:
    print(f"disable-user-telegram-plugin: {plugin} already absent; no change")
    sys.exit(0)

backup = f"{path}.bak.{time.strftime('%Y%m%d-%H%M%S')}"
with open(backup, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")

del enabled[plugin]
data["enabledPlugins"] = enabled

tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
os.replace(tmp, path)
print(f"disable-user-telegram-plugin: removed {plugin} from {path} (backup: {backup})")
PY
