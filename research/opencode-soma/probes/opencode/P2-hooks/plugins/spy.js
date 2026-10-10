// P2 spy: log every v1 hook invocation and every bus event to $P2_DIR/spy.jsonl
import { appendFileSync } from "node:fs";
const LOG = (process.env.P2_DIR || "/tmp") + "/spy.jsonl";
const cut = (v, n = 500) => { try { const s = JSON.stringify(v); return s && s.length > n ? s.slice(0, n) + "..." : s; } catch { return String(v); } };
const log = (o) => { try { appendFileSync(LOG, JSON.stringify({ ts: new Date().toISOString(), ...o }) + "\n"); } catch {} };

export const Spy = async ({ project, directory, worktree, serverUrl }) => {
  log({ kind: "init", directory, worktree, project: project && project.id, serverUrl: String(serverUrl) });
  const hook = (name) => async (input, output) => {
    log({ kind: "hook", hook: name, input_keys: input ? Object.keys(input) : [], input: cut(input, 700), output_keys: output ? Object.keys(output) : [], output: cut(output, 300) });
  };
  return {
    event: async ({ event }) => log({ kind: "event", type: event.type, props: cut(event.properties, 300) }),
    config: async (cfg) => log({ kind: "hook", hook: "config", input_keys: Object.keys(cfg || {}) }),
    "chat.message": hook("chat.message"),
    "chat.params": async (input, output) => log({ kind: "hook", hook: "chat.params", input_keys: Object.keys(input), sessionID: input.sessionID, agent: input.agent, model: input.model && input.model.id, output: cut(output, 300) }),
    "chat.headers": async (input, output) => log({ kind: "hook", hook: "chat.headers", sessionID: input.sessionID, agent: input.agent, output: cut(output, 200) }),
    "permission.ask": hook("permission.ask"),
    "command.execute.before": hook("command.execute.before"),
    "tool.execute.before": hook("tool.execute.before"),
    "shell.env": hook("shell.env"),
    "tool.execute.after": hook("tool.execute.after"),
    "experimental.chat.messages.transform": async (input, output) => log({ kind: "hook", hook: "experimental.chat.messages.transform", input: cut(input, 200), n_messages: output.messages.length }),
    "experimental.chat.system.transform": async (input, output) => log({ kind: "hook", hook: "experimental.chat.system.transform", input_keys: Object.keys(input), sessionID: input.sessionID, n_system: output.system.length, system_chars: output.system.join("").length }),
    "experimental.session.compacting": hook("experimental.session.compacting"),
    "experimental.compaction.autocontinue": hook("experimental.compaction.autocontinue"),
    "experimental.text.complete": hook("experimental.text.complete"),
    "tool.definition": async (input, output) => log({ kind: "hook", hook: "tool.definition", toolID: input.toolID, desc_chars: (output.description || "").length }),
  };
};
