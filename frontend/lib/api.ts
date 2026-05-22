import { headers } from "next/headers";

const API_BASE = process.env.HERMES_API_BASE || "http://127.0.0.1:9000";

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const h = await headers();
  const githubHandle = h.get("x-github-handle") || "";

  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-GitHub-Handle": githubHandle,
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`api ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

export async function publicApi<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    next: { revalidate: 30 },
  });
  if (!res.ok) throw new Error(`public api ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}
