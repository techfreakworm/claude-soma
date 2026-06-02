import Link from "next/link";
import { Upload } from "lucide-react";
import { api } from "@/lib/api";

type Project = {
  name: string;
  type: string;
  status: string;
};

function statusClass(status: string): string {
  switch (status) {
    case "active": return "text-emerald-400";
    case "idle": return "text-amber-400";
    case "error": return "text-rose-500";
    default: return "text-slate-500";
  }
}

export default async function UploadPickerPage() {
  const projects = await api<Project[]>("/api/projects").catch(() => [] as Project[]);

  return (
    <div className="p-4 sm:p-6 lg:p-8 space-y-6">
      <div className="space-y-1">
        <h1 className="text-xl sm:text-2xl font-bold font-mono flex items-center gap-2">
          <Upload className="size-5 text-indigo-300" />
          Upload File
        </h1>
        <p className="text-sm text-slate-400">
          Select a project lead to upload files to.
        </p>
      </div>

      {projects.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-700 p-8 text-slate-500 text-sm">
          No active project leads to upload to. Ask the bot to spawn one from Telegram.
        </div>
      ) : (
        <ul className="space-y-2 max-w-xl">
          {projects.map((p) => (
            <li key={p.name}>
              <Link
                href={`/admin/${encodeURIComponent(p.name)}/upload`}
                className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-3 hover:bg-slate-800/60 hover:border-slate-700 transition-colors group"
              >
                <div className="flex flex-col gap-0.5 min-w-0">
                  <span className="font-mono text-sm text-slate-100 truncate">
                    {p.name}
                  </span>
                  <span className="text-xs text-slate-500">{p.type}</span>
                </div>
                <div className="flex items-center gap-3 shrink-0 ml-4">
                  <span className={`text-xs font-medium ${statusClass(p.status)}`}>
                    &#9679; {p.status}
                  </span>
                  <Upload className="size-4 text-slate-600 group-hover:text-indigo-400 transition-colors" />
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
