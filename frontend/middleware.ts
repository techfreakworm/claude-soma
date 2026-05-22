import { auth } from "@/lib/auth";
import { NextResponse } from "next/server";

export default auth((req) => {
  const isAdmin = req.nextUrl.pathname.startsWith("/admin");
  if (!isAdmin) return NextResponse.next();

  const handle = (req.auth?.user as { githubHandle?: string } | undefined)
    ?.githubHandle;
  const allowed = (process.env.HERMES_ALLOWED_GITHUB_HANDLES || "techfreakworm")
    .split(",")
    .map((s) => s.trim());

  if (!handle || !allowed.includes(handle)) {
    const signin = new URL("/api/auth/signin", req.url);
    signin.searchParams.set("callbackUrl", req.nextUrl.pathname);
    return NextResponse.redirect(signin);
  }
  const res = NextResponse.next();
  res.headers.set("x-github-handle", handle);
  return res;
});

export const config = { matcher: ["/admin/:path*"] };
