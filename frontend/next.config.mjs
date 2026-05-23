/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  // Pin Turbopack's workspace root to this frontend dir. Without this, Next.js
  // walks upward looking for the nearest lockfile and may pick an unrelated one
  // (e.g. ~/bun.lock on the dev machine), which nests .next/standalone under
  // the wrong path and breaks the systemd ExecStart line.
  turbopack: {
    root: import.meta.dirname,
  },
  // No rewrites: Caddy is the public router and handles /api/auth/* (Next-auth)
  // vs /api/* (FastAPI) at the edge. Lib/api.ts uses an absolute API_BASE for
  // server-side fetches. An /api/:path* rewrite here would intercept Next-auth's
  // own /api/auth/* routes and forward them to FastAPI, which 404s on them.
};
export default nextConfig;
