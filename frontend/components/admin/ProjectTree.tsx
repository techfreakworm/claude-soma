"use client";
import { useMemo } from "react";
import ReactFlow, { Background, Controls, Edge, Node } from "reactflow";
import "reactflow/dist/style.css";

type TeamMember = { handle: string; role: string; status: string };
type Project = {
  name: string; agent_id: string; type: string; rc_url: string;
  status: string; idle_for_seconds: number; team?: TeamMember[];
};

const STATUS_COLOR: Record<string, string> = {
  active: "#34d399", idle: "#fbbf24", error: "#f43f5e", killed: "#64748b",
};

function statusOf(p: Project): string {
  // /api/projects -> list_projects_impl -> _reconcile_active() already filters to
  // genuinely-running leads (is_lead_alive: tmux + systemd), so any project shown
  // here is really active. The old `idle_for_seconds > 3600 -> "idle"` heuristic
  // was misleading: idle_for measures time since the orchestrator last MESSAGED
  // the lead, not the lead's real work, so an actively-working lead read "idle"
  // after an hour. Reflect the real reconciled status instead.
  return p.status;
}

export function ProjectTree({ projects }: { projects: Project[] }) {
  const { nodes, edges } = useMemo(() => {
    const n: Node[] = [
      {
        id: "orchestrator",
        position: { x: 0, y: 0 },
        data: { label: "telegram orchestrator" },
        style: {
          background: "#1e293b", color: "#f1f5f9",
          border: "2px solid #6366f1", padding: 12, borderRadius: 8,
          fontFamily: "monospace", fontSize: 12,
        },
      },
    ];
    const e: Edge[] = [];
    projects.forEach((p, idx) => {
      const status = statusOf(p);
      const color = STATUS_COLOR[status] || "#94a3b8";
      const px = (idx - (projects.length - 1) / 2) * 220;
      n.push({
        id: p.name,
        position: { x: px, y: 200 },
        data: {
          label: (
            <div className="text-left">
              <div className="font-mono text-sm">{p.name}</div>
              <div className="text-xs text-slate-400">{p.type}</div>
              <div className="text-xs" style={{ color }}>● {status}</div>
              {p.rc_url && (
                <a href={p.rc_url} target="_blank" rel="noreferrer"
                   className="text-xs underline text-indigo-300">attach</a>
              )}
            </div>
          ),
        },
        style: {
          background: "#0f172a", color: "#e2e8f0",
          border: `2px solid ${color}`, padding: 10, borderRadius: 8,
          width: 200,
        },
      });
      e.push({
        id: `orch-${p.name}`, source: "orchestrator", target: p.name,
        style: { stroke: color, strokeWidth: 1.5 }, animated: status === "active",
      });

      // Third tier: the lead's agent-team teammates (live, from its tmux panes).
      const team = p.team ?? [];
      team.forEach((tm, tIdx) => {
        const tColor = tm.status === "dead" ? "#64748b" : "#34d399";
        const tid = `${p.name}::${tm.handle}`;
        n.push({
          id: tid,
          position: { x: px + (tIdx - (team.length - 1) / 2) * 150, y: 400 },
          data: {
            label: (
              <div className="text-left">
                <div className="font-mono text-xs">{tm.handle}</div>
                <div className="text-[10px] text-slate-400 truncate" style={{ maxWidth: 130 }}>
                  {tm.role}
                </div>
              </div>
            ),
          },
          style: {
            background: "#0b1220", color: "#cbd5e1",
            border: `1px solid ${tColor}`, padding: 6, borderRadius: 6, width: 150,
          },
        });
        e.push({
          id: `${p.name}-${tid}`, source: p.name, target: tid,
          style: { stroke: tColor, strokeWidth: 1 }, animated: tm.status !== "dead",
        });
      });
    });
    return { nodes: n, edges: e };
  }, [projects]);

  return (
    <div className="h-[600px] rounded-lg border border-slate-800 bg-slate-900/40">
      <ReactFlow nodes={nodes} edges={edges} fitView>
        <Background gap={24} color="#1e293b" />
        <Controls className="!bg-slate-800 !border-slate-700" />
      </ReactFlow>
    </div>
  );
}
