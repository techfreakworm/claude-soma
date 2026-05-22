import { api } from "@/lib/api";

type MemoryResp = { project: string; text: string };

export default async function MemoryPage({
  searchParams,
}: { searchParams: Promise<{ project?: string }> }) {
  const sp = await searchParams;
  const project = sp.project || "default";
  const mem = await api<MemoryResp>(`/api/memory/${project}`).catch(() => ({
    project, text: "",
  }));
  return (
    <div className="p-8 space-y-6">
      <h1 className="text-2xl font-bold">Memory</h1>
      <p className="text-sm text-slate-500">
        Read-only view in V1. Edit via Telegram or `/memory` slash command in a
        Claude session. Use ?project=&lt;slug&gt; to switch.
      </p>
      <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-6">
        <div className="text-xs uppercase tracking-wider text-slate-500 mb-3">
          {mem.project}
        </div>
        <pre className="text-sm font-mono text-slate-300 whitespace-pre-wrap">
          {mem.text || "(empty)"}
        </pre>
      </div>
    </div>
  );
}
