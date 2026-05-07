/** @type {import('next').NextConfig} */
const backendOrigin = process.env.BACKEND_ORIGIN || "http://127.0.0.1:5000";

const nextConfig = {
  // Proxy /api/* to the backend so the frontend can call same-origin `/api/...`
  // in both dev and production (Vercel). Configure with BACKEND_ORIGIN.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendOrigin}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
