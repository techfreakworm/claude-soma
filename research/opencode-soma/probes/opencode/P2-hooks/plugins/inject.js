// P2(a) inject context on EVERY prompt (claude-soma scripts/notify_inject.sh analog).
// chat.message: adds a synthetic text part to each new user message, read from $P2_DIR/inject-msg.txt
// experimental.chat.system.transform: appends $P2_DIR/inject-sys.txt to the system prompt of every LLM call
import { readFileSync, appendFileSync } from "node:fs";
const DIR = process.env.P2_DIR || "/tmp";
const read = (f) => { try { return readFileSync(DIR + "/" + f, "utf8").trim(); } catch { return ""; } };
const log = (o) => { try { appendFileSync(DIR + "/inject.jsonl", JSON.stringify({ ts: new Date().toISOString(), ...o }) + "\n"); } catch {} };

export const Inject = async () => ({
  "chat.message": async (input, output) => {
    const txt = read("inject-msg.txt");
    if (!txt) return;
    const base = output.parts[0] || {};
    output.parts.push({
      id: "prt_" + Date.now().toString(16) + Math.random().toString(16).slice(2, 10),
      sessionID: input.sessionID,
      messageID: (output.message && output.message.id) || base.messageID,
      type: "text",
      synthetic: true,
      text: "[notify-inject] " + txt,
    });
    log({ hook: "chat.message", sessionID: input.sessionID, agent: input.agent, injected: txt });
  },
  "experimental.chat.system.transform": async (input, output) => {
    const txt = read("inject-sys.txt");
    if (!txt) return;
    output.system.push("[notify-inject-system] " + txt);
    log({ hook: "system.transform", sessionID: input.sessionID, injected: txt });
  },
});
