import { publicApi } from "@/lib/api";

type Stats = {
  messages_today: number;
  active_projects: number;
  decisions_today: number;
  uptime_hours: number;
};

export async function LiveStats() {
  let stats: Stats;
  try {
    stats = await publicApi<Stats>("/api/public/stats");
  } catch {
    stats = { messages_today: 0, active_projects: 0,
              decisions_today: 0, uptime_hours: 0 };
  }
  const cells = [
    { label: "Messages today", value: stats.messages_today },
    { label: "Active project-leads", value: stats.active_projects },
    { label: "Agent decisions today", value: stats.decisions_today },
    { label: "Uptime today (hrs)", value: stats.uptime_hours },
  ];
  return (
    <section className="border-t border-b border-slate-800/60 bg-slate-900/40">
      <div className="container mx-auto px-6 py-12 max-w-5xl grid grid-cols-2 md:grid-cols-4 gap-6">
        {cells.map((c) => (
          <div key={c.label} className="">
            <div className="text-3xl md:text-4xl font-mono font-bold">
              {c.value.toLocaleString()}
            </div>
            <div className="text-xs uppercase tracking-wider text-slate-500 mt-1">
              {c.label}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
