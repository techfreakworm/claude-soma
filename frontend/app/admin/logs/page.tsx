import Link from "next/link";
import { api } from "@/lib/api";

type LogRow = {
  ts: string; tool: string; session: string;
  input_summary?: unknown; result_summary?: string;
};

export default async function LogsPage({
  searchParams,
}: {
  searchParams: Promise<{ tool?: string }>;
}) {
  const sp = await searchParams;
  const tool = sp.tool?.trim() || "";

  // Two reads: an unfiltered baseline to build the distinct-tool chip vocabulary,
  // and the (optionally filtered) rows to display. Both tolerate failure.
  const [rows, baseline] = await Promise.all([
    api<LogRow[]>(
      `/api/logs?limit=200${tool ? `&tool=${encodeURIComponent(tool)}` : ""}`,
    ).catch(() => [] as LogRow[]),
    tool
      ? api<LogRow[]>("/api/logs?limit=200").catch(() => [] as LogRow[])
      : Promise.resolve(null),
  ]);

  const vocabSource = baseline ?? rows;
  const tools = Array.from(
    new Set(vocabSource.map((r) => r.tool).filter(Boolean)),
  ).sort();

  const chipBase =
    "px-3 py-1.5 rounded-full text-xs font-mono border transition-colors";
  const chipActive =
    "border-indigo-500 bg-indigo-500/15 text-indigo-200";
  const chipIdle =
    "border-slate-700 text-slate-400 hover:bg-slate-800/60 hover:text-slate-200";

  return (
    <div className="p-4 sm:p-6 lg:p-8 space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-bold">Logs</h1>
        <span className="inline-flex items-center gap-1.5 text-xs text-emerald-400">
          <span className="relative flex size-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
            <span className="relative inline-flex size-2 rounded-full bg-emerald-500" />
          </span>
          live
        </span>
      </div>
      <p className="text-sm text-slate-500">
        Tool-call activity feed (most recent 200). Filter by tool below.
      </p>

      <div className="flex flex-wrap gap-2">
        <Link href="/admin/logs" className={`${chipBase} ${tool ? chipIdle : chipActive}`}>
          all
        </Link>
        {tools.map((t) => (
          <Link
            key={t}
            href={`/admin/logs?tool=${encodeURIComponent(t)}`}
            aria-current={t === tool ? "true" : undefined}
            className={`${chipBase} ${t === tool ? chipActive : chipIdle}`}
          >
            {t}
          </Link>
        ))}
      </div>

      <div className="rounded-lg border border-slate-800 bg-slate-900/40 overflow-hidden">
        <ul className="divide-y divide-slate-800 font-mono text-xs">
          {rows.length === 0 ? (
            <li className="p-8 text-slate-500">
              {tool ? `No activity for tool "${tool}".` : "No activity yet."}
            </li>
          ) : (
            rows.slice().reverse().map((r, i) => (
              <li key={`${r.ts}-${i}`} className="px-4 py-2 hover:bg-slate-800/40 break-words">
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
