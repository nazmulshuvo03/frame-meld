import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  // Mirrors tsconfig's "@/*" paths entry. Vitest does not read tsconfig
  // paths on its own, so leaving this out breaks every "@/lib/..." import
  // with a module-not-found that looks like a missing package.
  resolve: { alias: { "@": path.resolve(import.meta.dirname, ".") } },
  test: {
    environment: "jsdom",
    setupFiles: ["./test/setup.ts"],
    globals: true,
  },
});
