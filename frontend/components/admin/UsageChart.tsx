"use client";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
         ResponsiveContainer, Legend } from "recharts";

type Snapshot = {
  date: string;
  interactive_credits_used: number;
  agent_sdk_credits_used: number;
};

export function UsageChart({ trend }: { trend: Snapshot[] }) {
  const data = [...trend].reverse();
  return (
    <ResponsiveContainer width="100%" height={300}>
      <LineChart data={data} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
        <XAxis dataKey="date" stroke="#64748b" fontSize={11} />
        <YAxis stroke="#64748b" fontSize={11} />
        <Tooltip
          contentStyle={{ background: "#0f172a", border: "1px solid #334155",
                          fontFamily: "monospace", fontSize: 12 }}
        />
        <Legend />
        <Line type="monotone" dataKey="interactive_credits_used"
              name="Interactive" stroke="#6366f1" strokeWidth={2} dot={false} />
        <Line type="monotone" dataKey="agent_sdk_credits_used"
              name="Agent SDK" stroke="#f59e0b" strokeWidth={2} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
