"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Play, Loader2, CheckCircle2, AlertTriangle } from "lucide-react";
import { runRoutine } from "@/lib/adminActions";
import { Button } from "@/components/ui/button";

type Status = { kind: "ok" | "err"; text: string } | null;

export function RunRoutineButton({
  name,
  kind,
}: {
  name: string;
  kind: string;
}) {
  const router = useRouter();
  const [status, setStatus] = useState<Status>(null);
  const [pending, start] = useTransition();

  // Local/system timers usually aren't runnable on demand (the backend may 502).
  // We still render the button so operators can try, but flag it as best-effort
  // and surface the error message rather than silently failing.
  const bestEffort = kind === "local";

  function onRun() {
    setStatus(null);
    start(async () => {
      const res = await runRoutine(name);
      if (res.ok) {
        setStatus({ kind: "ok", text: "Triggered." });
        router.refresh();
      } else {
        setStatus({ kind: "err", text: res.error });
      }
    });
  }

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <Button
        size="sm"
        variant="outline"
        onClick={onRun}
        disabled={pending}
        title={bestEffort ? "Local timer — run may not be supported" : "Run now"}
      >
        {pending ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}
        Run now
      </Button>
      {status && (
        <span
          className={`inline-flex items-center gap-1 text-xs ${status.kind === "ok" ? "text-emerald-400" : "text-rose-400"}`}
        >
          {status.kind === "ok" ? (
            <CheckCircle2 className="size-3.5 shrink-0" />
          ) : (
            <AlertTriangle className="size-3.5 shrink-0" />
          )}
          <span className="break-words">{status.text}</span>
        </span>
      )}
    </div>
  );
}
