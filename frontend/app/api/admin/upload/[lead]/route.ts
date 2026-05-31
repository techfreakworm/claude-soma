import { auth } from "@/lib/auth";
import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const API_BASE = process.env.HERMES_API_BASE || "http://127.0.0.1:9000";

const ALLOWED = (process.env.HERMES_ALLOWED_GITHUB_HANDLES || "techfreakworm")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ lead: string }> },
): Promise<NextResponse> {
  const session = await auth();
  const handle = (session?.user as { githubHandle?: string } | undefined)
    ?.githubHandle;

  if (!handle || !ALLOWED.includes(handle)) {
    return NextResponse.json({ detail: "not authorized" }, { status: 403 });
  }

  const { lead } = await params;
  const contentType = req.headers.get("content-type") ?? "";

  const upstream = await fetch(
    `${API_BASE}/api/admin/upload/${encodeURIComponent(lead)}`,
    {
      method: "POST",
      headers: {
        "content-type": contentType,
        "x-github-handle": handle,
      },
      // @ts-expect-error — duplex is required for streaming bodies in Node 18+
      // but not yet reflected in the TypeScript lib types
      duplex: "half",
      body: req.body,
    },
  );

  const data = await upstream.json().catch(() => ({}));
  return NextResponse.json(data, { status: upstream.status });
}
