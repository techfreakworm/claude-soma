import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { api } from "@/lib/api";

// Transcript items come from Claude Code JSONL and are loosely typed; render
// defensively. Common shapes: {role, content}, {type, message:{role,content}},
// where content is a string or an array of content blocks ({type,text} etc.).
type AnyItem = Record<string, unknown>;

function asString(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v, null, 2);
}

function extractRole(item: AnyItem): string {
  const msg = (item.message as AnyItem | undefined) ?? item;
  const role = msg.role ?? item.role ?? item.type ?? "message";
  return asString(role);
}

function extractTimestamp(item: AnyItem): string | null {
  const ts = item.timestamp ?? item.ts ?? item.created_at ?? item.time;
  if (ts == null) return null;
  if (typeof ts === "number") {
    const ms = ts > 1e12 ? ts : ts * 1000;
    return new Date(ms).toLocaleString();
  }
  const d = new Date(asString(ts));
  return isNaN(d.getTime()) ? asString(ts) : d.toLocaleString();
}

function extractText(item: AnyItem): string {
  const msg = (item.message as AnyItem | undefined) ?? item;
  const content = msg.content ?? item.content ?? item.text;
  if (content == null) {
    // Nothing obvious — show the raw item so operators can still inspect it.
    return asString(item);
  }
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((block) => {
        if (typeof block === "string") return block;
        const b = block as AnyItem;
        if (typeof b.text === "string") return b.text;
        const t = asString(b.type);
        if (t === "tool_use") return `[tool_use: ${asString(b.name)}]\n${asString(b.input)}`;
        if (t === "tool_result") return `[tool_result]\n${asString(b.content)}`;
        return asString(block);
      })
      .filter(Boolean)
      .join("\n\n");
  }
  return asString(content);
}

const ROLE_STYLE: Record<string, string> = {
  user: "border-l-indigo-500",
  assistant: "border-l-emerald-500",
  system: "border-l-slate-600",
  tool: "border-l-amber-500",
};

const ROLE_LABEL: Record<string, string> = {
  user: "text-indigo-300",
  assistant: "text-emerald-300",
  system: "text-slate-400",
  tool: "text-amber-300",
};

export default async function TranscriptPage({
  params,
  searchParams,
}: {
  params: Promise<{ thread: string }>;
  searchParams: Promise<{ project?: string }>;
}) {
  const { thread } = await params;
  const { project } = await searchParams;
  const qs = project ? `?project=${encodeURIComponent(project)}` : "";

  let items: AnyItem[] = [];
  let error: string | null = null;
  try {
    const data = await api<unknown>(
      `/api/conversations/${encodeURIComponent(thread)}${qs}`,
    );
    items = Array.isArray(data) ? (data as AnyItem[]) : [];
  } catch (e) {
    error = e instanceof Error ? e.message : "failed to load transcript";
  }

  return (
    <div className="p-4 sm:p-6 lg:p-8 space-y-6">
      <div className="space-y-2">
        <Link
          href="/admin/conversations"
          className="inline-flex items-center gap-1.5 text-sm text-slate-400 hover:text-slate-200"
        >
          <ArrowLeft className="size-4" /> Back to conversations
        </Link>
        <h1 className="text-xl sm:text-2xl font-bold font-mono break-all">{thread}</h1>
        {project && (
          <div className="text-xs text-slate-500">project: {project}</div>
        )}
      </div>

      {error ? (
        <div className="rounded-lg border border-rose-900/50 bg-rose-950/20 p-6 text-sm text-rose-300">
          Could not load transcript: {error}
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-700 p-8 text-slate-500 text-sm">
          This transcript has no messages.
        </div>
      ) : (
        <div className="space-y-3">
          {items.map((item, i) => {
            const role = extractRole(item);
            const ts = extractTimestamp(item);
            const text = extractText(item);
            const key = role.toLowerCase();
            return (
              <div
                key={i}
                className={`rounded-lg border border-slate-800 border-l-2 bg-slate-900/40 p-4 ${ROLE_STYLE[key] || "border-l-slate-700"}`}
              >
                <div className="flex items-center justify-between gap-2 mb-2">
                  <span
                    className={`text-xs font-medium uppercase tracking-wider ${ROLE_LABEL[key] || "text-slate-400"}`}
                  >
                    {role}
                  </span>
                  {ts && <span className="text-[11px] text-slate-600 font-mono">{ts}</span>}
                </div>
                <pre className="text-sm text-slate-300 whitespace-pre-wrap break-words font-sans">
                  {text || "(empty)"}
                </pre>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
