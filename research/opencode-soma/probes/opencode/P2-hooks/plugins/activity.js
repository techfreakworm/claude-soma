// P2(c) log EVERY tool use (claude-soma scripts/log_activity.sh analog): one JSONL line per call with
// tool, args, session, parent session, agent, duration and result head. Agent comes from chat.message
// (input.agent) / message.updated events, keyed by sessionID.
import { appendFileSync } from "node:fs";
const DIR = process.env.P2_DIR || "/tmp";
const OUT = DIR + "/activity.jsonl";
const cut = (v, n = 300) => { try { const s = typeof v === "string" ? v : JSON.stringify(v); return s && s.length > n ? s.slice(0, n) + "..." : s; } catch { return String(v); } };

export const Activity = async ({ client }) => {
  const started = new Map();
  const agentOf = new Map();
  const parentOf = new Map();
  const parent = async (sid) => {
    if (parentOf.has(sid)) return parentOf.get(sid);
    let p = null;
    try { const r = await client.session.get({ path: { id: sid } }); p = (r && r.data && r.data.parentID) || null; } catch {}
    parentOf.set(sid, p);
    return p;
  };
  return {
    "chat.message": async (input) => { if (input.agent) agentOf.set(input.sessionID, input.agent); },
    event: async ({ event }) => {
      if (event.type === "message.updated") {
        const info = event.properties && event.properties.info;
        if (info && (info.agent || info.mode)) agentOf.set(info.sessionID, info.agent || info.mode);
      }
      if (event.type === "session.created") {
        const info = event.properties && event.properties.info;
        if (info) parentOf.set(info.id, info.parentID || null);
      }
    },
    "tool.execute.before": async (input, output) => {
      started.set(input.callID, { t: Date.now(), args: output.args });
      // a "start" line too, so calls that a later plugin (the gate) blocks are still on record
      try { appendFileSync(OUT, JSON.stringify({ ts: new Date().toISOString(), phase: "start", tool: input.tool, args: cut(output.args, 300), sessionID: input.sessionID, parentSessionID: await parent(input.sessionID), agent: agentOf.get(input.sessionID) || null, callID: input.callID }) + "\n"); } catch {}
    },
    "tool.execute.after": async (input, output) => {
      const s = started.get(input.callID);
      const line = {
        ts: new Date().toISOString(),
        phase: "end",
        tool: input.tool,
        args: cut(input.args !== undefined ? input.args : s && s.args, 300),
        sessionID: input.sessionID,
        parentSessionID: await parent(input.sessionID),
        agent: agentOf.get(input.sessionID) || null,
        callID: input.callID,
        duration_ms: s ? Date.now() - s.t : null,
        title: output.title,
        result_head: cut(output.output, 200),
        metadata_keys: output.metadata ? Object.keys(output.metadata) : [],
      };
      try { appendFileSync(OUT, JSON.stringify(line) + "\n"); } catch {}
    },
  };
};
