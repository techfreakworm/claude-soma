"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Megaphone, Power, Loader2, CheckCircle2, AlertTriangle } from "lucide-react";
import { broadcast, pauseAll } from "@/lib/adminActions";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogClose,
} from "@/components/ui/dialog";

type Status = { kind: "ok" | "err"; text: string } | null;

function StatusLine({ status }: { status: Status }) {
  if (!status) return null;
  const ok = status.kind === "ok";
  return (
    <div
      className={`flex items-start gap-2 text-xs ${ok ? "text-emerald-400" : "text-rose-400"}`}
    >
      {ok ? (
        <CheckCircle2 className="size-4 shrink-0 mt-0.5" />
      ) : (
        <AlertTriangle className="size-4 shrink-0 mt-0.5" />
      )}
      <span className="break-words">{status.text}</span>
    </div>
  );
}

export function OperatorActions() {
  const router = useRouter();
  const [message, setMessage] = useState("");
  const [bStatus, setBStatus] = useState<Status>(null);
  const [pStatus, setPStatus] = useState<Status>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [pendingB, startB] = useTransition();
  const [pendingP, startP] = useTransition();

  function onBroadcast() {
    setBStatus(null);
    startB(async () => {
      const res = await broadcast(message);
      if (res.ok) {
        setBStatus({ kind: "ok", text: "Broadcast queued to the Telegram channel." });
        setMessage("");
        router.refresh();
      } else {
        setBStatus({ kind: "err", text: res.error });
      }
    });
  }

  function onPauseAll() {
    setPStatus(null);
    startP(async () => {
      const res = await pauseAll();
      setConfirmOpen(false);
      if (res.ok) {
        setPStatus({ kind: "ok", text: "Paused all active project-leads." });
        router.refresh();
      } else {
        setPStatus({ kind: "err", text: res.error });
      }
    });
  }

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-4 sm:p-6 space-y-5">
      <div className="text-xs uppercase tracking-wider text-slate-500">
        Operator actions
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        {/* Broadcast */}
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-sm font-medium text-slate-200">
            <Megaphone className="size-4 text-indigo-300" /> Broadcast to channel
          </div>
          <Textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder="Message to queue to the Telegram channel…"
            rows={3}
            disabled={pendingB}
          />
          <div className="flex items-center gap-3">
            <Button
              onClick={onBroadcast}
              disabled={pendingB || !message.trim()}
              variant="default"
            >
              {pendingB ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Megaphone className="size-4" />
              )}
              Send broadcast
            </Button>
          </div>
          <StatusLine status={bStatus} />
        </div>

        {/* Pause all */}
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-sm font-medium text-slate-200">
            <Power className="size-4 text-rose-300" /> Emergency stop
          </div>
          <p className="text-xs text-slate-500">
            Kills every active project-lead. Running work is archived. Use when
            something is misbehaving.
          </p>
          <Button
            variant="destructive"
            onClick={() => setConfirmOpen(true)}
            disabled={pendingP}
          >
            {pendingP ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Power className="size-4" />
            )}
            Pause all project-leads
          </Button>
          <StatusLine status={pStatus} />
        </div>
      </div>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Pause all project-leads?</DialogTitle>
            <DialogDescription>
              This kills every active lead immediately. Their sessions are
              archived. This cannot be undone from here.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline" disabled={pendingP}>
                Cancel
              </Button>
            </DialogClose>
            <Button variant="destructive" onClick={onPauseAll} disabled={pendingP}>
              {pendingP && <Loader2 className="size-4 animate-spin" />}
              Yes, pause all
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
