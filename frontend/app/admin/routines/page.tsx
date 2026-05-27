import { api } from "@/lib/api";
import { RunRoutineButton } from "@/components/admin/RunRoutineButton";

type Routine = {
  name: string;
  kind: string;             // "cloud" | "local"
  schedule: string;
  target_skill?: string | null;
  description?: string | null;
  last_run?: number | null; // unix seconds (from systemctl / RemoteTrigger)
  next_run?: number | null;
  created_by?: string;      // "user" | "bot" | "system" | "cloud"
};

const IST_FMT = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  year: "numeric",
  month: "short",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

function fmtIST(ts: number | null | undefined): string {
  if (!ts) return "—";
  return IST_FMT.format(new Date(ts * 1000)) + " IST";
}

const KIND_STYLE: Record<string, string> = {
  cloud: "bg-indigo-900/40 text-indigo-300",
  local: "bg-emerald-900/40 text-emerald-300",
};

const ORIGIN_STYLE: Record<string, string> = {
  user: "text-amber-300",
  bot: "text-indigo-300",
  system: "text-slate-400",
  cloud: "text-sky-300",
};

export default async function RoutinesPage() {
  const routines = await api<Routine[]>("/api/routines").catch(() => []);
  return (
    <div className="p-4 sm:p-6 lg:p-8 space-y-6">
      <h1 className="text-2xl font-bold">Routines</h1>
      <p className="text-sm text-slate-500">
        Create routines from Telegram: &ldquo;schedule a morning brief every weekday at 9am.&rdquo;
        Times below are <span className="text-slate-300">Asia/Kolkata (IST)</span>.
      </p>
      <div className="rounded-lg border border-slate-800 bg-slate-900/40">
        {routines.length === 0 ? (
          <div className="p-8 text-slate-500 text-sm">No routines.</div>
        ) : (
          <ul className="divide-y divide-slate-800">
            {routines.map((r) => (
              <li key={r.name} className="p-4 space-y-2">
                <div className="flex items-center flex-wrap gap-2">
                  <div className="font-mono text-sm text-slate-100">{r.name}</div>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded ${KIND_STYLE[r.kind] || "bg-slate-800 text-slate-400"}`}>
                    {r.kind}
                  </span>
                  <span className={`text-[10px] ${ORIGIN_STYLE[r.created_by || ""] || "text-slate-500"}`}>
                    by {r.created_by || "?"}
                  </span>
                </div>
                {r.description && (
                  <div className="text-xs text-slate-400">{r.description}</div>
                )}
                <div className="text-xs text-slate-500 flex flex-wrap gap-x-3 gap-y-1 font-mono">
                  <span>
                    schedule: <code className="text-indigo-300">{r.schedule || "—"}</code>
                  </span>
                  <span className="text-slate-600 hidden sm:inline">·</span>
                  <span>
                    next: <span className="text-slate-300">{fmtIST(r.next_run)}</span>
                  </span>
                  <span className="text-slate-600 hidden sm:inline">·</span>
                  <span>
                    last: <span className="text-slate-400">{fmtIST(r.last_run)}</span>
                  </span>
                </div>
                <div className="pt-1">
                  <RunRoutineButton name={r.name} kind={r.kind} />
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
