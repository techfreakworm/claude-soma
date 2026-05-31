import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { api } from "@/lib/api";
import { LogViewer } from "@/components/LogViewer";

type LogPage = {
  lines: string[];
  total_bytes: number;
  has_more: boolean;
  start_byte: number;
};

export default async function LeadLogsPage({
  params,
}: {
  params: Promise<{ lead: string }>;
}) {
  const { lead } = await params;

  const data = await api<LogPage>(
    `/api/admin/logs/${encodeURIComponent(lead)}?limit=1000`,
  ).catch(
    (): LogPage => ({ lines: [], total_bytes: 0, has_more: false, start_byte: 0 }),
  );

  return (
    <div className="p-4 sm:p-6 lg:p-8 space-y-6">
      <div className="space-y-1">
        <Link
          href="/admin"
          className="inline-flex items-center gap-1.5 text-sm text-slate-400 hover:text-slate-200"
        >
          <ArrowLeft className="size-4" /> Back to admin
        </Link>
        <h1 className="text-xl sm:text-2xl font-bold font-mono">
          Logs — <span className="text-indigo-300">{lead}</span>
        </h1>
        <p className="text-xs text-slate-500 font-mono">
          /var/log/claude-soma/{lead}.log
        </p>
      </div>

      <LogViewer
        lead={lead}
        initialLines={data.lines}
        initialStartByte={data.start_byte}
        totalBytes={data.total_bytes}
        initialHasMore={data.has_more}
      />
    </div>
  );
}
