#!/usr/bin/env bash
# P2 catalog: the full hook + event list of opencode 1.18.8 from the authoritative type definitions
# (@opencode-ai/plugin@1.18.8 and @opencode-ai/sdk@1.18.8, fetched into the sandbox by fetch-types.sh).
set -u
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/P2-hooks/catalog.sh"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROBE_ID=P2-types; source "$HERE/../sandbox.sh"
NPM="$SANDBOX/npm"
[ -f "$NPM/plugin/package/dist/index.d.ts" ] || bash "$HERE/fetch-types.sh" >/dev/null
PL="$NPM/plugin/package/dist"
SD="$NPM/sdk/package/dist"
ev_open P2-hooks/hook-catalog.log "hook and event catalog of 1.18.8 from @opencode-ai/plugin + @opencode-ai/sdk type definitions" "none"
ev "## 1. package versions"
ev_run sh -c "jq -r '.name+\"@\"+.version' '$NPM/plugin/package/package.json' '$NPM/sdk/package/package.json'"
ev "## 2. v1 server plugin hooks: interface Hooks (plugin/dist/index.d.ts), verbatim"
ev_run sh -c "awk '/^export interface Hooks \\{/,/^\\}/' '$PL/index.d.ts'"
ev "## 2b. hook names only"
ev_run sh -c "awk '/^export interface Hooks \\{/,/^\\}/' '$PL/index.d.ts' | grep -oE '^    (\"[a-z.]+\"|[a-z]+)\\??:' | tr -d ' \"?:'"
ev "## 2c. PluginInput (what a plugin factory receives) and Plugin/PluginModule"
ev_run sh -c "awk '/^export type PluginInput = \\{/,/^\\};/' '$PL/index.d.ts'; grep -E '^export type (Plugin|PluginModule) ' -A4 '$PL/index.d.ts'"
ev "## 2d. custom tool definition (plugin/dist/tool.d.ts)"
ev_run cat "$PL/tool.d.ts"
ev "## 3. v2 plugin API (plugin/dist/v2/promise): domains on PluginContext (no tool-execution hooks)"
ev_run cat "$PL/v2/promise/context.d.ts"
ev "## 4. v1 Event union (sdk/dist/gen/types.gen.d.ts) -> event type strings"
ev_run python3.12 -I -c '
import re,sys
s=open(sys.argv[1]).read()
names=[x.strip() for x in re.search(r"export type Event = ([^;]+);",s).group(1).split("|")]
out=[]
for n in names:
    i=s.find("export type "+n+" = "); m=re.search(r"type: \"([^\"]+)\"", s[i:i+600])
    out.append(m.group(1) if m else n)
print(len(out),"v1 event types:"); print("\n".join(out))' "$SD/gen/types.gen.d.ts"
ev "## 5. v2 Event union (sdk/dist/v2/gen/types.gen.d.ts) -> event type strings"
ev_run python3.12 -I -c '
import re,sys
s=open(sys.argv[1]).read()
names=[x.strip() for x in re.search(r"export type Event = ([^;]+);",s).group(1).split("|")]
out=[]
for n in names:
    i=s.find("export type "+n+" = "); m=re.search(r"type: \"([^\"]+)\"", s[i:i+600])
    out.append(m.group(1) if m else n)
print(len(out),"v2 event types:"); print("\n".join(out))' "$SD/v2/gen/types.gen.d.ts"
ev "## 6. types a gate/logger needs: Permission (input of permission.ask), Session (parentID), AssistantMessage"
ev_run sh -c "awk '/^export type Permission = \\{/,/^\\};/' '$SD/gen/types.gen.d.ts'"
ev_run sh -c "awk '/^export type Session = \\{/,/^\\};/' '$SD/gen/types.gen.d.ts'"
ev_run sh -c "awk '/^export type AssistantMessage = \\{/,/^\\};/' '$SD/gen/types.gen.d.ts'"
ev_run sh -c "awk '/^export type ToolPart = \\{/,/^\\};/' '$SD/gen/types.gen.d.ts'; awk '/^export type ToolStateCompleted = \\{/,/^\\};/' '$SD/gen/types.gen.d.ts'"
n_hooks=$(awk '/^export interface Hooks \{/,/^\}/' "$PL/index.d.ts" | grep -cE '^    ("[a-z.]+"|[a-z]+)\??:')
if [ "$n_hooks" -ge 15 ]; then
  ev_verdict PASS "1.18.8 exposes $n_hooks v1 Hooks keys (incl. tool.execute.before/after, chat.message, chat.params, permission.ask, shell.env, experimental.chat.system.transform), 32 v1 / 89 v2 event types."
else
  ev_verdict FAIL "could not extract the Hooks interface ($n_hooks keys)."
fi
tail -1 "$EV_LOG"
