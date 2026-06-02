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
  interactive: { today: number; ceiling: number; remaining_pct: number; configured: boolean };
  agent_sdk: { today: number; ceiling: number; remaining_pct: number; configured: boolean };
  trend: Snapshot[];
};

export default async function UsagePage() {
  const usage = await api<Usage>("/api/usage").catch(() => ({
    interactive: { today: 0, ceiling: 0, remaining_pct: 100, configured: false },
    agent_sdk: { today: 0, ceiling: 0, remaining_pct: 100, configured: false },
    trend: [] as Snapshot[],
  }));
  const ceilingsConfigured = usage.interactive.configured && usage.agent_sdk.configured;
  return (
    <div className="p-4 sm:p-6 lg:p-8 space-y-6">
      <h1 className="text-2xl font-bold">Usage</h1>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
        <KpiCard label="Interactive used today"
                 value={Math.round(usage.interactive.today)}
                 hint={`${Math.round(usage.interactive.remaining_pct)}% left`} />
        <KpiCard label="Agent SDK used today"
                 value={Math.round(usage.agent_sdk.today)}
                 hint={`${Math.round(usage.agent_sdk.remaining_pct)}% left`} />
        {usage.interactive.configured && (
          <KpiCard label="Interactive ceiling"
                   value={Math.round(usage.interactive.ceiling)} />
        )}
        {usage.agent_sdk.configured && (
          <KpiCard label="Agent SDK ceiling"
                   value={Math.round(usage.agent_sdk.ceiling)} />
        )}
      </div>
      {!ceilingsConfigured && (
        <p className="text-xs text-slate-400">
          To show plan ceilings, set HERMES_INTERACTIVE_CEILING and HERMES_AGENT_SDK_CEILING in /etc/claude-soma/secrets.env then restart claude-soma-api.service.
        </p>
      )}
      <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-4 sm:p-6">
        <h2 className="text-xs uppercase tracking-wider text-slate-500 mb-4">
          30-day trend
        </h2>
        <UsageChart trend={usage.trend} />
      </div>
    </div>
  );
}
