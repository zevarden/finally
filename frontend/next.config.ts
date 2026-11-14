import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";

// Production: static export to out/, served by FastAPI with relative /api URLs.
// Dev: no export; proxy /api to the local backend (default http://localhost:8010).
export default function config(phase: string): NextConfig {
  if (phase === PHASE_DEVELOPMENT_SERVER) {
    const target = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8010";
    return {
      images: { unoptimized: true },
      async rewrites() {
        return [{ source: "/api/:path*", destination: `${target}/api/:path*` }];
      },
    };
  }
  return {
    output: "export",
    images: { unoptimized: true },
    trailingSlash: false,
  };
}
