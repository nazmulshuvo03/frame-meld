import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Traces exactly the files the server needs into .next/standalone, so the
  // runtime image carries neither the toolchain nor node_modules. `npm run
  // dev` and `npm start` are unaffected; this only changes what a build
  // leaves behind for the Dockerfile to copy.
  output: "standalone",
};

export default nextConfig;
