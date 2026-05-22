import { api } from "@/lib/api";
import { KpiCard } from "@/components/admin/KpiCard";
import { ActivityFeed } from "@/components/admin/ActivityFeed";

type Health = { status: string; uptime_seconds: number };
type Project = { name: string };
type Usage = { interactive: { remaining_pct: number } };

export default async function OverviewPage() {
  const [health, projects, usage] = await Promise.all([
    api<Health>("/api/healthz").catch(() => ({ status: "down", uptime_seconds: 0 })),
    api<Project[]>("/api/projects").catch(() => []),
    api<Usage>("/api/usage").catch(() =>
      ({ interactive: { remaining_pct: 100 } } as Usage)),
  ]);

  return (
    <div className="p-8 space-y-8">
      <header className="flex items-baseline justify-between">
        <h1 className="text-2xl font-bold">Overview</h1>
        <span className={`text-xs font-mono ${health.status === "ok" ? "text-emerald-400" : "text-rose-400"}`}>
          ● {health.status} · uptime {Math.round(health.uptime_seconds / 60)}m
        </span>
      </header>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KpiCard label="Active projects" value={projects.length} />
        <KpiCard label="Interactive credit left"
                 value={`${Math.round(usage.interactive.remaining_pct)}%`} />
        <KpiCard label="Uptime today"
                 value={`${Math.round(health.uptime_seconds / 3600)}h`} />
        <KpiCard label="Status" value={health.status} />
      </div>

      <ActivityFeed />
    </div>
  );
}
