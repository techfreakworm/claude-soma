// P2(b) gate a tool call BEFORE it runs (claude-soma scripts/orchestrator_gate.py analog).
// Denies bash commands matching /touch\s+\S*sentinel/ for the PRIMARY agent; subagent sessions
// (session.parentID set) are exempt, mirroring FI-GATE-SUBAGENT-EXEMPT. Throwing blocks the call;
// the error message becomes the tool result the model sees.
import { appendFileSync } from "node:fs";
const DIR = process.env.P2_DIR || "/tmp";
const log = (o) => { try { appendFileSync(DIR + "/gate.jsonl", JSON.stringify({ ts: new Date().toISOString(), ...o }) + "\n"); } catch {} };
const DENY = /touch\s+\S*sentinel/;

export const Gate = async ({ client }) => {
  const parentCache = new Map();
  const parentOf = async (sessionID) => {
    if (parentCache.has(sessionID)) return parentCache.get(sessionID);
    let parent = null;
    try {
      const r = await client.session.get({ path: { id: sessionID } });
      parent = (r && r.data && r.data.parentID) || null;
    } catch (e) { parent = "lookup-failed:" + String(e).slice(0, 80); }
    parentCache.set(sessionID, parent);
    return parent;
  };
  return {
    "tool.execute.before": async (input, output) => {
      const parentID = await parentOf(input.sessionID);
      const isSubagent = !!parentID && !String(parentID).startsWith("lookup-failed");
      const cmd = input.tool === "bash" ? String((output.args && output.args.command) || "") : "";
      const match = input.tool === "bash" && DENY.test(cmd);
      const decision = match ? (isSubagent ? "allow-subagent-exempt" : "deny") : "allow";
      log({ hook: "tool.execute.before", input_fields: Object.keys(input), tool: input.tool, sessionID: input.sessionID, callID: input.callID, parentID, isSubagent, command: cmd, decision });
      if (decision === "deny") {
        throw new Error("BLOCKED by orchestrator gate: creating sentinel files is substantive work; dispatch a subagent via the task tool instead.");
      }
    },
  };
};
