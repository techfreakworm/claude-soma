import Link from "next/link";
import { api } from "@/lib/api";

type MemoryResp = { project: string; text: string };
type Project = { name: string };

export default async function MemoryPage({
  searchParams,
}: { searchParams: Promise<{ project?: string }> }) {
  const sp = await searchParams;
  const active = sp.project || "default";

  const [mem, projects] = await Promise.all([
    api<MemoryResp>(`/api/memory/${encodeURIComponent(active)}`).catch(() => ({
      project: active,
      text: "",
    })),
    api<Project[]>("/api/projects").catch(() => [] as Project[]),
  ]);

  // "default" is always available plus every live project slug (de-duped).
  const slugs = Array.from(new Set(["default", ...projects.map((p) => p.name)]));

  return (
    <div className="p-4 sm:p-6 lg:p-8 space-y-6">
      <h1 className="text-2xl font-bold">Memory</h1>
      <p className="text-sm text-slate-500">
        Read-only view. Edit via Telegram or the <code>/memory</code> slash
        command in a Claude session.
      </p>

      <div className="flex flex-wrap gap-2">
        {slugs.map((slug) => {
          const isActive = slug === active;
          return (
            <Link
              key={slug}
              href={`/admin/memory?project=${encodeURIComponent(slug)}`}
              aria-current={isActive ? "true" : undefined}
              className={`px-3 py-1.5 rounded-full text-xs font-mono border transition-colors ${
                isActive
                  ? "border-indigo-500 bg-indigo-500/15 text-indigo-200"
                  : "border-slate-700 text-slate-400 hover:bg-slate-800/60 hover:text-slate-200"
              }`}
            >
              {slug}
            </Link>
          );
        })}
      </div>

      <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-4 sm:p-6">
        <div className="text-xs uppercase tracking-wider text-slate-500 mb-3">
          {mem.project}
        </div>
        <pre className="text-sm font-mono text-slate-300 whitespace-pre-wrap break-words">
          {mem.text || "(empty)"}
        </pre>
      </div>
    </div>
  );
}
