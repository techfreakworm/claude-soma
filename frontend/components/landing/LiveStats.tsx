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
    stats = {
      messages_today: 0,
      active_projects: 0,
      decisions_today: 0,
      uptime_hours: 0,
    };
  }
  const cells = [
    { label: "Messages today", value: stats.messages_today },
    { label: "Active project-leads", value: stats.active_projects },
    { label: "Agent decisions today", value: stats.decisions_today },
    { label: "Uptime today (hrs)", value: stats.uptime_hours },
  ];
  return (
    <section
      aria-label="Live system statistics"
      className="border-b border-slate-800/60 bg-slate-900/40"
    >
      <div className="container mx-auto max-w-6xl px-5 py-10 sm:px-6 sm:py-12">
        <div className="mb-6 flex items-center gap-2">
          <span className="relative flex size-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
            <span className="relative inline-flex size-2 rounded-full bg-emerald-400" />
          </span>
          <h2 className="text-xs font-semibold tracking-wider text-slate-400 uppercase">
            Live from the running system
          </h2>
        </div>
        <dl className="grid grid-cols-2 gap-4 sm:gap-6 md:grid-cols-4">
          {cells.map((c) => (
            <div
              key={c.label}
              className="rounded-xl border border-slate-800 bg-slate-950/40 p-4 sm:p-5"
            >
              <dd className="font-mono text-3xl font-bold text-slate-100 sm:text-4xl">
                {c.value.toLocaleString()}
              </dd>
              <dt className="mt-1 text-xs tracking-wider text-slate-500 uppercase">
                {c.label}
              </dt>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}
