"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import {
  Send,
  Trash2,
  Loader2,
  CheckCircle2,
  AlertTriangle,
  ExternalLink,
} from "lucide-react";
import { sendToProject, killProject } from "@/lib/adminActions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogClose,
} from "@/components/ui/dialog";

type TeamMember = { handle: string; role: string; status: string };
type Project = {
  name: string;
  agent_id: string;
  type: string;
  rc_url: string;
  status: string;
  idle_for_seconds: number;
  team?: TeamMember[];
};

type Status = { kind: "ok" | "err"; text: string } | null;

const STATUS_BADGE: Record<string, string> = {
  active: "bg-emerald-900/40 text-emerald-300",
  idle: "bg-amber-900/40 text-amber-300",
  error: "bg-rose-900/40 text-rose-300",
  killed: "bg-slate-800 text-slate-400",
};

function fmtIdle(s: number): string {
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${Math.round(s / 3600)}h`;
}

function ProjectRow({ project }: { project: Project }) {
  const router = useRouter();
  const [message, setMessage] = useState("");
  const [status, setStatus] = useState<Status>(null);
  const [killOpen, setKillOpen] = useState(false);
  const [sending, startSend] = useTransition();
  const [killing, startKill] = useTransition();

  function onSend() {
    setStatus(null);
    startSend(async () => {
      const res = await sendToProject(project.name, message);
      if (res.ok) {
        setStatus({ kind: "ok", text: "Message sent to lead." });
        setMessage("");
        router.refresh();
      } else {
        setStatus({ kind: "err", text: res.error });
      }
    });
  }

  function onKill() {
    setStatus(null);
    startKill(async () => {
      const res = await killProject(project.name);
      setKillOpen(false);
      if (res.ok) {
        setStatus({ kind: "ok", text: "Lead killed / archived." });
        router.refresh();
      } else {
        setStatus({ kind: "err", text: res.error });
      }
    });
  }

  const team = project.team ?? [];

  return (
    <div className="p-4 space-y-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="font-mono text-sm text-slate-100 break-all">{project.name}</span>
        <span
          className={`text-[10px] px-1.5 py-0.5 rounded ${STATUS_BADGE[project.status] || "bg-slate-800 text-slate-400"}`}
        >
          ● {project.status}
        </span>
        <span className="text-xs text-slate-500">{project.type}</span>
        <span className="text-xs text-slate-600">idle {fmtIdle(project.idle_for_seconds)}</span>
        {project.rc_url && (
          <a
            href={project.rc_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-xs underline text-indigo-300 hover:text-indigo-200"
          >
            attach <ExternalLink className="size-3" />
          </a>
        )}
      </div>

      {team.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[10px] uppercase tracking-wider text-slate-600">team</span>
          {team.map((tm) => (
            <Badge
              key={tm.handle}
              variant="outline"
              className={
                tm.status === "dead"
                  ? "border-slate-700 text-slate-500"
                  : "border-emerald-700/60 text-emerald-300"
              }
              title={tm.role}
            >
              {tm.handle}
              <span className="text-slate-500">· {tm.role}</span>
            </Badge>
          ))}
        </div>
      )}

      <div className="flex flex-col sm:flex-row gap-2">
        <Input
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && message.trim() && !sending) onSend();
          }}
          placeholder={`Message ${project.name} lead…`}
          disabled={sending}
          className="flex-1"
        />
        <div className="flex gap-2">
          <Button
            onClick={onSend}
            disabled={sending || !message.trim()}
            className="flex-1 sm:flex-none"
          >
            {sending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
            Send
          </Button>
          <Button
            variant="destructive"
            onClick={() => setKillOpen(true)}
            disabled={killing}
            className="flex-1 sm:flex-none"
          >
            {killing ? <Loader2 className="size-4 animate-spin" /> : <Trash2 className="size-4" />}
            Kill
          </Button>
        </div>
      </div>

      {status && (
        <div
          className={`flex items-start gap-2 text-xs ${status.kind === "ok" ? "text-emerald-400" : "text-rose-400"}`}
        >
          {status.kind === "ok" ? (
            <CheckCircle2 className="size-4 shrink-0 mt-0.5" />
          ) : (
            <AlertTriangle className="size-4 shrink-0 mt-0.5" />
          )}
          <span className="break-words">{status.text}</span>
        </div>
      )}

      <Dialog open={killOpen} onOpenChange={setKillOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Kill {project.name}?</DialogTitle>
            <DialogDescription>
              This archives and kills the project-lead. Its running work stops.
              This cannot be undone from here.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline" disabled={killing}>
                Cancel
              </Button>
            </DialogClose>
            <Button variant="destructive" onClick={onKill} disabled={killing}>
              {killing && <Loader2 className="size-4 animate-spin" />}
              Yes, kill lead
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export function ProjectActions({ projects }: { projects: Project[] }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40">
      <div className="px-4 py-3 text-xs uppercase tracking-wider text-slate-500 border-b border-slate-800">
        Project controls
      </div>
      <div className="divide-y divide-slate-800">
        {projects.map((p) => (
          <ProjectRow key={p.name} project={p} />
        ))}
      </div>
    </div>
  );
}
