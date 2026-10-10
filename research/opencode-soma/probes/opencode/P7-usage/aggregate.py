"""Rebuild a claude-soma-style daily usage table from opencode's own data.

Input: JSON rows from
  opencode db --format json "SELECT m.id, m.session_id, m.time_created, m.data, s.parent_id, s.agent AS s_agent
                             FROM message m JOIN session s ON s.id = m.session_id"
(read from the file given as argv[1]). Output: a per-day / per-class / per-model report on stdout
and the same rows written into argv[2] (sqlite) as table daily_usage, the token analog of
claude-soma's daily_snapshots (interactive vs agent buckets, used vs ceiling).
Class: "interactive" = top-level sessions of the orchestrator-facing agents, "agent" = subagent
sessions (parent_id set) and lead agents; ceilings come from argv[3] JSON if given.
"""

from __future__ import annotations

import collections
import datetime as dt
import json
import sqlite3
import sys


def main() -> None:
    rows = json.load(open(sys.argv[1]))
    ceilings = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    agg: dict[tuple[str, str, str], collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for r in rows:
        d = json.loads(r["data"])
        if d.get("role") != "assistant":
            continue
        day = dt.datetime.fromtimestamp(int(r["time_created"]) / 1000, dt.timezone.utc).date().isoformat()
        agent = d.get("agent") or d.get("mode") or "?"
        klass = "agent" if (r.get("parent_id") or agent.startswith("lead")) else "interactive"
        model = f'{d.get("providerID")}/{d.get("modelID")}'
        t = d.get("tokens") or {}
        c = agg[(day, klass, model)]
        c["messages"] += 1
        c["input"] += int(t.get("input") or 0)
        c["output"] += int(t.get("output") or 0)
        c["reasoning"] += int(t.get("reasoning") or 0)
        c["cache_read"] += int((t.get("cache") or {}).get("read") or 0)
        c["cache_write"] += int((t.get("cache") or {}).get("write") or 0)
        c["cost_micro_usd"] += int(round(float(d.get("cost") or 0) * 1_000_000))
        c["agents:" + agent] += 1
    db = sqlite3.connect(sys.argv[2])
    db.execute(
        "CREATE TABLE IF NOT EXISTS daily_usage(date TEXT, class TEXT, model TEXT, messages INT,"
        " input INT, output INT, reasoning INT, cache_read INT, cache_write INT, cost_usd REAL,"
        " tokens_total INT, ceiling_tokens INT, used_pct REAL, PRIMARY KEY(date, class, model))"
    )
    print(f"{'date':10} {'class':11} {'model':28} {'msgs':>4} {'input':>8} {'output':>7} {'reason':>6} {'c.read':>7} {'cost$':>6} {'used%':>6}  agents")
    for (day, klass, model), c in sorted(agg.items()):
        total = c["input"] + c["output"] + c["reasoning"] + c["cache_read"]
        ceil = int(ceilings.get(klass, 0))
        pct = round(100.0 * total / ceil, 1) if ceil else None
        db.execute(
            "INSERT OR REPLACE INTO daily_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (day, klass, model, c["messages"], c["input"], c["output"], c["reasoning"], c["cache_read"],
             c["cache_write"], c["cost_micro_usd"] / 1e6, total, ceil, pct),
        )
        agents = ",".join(f"{k[7:]}:{v}" for k, v in c.items() if k.startswith("agents:"))
        print(f"{day:10} {klass:11} {model:28} {c['messages']:>4} {c['input']:>8} {c['output']:>7} {c['reasoning']:>6} {c['cache_read']:>7} {c['cost_micro_usd']/1e6:>6.2f} {str(pct):>6}  {agents}")
    db.commit()
    n = db.execute("SELECT count(*) FROM daily_usage").fetchone()[0]
    print(f"daily_usage rows written: {n}")


if __name__ == "__main__":
    main()
