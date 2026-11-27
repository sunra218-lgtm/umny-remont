import type { NextConfig } from "next";

const backend = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  // Браузер ходит в API через тот же адрес, что и сайт: cookie сессии остаются first-party.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
