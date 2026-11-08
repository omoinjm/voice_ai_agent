import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Self-contained server bundle for the ECS image (see Dockerfile).
  output: "standalone",
};

export default nextConfig;
