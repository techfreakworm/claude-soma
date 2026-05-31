import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { FileDropZone } from "@/components/FileDropZone";

export default async function LeadUploadPage({
  params,
}: {
  params: Promise<{ lead: string }>;
}) {
  const { lead } = await params;

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
          Upload — <span className="text-indigo-300">{lead}</span>
        </h1>
        <p className="text-xs text-slate-500">
          Files land at{" "}
          <code className="rounded bg-slate-800 px-1 py-0.5 font-mono">
            /var/lib/claude-soma/staging/{lead}/inbox/
          </code>
        </p>
      </div>

      <div className="max-w-xl">
        <FileDropZone lead={lead} />
      </div>

      <div className="rounded-lg border border-slate-800 bg-slate-900/30 p-4 text-xs text-slate-500 space-y-1 max-w-xl">
        <p className="font-medium text-slate-400">After upload</p>
        <p>
          The bot can read the file via the{" "}
          <code className="font-mono">Read</code> tool or pass it to the lead
          via{" "}
          <code className="font-mono">tmux send-keys</code>. A manifest
          (name, size, sha256, timestamp) is written alongside each file.
        </p>
      </div>
    </div>
  );
}
