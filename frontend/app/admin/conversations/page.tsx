import { api } from "@/lib/api";

type Thread = {
  thread_id: string; project: string; modified_at: number; size_bytes: number;
};

export default async function ConversationsPage() {
  const threads = await api<Thread[]>("/api/conversations").catch(() => []);
  return (
    <div className="p-8 space-y-6">
      <h1 className="text-2xl font-bold">Conversations</h1>
      <div className="rounded-lg border border-slate-800 bg-slate-900/40">
        {threads.length === 0 ? (
          <div className="p-8 text-slate-500 text-sm">No transcripts yet.</div>
        ) : (
          <ul className="divide-y divide-slate-800">
            {threads.map((t) => (
              <li key={`${t.project}-${t.thread_id}`} className="p-4 hover:bg-slate-800/40">
                <div className="font-mono text-sm">{t.thread_id}</div>
                <div className="text-xs text-slate-500 mt-1">
                  project: {t.project} · {new Date(t.modified_at * 1000).toLocaleString()}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
