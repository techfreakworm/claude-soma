/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  rewrites: async () => [
    {
      source: "/api/:path*",
      destination: "http://127.0.0.1:9000/api/:path*",
    },
  ],
};
export default nextConfig;
