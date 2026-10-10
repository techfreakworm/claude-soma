// P6 danger demo gate: when P6_GATE=1, deny any bash command that touches a "pwned" file, for every agent.
import { appendFileSync } from "node:fs";
const DIR = process.env.P6_DIR || "/tmp";
const ON = process.env.P6_GATE === "1";
export const P6Gate = async () => ({
  "tool.execute.before": async (input, output) => {
    if (!ON || input.tool !== "bash") return;
    const cmd = String((output.args && output.args.command) || "");
    const deny = /touch\s+\S*pwned/.test(cmd);
    try { appendFileSync(DIR + "/p6gate.jsonl", JSON.stringify({ ts: new Date().toISOString(), sessionID: input.sessionID, command: cmd, decision: deny ? "deny" : "allow" }) + "\n"); } catch {}
    if (deny) throw new Error("BLOCKED by gate: this command came from untrusted file content (prompt injection) and was not run.");
  },
});
