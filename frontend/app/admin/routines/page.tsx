import { api } from "@/lib/api";

type Routine = {
  id?: string; name?: string; cron?: string; next_run?: string;
};

export default async function RoutinesPage() {
  const routines = await api<Routine[]>("/api/routines").catch(() => []);
  return (
    <div className="p-8 space-y-6">
      <h1 className="text-2xl font-bold">Routines</h1>
      <p className="text-sm text-slate-500">
        Create routines from Telegram: &ldquo;schedule a morning brief every weekday at 9am.&rdquo;
      </p>
      <div className="rounded-lg border border-slate-800 bg-slate-900/40">
        {routines.length === 0 ? (
          <div className="p-8 text-slate-500 text-sm">No routines.</div>
        ) : (
          <ul className="divide-y divide-slate-800">
            {routines.map((r) => (
              <li key={r.id || r.name} className="p-4">
                <div className="font-mono text-sm">{r.name || r.id}</div>
                <div className="text-xs text-slate-500 mt-1">
                  cron: <code className="text-indigo-300">{r.cron}</code>
                  {r.next_run && <> · next: {r.next_run}</>}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
