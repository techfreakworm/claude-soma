"use client";

import { useState, useCallback } from "react";

const CHUNK = 80 * 1024;

interface LogViewerProps {
  lead: string;
  initialLines: string[];
  initialStartByte: number;
  totalBytes: number;
  initialHasMore: boolean;
}

export function LogViewer({
  lead,
  initialLines,
  initialStartByte,
  totalBytes,
  initialHasMore,
}: LogViewerProps) {
  const [lines, setLines] = useState<string[]>(initialLines);
  const [startByte, setStartByte] = useState(initialStartByte);
  const [hasMore, setHasMore] = useState(initialHasMore);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadOlder = useCallback(async () => {
    const olderOffset = Math.max(0, startByte - CHUNK);
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/admin/logs/${encodeURIComponent(lead)}?offset=${olderOffset}&limit=1000`,
      );
      if (!res.ok) {
        setError(`Server returned ${res.status}`);
        return;
      }
      const body = await res.json();
      setLines((prev) => [...(body.lines as string[]), ...prev]);
      setStartByte(body.start_byte as number);
      setHasMore((body.start_byte as number) > 0);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [lead, startByte]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500 font-mono">
        <span>
          {lines.length} line{lines.length !== 1 ? "s" : ""} shown
          {totalBytes > 0 && (
            <span className="ml-2">
              · {(totalBytes / 1024).toFixed(1)} KB total
            </span>
          )}
        </span>
        {startByte > 0 && (
          <span>byte offset {startByte.toLocaleString()}</span>
        )}
      </div>

      {hasMore && (
        <div className="flex justify-center">
          <button
            type="button"
            onClick={loadOlder}
            disabled={loading}
            className="px-4 py-1.5 rounded text-xs font-mono border border-slate-700 text-slate-400 hover:bg-slate-800/60 hover:text-slate-200 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {loading ? "Loading..." : "Load older lines"}
          </button>
        </div>
      )}

      {error && (
        <div className="rounded border border-rose-800/50 bg-rose-950/20 px-4 py-2 text-xs text-rose-300 font-mono">
          {error}
        </div>
      )}

      <div className="rounded-lg border border-slate-800 bg-slate-950 overflow-auto max-h-[72vh]">
        {lines.length === 0 ? (
          <p className="p-8 text-slate-500 text-sm font-mono text-center">
            No log output yet.
          </p>
        ) : (
          <pre className="p-4 text-xs font-mono text-slate-300 leading-5 whitespace-pre-wrap break-all">
            {lines.join("\n")}
          </pre>
        )}
      </div>
    </div>
  );
}
