import Link from "next/link";
import { ChevronRight } from "lucide-react";
import { api } from "@/lib/api";

type Thread = {
  thread_id: string; project: string; modified_at: number; size_bytes: number;
};

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export default async function ConversationsPage() {
  const threads = await api<Thread[]>("/api/conversations").catch(() => []);
  return (
    <div className="p-4 sm:p-6 lg:p-8 space-y-6">
      <h1 className="text-2xl font-bold">Conversations</h1>
      <p className="text-sm text-slate-500">
        Tap a transcript to read the full message thread.
      </p>
      <div className="rounded-lg border border-slate-800 bg-slate-900/40">
        {threads.length === 0 ? (
          <div className="p-8 text-slate-500 text-sm">No transcripts yet.</div>
        ) : (
          <ul className="divide-y divide-slate-800">
            {threads.map((t) => (
              <li key={`${t.project}-${t.thread_id}`}>
                <Link
                  href={`/admin/conversations/${encodeURIComponent(t.thread_id)}?project=${encodeURIComponent(t.project)}`}
                  className="flex items-center justify-between gap-3 p-4 hover:bg-slate-800/40 transition-colors"
                >
                  <div className="min-w-0">
                    <div className="font-mono text-sm break-all">{t.thread_id}</div>
                    <div className="text-xs text-slate-500 mt-1">
                      project: {t.project} · {new Date(t.modified_at * 1000).toLocaleString()} ·{" "}
                      {fmtBytes(t.size_bytes)}
                    </div>
                  </div>
                  <ChevronRight className="size-4 shrink-0 text-slate-600" />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
