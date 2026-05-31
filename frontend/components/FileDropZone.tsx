"use client";

import { useCallback, useRef, useState } from "react";

type UploadState =
  | { status: "idle" }
  | { status: "uploading" }
  | { status: "done"; name: string; size: number; sha256: string; path: string }
  | { status: "error"; message: string };

interface FileDropZoneProps {
  lead: string;
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function FileDropZone({ lead }: FileDropZoneProps) {
  const [state, setState] = useState<UploadState>({ status: "idle" });
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const upload = useCallback(
    async (file: File) => {
      setState({ status: "uploading" });
      const form = new FormData();
      form.append("file", file, file.name);
      try {
        const res = await fetch(
          `/api/admin/upload/${encodeURIComponent(lead)}`,
          { method: "POST", body: form },
        );
        const json = await res.json().catch(() => ({}));
        if (!res.ok) {
          setState({
            status: "error",
            message: json?.detail ?? `Server returned ${res.status}`,
          });
          return;
        }
        setState({
          status: "done",
          name: json.name,
          size: json.size,
          sha256: json.sha256,
          path: json.path,
        });
      } catch (e) {
        setState({
          status: "error",
          message: e instanceof Error ? e.message : String(e),
        });
      }
    },
    [lead],
  );

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(true);
  }, []);

  const onDragLeave = useCallback(() => setDragging(false), []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      const file = e.dataTransfer.files[0];
      if (file) upload(file);
    },
    [upload],
  );

  const onInput = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) upload(file);
      e.target.value = "";
    },
    [upload],
  );

  const isUploading = state.status === "uploading";

  return (
    <div className="space-y-4">
      <div
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        onClick={() => !isUploading && inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === " ") && !isUploading) {
            inputRef.current?.click();
          }
        }}
        aria-label="File drop zone — click or drag a file here to upload"
        className={[
          "relative flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed p-10 text-center transition-colors cursor-pointer select-none",
          dragging
            ? "border-indigo-400 bg-indigo-500/10"
            : isUploading
              ? "border-slate-600 bg-slate-900/30 cursor-not-allowed"
              : "border-slate-700 bg-slate-900/20 hover:border-indigo-500/60 hover:bg-indigo-500/5",
        ].join(" ")}
      >
        <input
          ref={inputRef}
          type="file"
          className="sr-only"
          onChange={onInput}
          disabled={isUploading}
        />

        {isUploading ? (
          <>
            <div className="size-10 rounded-full border-2 border-indigo-400 border-t-transparent animate-spin" />
            <p className="text-sm text-slate-400">Uploading — do not close this tab</p>
          </>
        ) : (
          <>
            <div className="flex size-12 items-center justify-center rounded-full bg-slate-800">
              <svg
                xmlns="http://www.w3.org/2000/svg"
                fill="none"
                viewBox="0 0 24 24"
                strokeWidth={1.5}
                stroke="currentColor"
                className="size-6 text-indigo-300"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"
                />
              </svg>
            </div>
            <div>
              <p className="text-sm font-medium text-slate-200">
                Drop a file here or click to browse
              </p>
              <p className="mt-1 text-xs text-slate-500">
                Large files (200 MB+) are streamed — no browser memory limit
              </p>
            </div>
          </>
        )}
      </div>

      {state.status === "done" && (
        <div className="rounded-lg border border-emerald-800/50 bg-emerald-950/20 p-4 space-y-1.5">
          <p className="text-sm font-medium text-emerald-300">Upload complete</p>
          <dl className="text-xs text-slate-400 space-y-1 font-mono">
            <div className="flex gap-2">
              <dt className="text-slate-500 shrink-0">name</dt>
              <dd className="break-all">{state.name}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-slate-500 shrink-0">size</dt>
              <dd>{formatBytes(state.size)}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-slate-500 shrink-0">sha256</dt>
              <dd className="break-all">{state.sha256}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-slate-500 shrink-0">path</dt>
              <dd className="break-all">{state.path}</dd>
            </div>
          </dl>
          <button
            type="button"
            onClick={() => setState({ status: "idle" })}
            className="mt-2 text-xs text-indigo-400 hover:text-indigo-200 underline"
          >
            Upload another file
          </button>
        </div>
      )}

      {state.status === "error" && (
        <div className="rounded-lg border border-rose-800/50 bg-rose-950/20 p-4 space-y-1.5">
          <p className="text-sm font-medium text-rose-300">Upload failed</p>
          <p className="text-xs text-slate-400 break-words">{state.message}</p>
          <button
            type="button"
            onClick={() => setState({ status: "idle" })}
            className="mt-1 text-xs text-indigo-400 hover:text-indigo-200 underline"
          >
            Try again
          </button>
        </div>
      )}
    </div>
  );
}
