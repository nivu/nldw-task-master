import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Security headers for every response. Set here rather than in netlify.toml:
  // Netlify's [[headers]] reach only files its CDN serves, not the pages the
  // Next.js server function renders.
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          // The portal must not be framed by another site (clickjacking).
          // frame-ancestors is the modern form; X-Frame-Options covers older
          // browsers. Only this directive — a full CSP is a separate change.
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
          { key: "X-Content-Type-Options", value: "nosniff" },
        ],
      },
    ];
  },
};

export default nextConfig;
