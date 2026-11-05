import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { readdirSync } from "fs";

/** As in vite.config.ts: every folder of Guide topics is a language the Guide ships. */
const GUIDE_LANGUAGE_FOLDERS = readdirSync("frontend_src/guide/topics", { withFileTypes: true })
  .filter((d) => d.isDirectory()).map((d) => d.name).sort();

export default defineConfig({
  plugins: [react()],
  define: { __GUIDE_LANGUAGES__: JSON.stringify(GUIDE_LANGUAGE_FOLDERS) },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./frontend_src/__tests__/setup.ts"],
    include: ["frontend_src/__tests__/**/*.test.{ts,tsx}"],
    // Report only: no threshold, so `--coverage` never fails a run.
    coverage: {
      provider: "v8",
      include: ["frontend_src/**/*.{ts,tsx}"],
      exclude: ["frontend_src/__tests__/**"],
      reporter: ["text-summary"],
    },
  },
});
