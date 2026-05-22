import { api } from "@/lib/api";
import { KpiCard } from "@/components/admin/KpiCard";
import { UsageChart } from "@/components/admin/UsageChart";

type Snapshot = {
  date: string;
  interactive_credits_used: number;
  interactive_ceiling: number;
  agent_sdk_credits_used: number;
  agent_sdk_ceiling: number;
};

type Usage = {
  interactive: { today: number; ceiling: number; remaining_pct: number };
  agent_sdk: { today: number; ceiling: number; remaining_pct: number };
  trend: Snapshot[];
};

export default async function UsagePage() {
  const usage = await api<Usage>("/api/usage").catch(() => ({
    interactive: { today: 0, ceiling: 0, remaining_pct: 100 },
    agent_sdk: { today: 0, ceiling: 0, remaining_pct: 100 },
    trend: [] as Snapshot[],
  }));
  return (
    <div className="p-8 space-y-6">
      <h1 className="text-2xl font-bold">Usage</h1>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KpiCard label="Interactive used today"
                 value={Math.round(usage.interactive.today)}
                 hint={`${Math.round(usage.interactive.remaining_pct)}% left`} />
        <KpiCard label="Agent SDK used today"
                 value={Math.round(usage.agent_sdk.today)}
                 hint={`${Math.round(usage.agent_sdk.remaining_pct)}% left`} />
        <KpiCard label="Interactive ceiling"
                 value={Math.round(usage.interactive.ceiling)} />
        <KpiCard label="Agent SDK ceiling"
                 value={Math.round(usage.agent_sdk.ceiling)} />
      </div>
      <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-6">
        <h2 className="text-xs uppercase tracking-wider text-slate-500 mb-4">
          30-day trend
        </h2>
        <UsageChart trend={usage.trend} />
      </div>
    </div>
  );
}
