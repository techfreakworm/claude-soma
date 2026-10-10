// P6: can a plugin answer a permission request programmatically?
//  P6_MODE=hook   -> permission.ask hook sets output.status = "allow"
//  P6_MODE=event  -> on bus event permission.asked, reply "once" through the SDK client
//  (any mode)     -> logs every permission.ask call and permission.* event to $P6_DIR/autoapprove.jsonl
import { appendFileSync } from "node:fs";
const DIR = process.env.P6_DIR || "/tmp";
const MODE = process.env.P6_MODE || "off";
const log = (o) => { try { appendFileSync(DIR + "/autoapprove.jsonl", JSON.stringify({ ts: new Date().toISOString(), mode: MODE, ...o }) + "\n"); } catch {} };

export const AutoApprove = async ({ client }) => ({
  "permission.ask": async (input, output) => {
    log({ kind: "permission.ask-hook-fired", input });
    if (MODE === "hook") output.status = "allow";
  },
  event: async ({ event }) => {
    if (!String(event.type).startsWith("permission")) return;
    const p = event.properties || {};
    log({ kind: "event", type: event.type, props: p });
    if (MODE === "event" && event.type === "permission.asked") {
      try {
        const r = await client.postSessionIdPermissionsPermissionId({ path: { id: p.sessionID, permissionID: p.id }, body: { response: "once" } });
        log({ kind: "replied-via-sdk", id: p.id, ok: !r.error, error: r.error ? JSON.stringify(r.error).slice(0, 200) : null });
      } catch (e) {
        log({ kind: "reply-failed", id: p.id, error: String(e).slice(0, 200) });
      }
    }
  },
});
