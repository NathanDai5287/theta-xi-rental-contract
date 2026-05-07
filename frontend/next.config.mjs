/** @type {import('next').NextConfig} */
const nextConfig = {
  // Proxy /api/* to the Flask backend during dev so CORS isn't a concern
  // and the same-origin pattern matches what production behind a reverse
  // proxy will look like.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:5000/api/:path*",
      },
    ];
  },
};

export default nextConfig;
