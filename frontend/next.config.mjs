/** @type {import('next').NextConfig} */
const API_URL = process.env.API_URL || "http://localhost:8000"

const nextConfig = {
  output: "standalone",
  images: { unoptimized: true },
  // The browser only talks to this origin; Next proxies API calls to the FastAPI service.
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: `${API_URL}/api/v1/:path*` }]
  },
}

export default nextConfig
