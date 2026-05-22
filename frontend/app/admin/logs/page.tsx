import { api } from "@/lib/api";

type LogRow = {
  ts: string; tool: string; session: string;
  input_summary?: unknown; result_summary?: string;
};

export default async function LogsPage() {
  const rows = await api<LogRow[]>("/api/logs?limit=200").catch(() => []);
  return (
    <div className="p-8 space-y-6">
      <h1 className="text-2xl font-bold">Logs</h1>
      <p className="text-sm text-slate-500">
        Tool-call activity feed (most recent 200). Filter UI in V1.5.
      </p>
      <div className="rounded-lg border border-slate-800 bg-slate-900/40 overflow-hidden">
        <ul className="divide-y divide-slate-800 font-mono text-xs">
          {rows.length === 0 ? (
            <li className="p-8 text-slate-500">No activity yet.</li>
          ) : (
            rows.slice().reverse().map((r, i) => (
              <li key={`${r.ts}-${i}`} className="px-4 py-2 hover:bg-slate-800/40">
                <span className="text-slate-500">{r.ts.slice(11, 19)}</span>{" "}
                <span className="text-indigo-300">{r.tool}</span>{" "}
                <span className="text-slate-500">·</span>{" "}
                <span className="text-slate-400">{r.session.slice(0, 8)}</span>
                {r.result_summary && (
                  <span className="text-slate-500 ml-2">→ {r.result_summary.slice(0, 100)}</span>
                )}
              </li>
            ))
          )}
        </ul>
      </div>
    </div>
  );
}
