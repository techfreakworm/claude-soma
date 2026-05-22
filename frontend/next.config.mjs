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
  rewrites: async () => [
    {
      source: "/api/:path*",
      destination: "http://127.0.0.1:9000/api/:path*",
    },
  ],
};
export default nextConfig;
