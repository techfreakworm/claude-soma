"use server";

import { auth } from "@/lib/auth";

const API_BASE = process.env.HERMES_API_BASE || "http://127.0.0.1:9000";

export type ActionResult = { ok: true; data?: unknown } | { ok: false; error: string };

/**
 * Browser-triggered mutations cannot hit FastAPI :9000 directly — Caddy strips
 * the X-GitHub-Handle header and the browser has no session there. So every
 * mutation is a Server Action that resolves the GitHub handle from the
 * NextAuth session (session.user.githubHandle, set in lib/auth.ts) and injects
 * the header server-side, mirroring the read path in lib/api.ts.
 */
async function mutate(path: string, body?: unknown): Promise<ActionResult> {
  const session = await auth();
  const handle = (session?.user as { githubHandle?: string } | undefined)
    ?.githubHandle;
  if (!handle) return { ok: false, error: "unauthorized" };
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-GitHub-Handle": handle,
      },
      body: body ? JSON.stringify(body) : undefined,
      cache: "no-store",
    });
    if (!res.ok) {
      const text = (await res.text().catch(() => "")).slice(0, 200);
      return { ok: false, error: `${res.status} ${text || res.statusText}` };
    }
    const data = await res.json().catch(() => undefined);
    return { ok: true, data };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

export async function runRoutine(name: string): Promise<ActionResult> {
  return mutate(`/api/routines/${encodeURIComponent(name)}/run`);
}

export async function sendToProject(
  name: string,
  message: string,
): Promise<ActionResult> {
  const trimmed = message.trim();
  if (!trimmed) return { ok: false, error: "message is empty" };
  return mutate(`/api/projects/${encodeURIComponent(name)}/message`, {
    message: trimmed,
  });
}

export async function killProject(name: string): Promise<ActionResult> {
  return mutate(`/api/projects/${encodeURIComponent(name)}/kill`);
}

export async function broadcast(message: string): Promise<ActionResult> {
  const trimmed = message.trim();
  if (!trimmed) return { ok: false, error: "message is empty" };
  return mutate(`/api/admin/broadcast`, { message: trimmed });
}

export async function pauseAll(): Promise<ActionResult> {
  return mutate(`/api/admin/pause-all`);
}
